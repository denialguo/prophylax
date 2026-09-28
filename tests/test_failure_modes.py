"""M3: timeouts, typed errors, input bounds. No real engine, no LLM."""
import json
import os
import stat
import sys
import time

import pytest

from mcp_server.server import (
    handle_line, INVALID_PARAMS, PARSE_ERROR, ENGINE_TIMEOUT, ENGINE_CRASHED,
)

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_uci_engine.py")


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    """Executable wrapper so STOCKFISH_PATH can point at the fake engine."""
    exe = tmp_path / "fake_stockfish"
    exe.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("STOCKFISH_PATH", str(exe))
    monkeypatch.setenv("STOCKFISH_NODES", "1000")
    return monkeypatch


def call(tool, args):
    req = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    return handle_line(json.dumps(req))


FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
# 600 legal plies of knight shuffling: over the ply cap
LONG_GAME = " ".join(
    f"{i}. Nf3 Nf6" if i % 2 else f"{i}. Ng1 Ng8" for i in range(1, 301)
)


def test_hung_engine_returns_typed_timeout(fake_engine):
    fake_engine.setenv("FAKE_ENGINE_MODE", "hang")
    fake_engine.setenv("STOCKFISH_SEARCH_TIMEOUT_S", "1")
    start = time.monotonic()
    res = call("analyze_position", {"fen": FEN, "multipv": 1})
    assert time.monotonic() - start < 10
    assert res["id"] == 7
    assert res["error"]["code"] == ENGINE_TIMEOUT


def test_hang_inside_streaming_analysis_is_typed_timeout(fake_engine):
    # analyze_pgn's per-move searches use engine.analysis(); a kill there must
    # raise, never end the iterator quietly with partial data
    fake_engine.setenv("FAKE_ENGINE_MODE", "hang_after_first")
    fake_engine.setenv("STOCKFISH_SEARCH_TIMEOUT_S", "1")
    res = call("analyze_pgn", {"pgn": "1. e4 e5 2. Nf3 Nc6 *"})
    assert "result" not in res
    assert res["error"]["code"] == ENGINE_TIMEOUT


def test_crashing_engine_returns_typed_crash(fake_engine):
    fake_engine.setenv("FAKE_ENGINE_MODE", "crash")
    res = call("analyze_position", {"fen": FEN, "multipv": 1})
    assert res["error"]["code"] == ENGINE_CRASHED


@pytest.mark.parametrize("tool,args", [
    ("analyze_pgn", {"pgn": "1. e4 e5", "max_flags": 0}),
    ("analyze_pgn", {"pgn": "1. e4 e5", "max_flags": "4"}),
    ("analyze_pgn", {"pgn": "1. e4 e5 " * 20000}),
    ("analyze_pgn", {"pgn": LONG_GAME}),
    ("analyze_position", {"fen": FEN, "multipv": 50}),
    ("analyze_position", {"fen": "8/8/8/8/8/8/8/8 w - - 0 1"}),
    ("analyze_position", {"fen": "not a fen"}),
])
def test_bad_input_is_invalid_params(fake_engine, tool, args):
    res = call(tool, args)
    assert res["error"]["code"] == INVALID_PARAMS, res


def test_existing_callers_max_flags_still_valid():
    # goldens, verify_goldens.py and generate_inputs.py request max_flags=100
    from mcp_server.server import _bounded_int
    from config.settings import MAX_FLAGS
    assert _bounded_int({"max_flags": 100}, "max_flags", 4, MAX_FLAGS) == 100


def test_malformed_line_gets_parse_error():
    res = handle_line("{not json")
    assert res["error"]["code"] == PARSE_ERROR
    assert res["id"] is None


def test_version_pin_is_exact(monkeypatch):
    from config import verify_engine
    monkeypatch.setenv("STOCKFISH_PATH", "dummy")
    with pytest.raises(RuntimeError, match="mismatch"):
        verify_engine(mock_output="Stockfish dev-20250118-abc by the Stockfish developers\n")
    with pytest.raises(RuntimeError, match="mismatch"):
        verify_engine(mock_output="Stockfish 18.1 by the Stockfish developers\n")
    assert "Stockfish 18" in verify_engine(mock_output="Stockfish 18 by the Stockfish developers\n")


def test_unknown_tool_is_invalid_params():
    res = call("rm_rf", {})
    assert res["error"]["code"] == INVALID_PARAMS
