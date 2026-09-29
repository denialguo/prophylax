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


def test_session_movetext_keeps_the_start_position():
    # The session stores sanitized movetext; deep dives replay it
    from hooks.sanitize_pgn import sanitized_movetext
    game = chess.pgn.read_game(io.StringIO(sanitized_movetext(PGN)))
    assert game.board().fen() == FEN
    assert list(game.mainline_moves()) == list(GAME.mainline_moves())


@pytest.mark.anyio
async def test_deep_dive_on_a_fen_game_analyses_the_right_position():
    from unittest.mock import patch
    from google.genai import types
    from app.agent import CoachingAgent
    from hooks.sanitize_pgn import sanitized_movetext

    class Ctx:
        class session:
            state = {"pgn_text": sanitized_movetext(PGN), "config": {},
                     "move_evals": [{"move_san": "Kd2", "move_number": 31, "side": "white",
                                     "phase": "endgame", "wdl_delta": 0.0, "best_move_san": "Kd2"}]}
            events = [type("E", (), {"content": types.Content(role="user", parts=[types.Part.from_text(text="31.Kd2")])})()]

    seen = {}
    async def mcp(tool, args):
        seen[tool] = args
        return {"multipv_lines": [{"pv": ["Kd2"], "wdl": None}], "features": {}}

    async def narrate(agent, prompt):
        return "The king heads for the pawn. It keeps the opposition."

    coach = CoachingAgent(name="t")
    with patch("app.agent.call_mcp_tool_subprocess", new=mcp), patch.object(coach, "_run_sub_agent", new=narrate):
        events = [e async for e in coach._run_async_impl(Ctx())]
    board = chess.Board(FEN)
    board.push_san("Kd5")
    assert seen["analyze_position"]["fen"] == board.fen()
    assert "31.Kd2" in events[-1].content.parts[0].text
