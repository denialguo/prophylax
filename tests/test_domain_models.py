"""M11: the MCP payload converts into the canonical domain models, round-trips back to
the exact flag dicts (so prompts don't change), and malformed payloads fail loudly."""
import copy
import io
import json
import os
import shutil

import chess
import chess.pgn
import pytest
from pydantic import ValidationError

from domain.convert import (PayloadError, game_analysis_from_payload, position_analysis_from_payload,
                            to_flag_dict)
from domain.models import MoveAnalysis
from scripts.format_narration import format_flag_for_llm
from tests import test_agent_structure, test_grounding
from tests.payload_schema import pgn_payload as payload_for

HAS_ENGINE = bool(os.environ.get("STOCKFISH_PATH") and shutil.which(os.environ["STOCKFISH_PATH"]))
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


MOCKS = [(test_grounding.PGN, [test_grounding.FLAG]),
         (test_agent_structure.PGN, test_agent_structure.FLAGS)]


@pytest.mark.parametrize("pgn,flags", MOCKS)
def test_mock_payloads_round_trip_to_identical_prompts(pgn, flags):
    analysis = game_analysis_from_payload(payload_for(pgn, flags), pgn)
    flagged = analysis.flagged()
    assert [to_flag_dict(m) for m in flagged] == flags  # server rank order kept
    for m, original in zip(flagged, flags):
        assert format_flag_for_llm(to_flag_dict(m), 1, 1800) == format_flag_for_llm(original, 1, 1800)


def test_moves_carry_ply_fen_and_numbering():
    pgn = test_grounding.PGN
    analysis = game_analysis_from_payload(payload_for(pgn, [test_grounding.FLAG]), pgn)
    assert len(analysis.moves) == 6
    nf3 = analysis.moves[2]
    assert (nf3.ply, nf3.move_number, nf3.side, nf3.san) == (2, 2, "white", "Nf3")
    board = chess.Board()
    board.push_san("e4"); board.push_san("e5")
    assert nf3.fen_before == board.fen()
    assert nf3.detail.pv.start_ply == 2 and nf3.detail.refutation.start_ply == 3
    assert analysis.moves[0].detail is None
    assert analysis.game_id == game_analysis_from_payload(payload_for(pgn, []), pgn).game_id


def test_fen_game_converts_with_its_own_numbering():
    from tests.test_fen_numbering import PGN
    analysis = game_analysis_from_payload(payload_for(PGN, []), PGN)
    assert [(m.ply, m.move_number, m.side) for m in analysis.moves] == [
        (0, 30, "black"), (1, 31, "white"), (2, 31, "black"), (3, 32, "white")]


def _mutations():
    def flag0(p): return p["flags"][0]
    yield "missing phase", lambda p: flag0(p).pop("phase")
    yield "extra key", lambda p: flag0(p).update(narration="x")
    yield "unknown concession kind", lambda p: flag0(p)["concessions"].update(new_holes=["f3"])
    yield "bad square", lambda p: flag0(p)["concessions"].update(new_weak_squares=["z9"])
    yield "SAN not the game's move", lambda p: [d.update(move_san="Bc4") for d in (flag0(p), p["move_evals"][2])]
    yield "flag disagrees with move_evals", lambda p: flag0(p).update(best_move_san="d4")
    yield "truncated move_evals", lambda p: p["move_evals"].pop()
    yield "flag outside the game", lambda p: flag0(p).update(move_number=40)
    yield "duplicate flag", lambda p: p["flags"].append(copy.deepcopy(flag0(p)))
    yield "number as string", lambda p: [d.update(wdl_delta="-0.2") for d in (flag0(p), p["move_evals"][2])]
    yield "bool as number", lambda p: p["summary"].update(total_flags=True)
    yield "unknown channel", lambda p: flag0(p).update(channel="deep_dive")
    yield "unknown phase", lambda p: [d.update(phase="late") for d in (flag0(p), p["move_evals"][2])]
    yield "missing summary", lambda p: p.pop("summary")


@pytest.mark.parametrize("name,mutate", list(_mutations()), ids=[n for n, _ in _mutations()])
def test_malformed_payloads_fail_loudly(name, mutate):
    payload = payload_for(test_grounding.PGN, [test_grounding.FLAG])
    mutate(payload)
    with pytest.raises(PayloadError):
        game_analysis_from_payload(payload, test_grounding.PGN)


def test_movetext_that_is_not_the_analysed_game_fails():
    payload = payload_for(test_grounding.PGN, [test_grounding.FLAG])
    with pytest.raises(PayloadError):
        game_analysis_from_payload(payload, "1. d4 d5 2. c4 e6 3. Nc3 Nf6 *")


def test_layers_stay_separate():
    # Narration and claims are never fields of the analysis models, and models are immutable
    assert not {"narration", "claims"} & set(MoveAnalysis.model_fields)
    analysis = game_analysis_from_payload(payload_for(test_grounding.PGN, [test_grounding.FLAG]), test_grounding.PGN)
    with pytest.raises(ValidationError):
        analysis.moves[2].san = "Nc3"


def test_position_payload_converts_and_fails_loudly():
    fen = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
    pos = position_analysis_from_payload(test_grounding.POSITION, fen)
    assert [line.moves for line, _ in pos.lines] == [("Nc3", "Nf6"), ("d4", "exd4")]
    assert pos.features.pawn_structure["black"].backward_pawns == ("d6",)
    bad = copy.deepcopy(test_grounding.POSITION)
    del bad["features"]["weak_squares"]
    with pytest.raises(PayloadError):
        position_analysis_from_payload(bad, fen)


@pytest.mark.skipif(not HAS_ENGINE, reason="STOCKFISH_PATH not configured")
def test_real_server_payload_round_trips(monkeypatch):
    from mcp_server.server import handle_line
    monkeypatch.setenv("STOCKFISH_NODES", "5000")  # shape, not strength
    pgn = open(os.path.join(FIXTURES, "carlsbad_quiet.pgn")).read()
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "analyze_pgn", "arguments": {"pgn": pgn, "max_flags": 50}}}
    payload = json.loads(handle_line(json.dumps(req))["result"]["content"][0]["text"])
    analysis = game_analysis_from_payload(payload, pgn)
    assert payload["flags"], "no flags: the round trip went unchecked"
    assert [to_flag_dict(m) for m in analysis.flagged()] == payload["flags"]


# --- the agent converts every server result on arrival (M11 wiring) ---

def _agent_run(pgn_result, position_result=None, messages=None):
    import asyncio
    from unittest.mock import patch
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from app.agent import root_agent
    prompts, out = [], []

    async def mcp(tool, args):
        return pgn_result if tool == "analyze_pgn" else position_result

    async def sub(self, agent, prompt):
        prompts.append((agent.name, prompt))
        return "The move loosened the position. The engine preferred another plan."

    async def go():
        service = InMemorySessionService()
        await service.create_session(app_name="app", user_id="u", session_id="s")
        runner = Runner(agent=root_agent, app_name="app", session_service=service)
        with patch("app.agent.call_mcp_tool_subprocess", new=mcp), \
             patch("app.agent.CoachingAgent._run_sub_agent", new=sub):
            for msg in messages or [test_agent_structure.PGN]:
                async for e in runner.run_async(user_id="u", session_id="s", new_message=types.Content(
                        role="user", parts=[types.Part.from_text(text=msg)])):
                    if e.content and e.content.parts:
                        out.append(e.content.parts[0].text)
    asyncio.run(go())
    return prompts, out


def test_agent_narration_prompts_unchanged_by_conversion():
    flags = test_agent_structure.FLAGS
    prompts, _ = _agent_run(payload_for(test_agent_structure.PGN, flags))
    narrator_prompts = sorted(p for name, p in prompts if name.startswith("analysing_"))
    assert narrator_prompts == sorted(format_flag_for_llm(f, 1, 1800) for f in flags)


def test_agent_stops_on_a_payload_that_does_not_match_the_game():
    payload = payload_for(test_agent_structure.PGN, test_agent_structure.FLAGS)
    payload["flags"][0]["move_san"] = payload["move_evals"][11]["move_san"] = "a5"  # 6...b5 was played
    prompts, out = _agent_run(payload)
    assert prompts == []  # nothing narrated from a payload that isn't this game
    assert "Game Report" not in out[-1] and "went wrong" in out[-1].lower()


def test_agent_stops_on_a_malformed_position_payload():
    pgn = test_grounding.PGN
    prompts, out = _agent_run(payload_for(pgn, [test_grounding.FLAG]), {"multipv_lines": [], "features": {}},
                              messages=[pgn, "2.Nf3"])
    assert not any(name.startswith("analysing_") and "Engine Alternatives" in p for name, p in prompts)
    assert "went wrong" in out[-1].lower()
