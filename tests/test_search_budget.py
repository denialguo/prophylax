"""M4: one search per position, pinned Limit/Hash actually sent, whole-request cache."""
import json
import os
import stat
import sys

import pytest

from mcp_server.server import handle_line, search_game, search_position

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_uci_engine.py")
PGN = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 *"  # 6 plies -> 7 positions
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@pytest.fixture
def engine_log(tmp_path, monkeypatch):
    exe = tmp_path / "fake_stockfish"
    exe.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "uci.log"
    monkeypatch.setenv("STOCKFISH_PATH", str(exe))
    monkeypatch.setenv("STOCKFISH_NODES", "1234")
    monkeypatch.setenv("FAKE_ENGINE_MODE", "answer")
    monkeypatch.setenv("FAKE_ENGINE_LOG", str(log))
    search_game.cache_clear()
    search_position.cache_clear()
    return lambda: log.read_text().splitlines() if log.exists() else []


def call(tool, args):
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    res = handle_line(json.dumps(req))
    assert "result" in res, res
    return json.loads(res["result"]["content"][0]["text"])


def gos(lines):
    return [l for l in lines if l.startswith("go")]


def test_one_search_per_position_with_pinned_limit(engine_log):
    res = call("analyze_pgn", {"pgn": PGN, "max_flags": 10})
    sent = engine_log()
    assert gos(sent) == ["go nodes 1234"] * 7  # N+1, not 2N+1
    assert "setoption name Hash value 16" in sent
    assert "setoption name Threads value 1" in sent
    # best move comes from the pass-1 search: fake engine plays the first legal move
    assert all(e["best_move_san"] for e in res["move_evals"])


def test_repeat_is_cache_hit_even_with_other_max_flags(engine_log):
    call("analyze_pgn", {"pgn": PGN, "max_flags": 10})
    call("analyze_pgn", {"pgn": PGN, "max_flags": 3})
    call("analyze_position", {"fen": FEN, "multipv": 2})
    call("analyze_position", {"fen": FEN, "multipv": 2})
    assert len(gos(engine_log())) == 8


def test_changed_node_limit_is_a_miss(engine_log, monkeypatch):
    call("analyze_pgn", {"pgn": PGN})
    monkeypatch.setenv("STOCKFISH_NODES", "999")
    call("analyze_pgn", {"pgn": PGN})
    assert gos(engine_log()) == ["go nodes 1234"] * 7 + ["go nodes 999"] * 7


def test_errors_are_not_cached(engine_log, monkeypatch):
    monkeypatch.setenv("FAKE_ENGINE_MODE", "crash")
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "analyze_position", "arguments": {"fen": FEN}}}
    assert "error" in handle_line(json.dumps(req))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "answer")
    call("analyze_position", {"fen": FEN})


def test_one_engine_per_process_ucinewgame_per_request(engine_log):
    call("analyze_pgn", {"pgn": PGN})
    call("analyze_position", {"fen": FEN, "multipv": 2})
    sent = engine_log()
    assert sent.count("uci") == 1           # engine opened once
    assert sent.count("ucinewgame") == 2    # cleared hash per request
