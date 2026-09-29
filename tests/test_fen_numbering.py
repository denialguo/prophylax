"""M11.0: games that start from a FEN keep the FEN's move numbers everywhere."""
import io
import json
import os
import shutil

import chess
import chess.pgn
import pytest

from scripts.move_reference import ply_of
from evals.validate_narration import check_narration_moves_legality

# Black to move at move 30
FEN = "8/8/8/4k3/8/8/4P3/4K3 b - - 0 30"
PGN = f'[SetUp "1"]\n[FEN "{FEN}"]\n\n30... Kd5 31. Kd2 Ke4 32. Kc3 *\n'
GAME = chess.pgn.read_game(io.StringIO(PGN))
HAS_ENGINE = bool(os.environ.get("STOCKFISH_PATH") and shutil.which(os.environ["STOCKFISH_PATH"]))


@pytest.mark.parametrize("fen,move_number,side,ply", [
    (chess.STARTING_FEN, 1, "white", 0),
    (chess.STARTING_FEN, 13, "black", 25),
    (FEN, 30, "black", 0),
    (FEN, 31, "white", 1),
    (FEN, 32, "white", 3),
    (FEN, 30, "white", -1),  # before the game started; callers range-check
])
def test_ply_of(fen, move_number, side, ply):
    assert ply_of(chess.Board(fen), move_number, side) == ply


def test_validator_uses_fen_move_numbers():
    assert check_narration_moves_legality("after 30...Kd5 31.Kd2 Ke4 the king leads", GAME) is None
    assert check_narration_moves_legality("after 32.Kc3 White holds", GAME) is None
    assert check_narration_moves_legality("after 31.Ke4 White wins", GAME) == "31.Ke4"


@pytest.mark.skipif(not HAS_ENGINE, reason="STOCKFISH_PATH not configured")
def test_server_numbers_moves_from_the_fen(monkeypatch):
    from mcp_server.server import handle_line
    monkeypatch.setenv("STOCKFISH_NODES", "5000")
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "analyze_pgn", "arguments": {"pgn": PGN, "max_flags": 10}}}
    res = handle_line(json.dumps(req))
    out = json.loads(res["result"]["content"][0]["text"])
    assert [(e["move_number"], e["side"], e["move_san"]) for e in out["move_evals"]] == [
        (30, "black", "Kd5"), (31, "white", "Kd2"), (31, "black", "Ke4"), (32, "white", "Kc3")]
