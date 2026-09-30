"""M15: one derivation path. The server's post-search code, split out as
derive_game_payload, reproduces the pre-split analyze_pgn output exactly, both from
stored searches (fast, offline) and live at 1M nodes (golden).

The recordings in tests/fixtures/derive_parity/ were made from the unchanged server
(commit 8647650) before the split."""
import glob
import json
import os

import chess.pgn
import io
import pytest

from mcp_server.server import derive_game_payload, handle_analyze_pgn, searches_from_json, searches_to_json

RECORDINGS = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "fixtures", "derive_parity", "*.json")))


def canonical(payload):
    """Pre-existing, not from the split: get_quiet_concessions builds its square lists from
    sets, so their order follows the process's string hash seed (PYTHONHASHSEED=3 gives
    a6,c5 for 10...b5; 1,2,4 give c5,a6). Compare those lists as sets; everything else exactly."""
    out = json.loads(json.dumps(payload))
    for f in out["flags"]:
        f["concessions"] = {k: sorted(v) for k, v in f["concessions"].items()}
    return out


def _load(path):
    rec = json.load(open(path))
    game = chess.pgn.read_game(io.StringIO(rec["pgn"]))
    return rec, game, list(game.mainline_moves())


@pytest.mark.parametrize("path", RECORDINGS, ids=[os.path.basename(p)[:-5] for p in RECORDINGS])
def test_stored_searches_rederive_the_recorded_payload(path):
    rec, game, moves = _load(path)
    searches = searches_from_json(game.board(), moves, rec["searches"])
    assert canonical(derive_game_payload(game, moves, searches, 400)) == canonical(rec["payload"])


@pytest.mark.parametrize("path", RECORDINGS, ids=[os.path.basename(p)[:-5] for p in RECORDINGS])
def test_searches_round_trip(path):
    rec, game, moves = _load(path)
    assert searches_to_json(searches_from_json(game.board(), moves, rec["searches"])) == rec["searches"]


@pytest.mark.golden
@pytest.mark.parametrize("path", [p for p in RECORDINGS if p.endswith("_1000000.json")],
                         ids=lambda p: os.path.basename(p)[:-5])
def test_live_payload_matches_pre_split_recording(path, monkeypatch):
    """The live server at the pinned 1M budget still returns what it returned before
    the split; only analysis_config (and searches, when asked) are added."""
    rec, game, moves = _load(path)
    monkeypatch.setenv("STOCKFISH_NODES", "1000000")
    out = handle_analyze_pgn({"pgn": rec["pgn"], "max_flags": 400, "include_searches": True})
    config, searches = out.pop("analysis_config"), out.pop("searches")
    assert canonical(out) == canonical(rec["payload"])
    assert searches == rec["searches"]
    assert config["engine"].startswith("Stockfish 18") and config["nodes"] == 1_000_000
    assert (config["threads"], config["hash_mb"]) == (1, 16)
