"""M7: the deep dive narrates from analyze_position's data, and conversational
replies are held to the same grounding rules as narration."""
import pytest
from unittest.mock import patch
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types

from app.agent import root_agent

PGN = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *"
FLAG = {
    "move_san": "Nf3", "move_number": 2, "side": "white", "phase": "opening",
    "wdl_delta": -0.2, "best_move_san": "Nc3", "pv": ["Nc3", "Nf6"],
    "refutation_pv": ["Nc6"], "feature_deltas": {}, "concessions": {"new_weak_squares": ["f3"]},
    "channel": "wdl",
}
POSITION = {
    "multipv_lines": [{"pv": ["Nc3", "Nf6"], "wdl": None}, {"pv": ["d4", "exd4"], "wdl": None}],
    "features": {
        "weak_squares": {"white": [{"square": "h3", "complex": "light"}], "black": []},
        "pawn_structure": {"white": {"backward_pawns": [], "isolated_pawns": [], "doubled_pawns": []},
                           "black": {"backward_pawns": ["d6"], "isolated_pawns": [], "doubled_pawns": []}},
    },
}


async def mock_mcp(tool, args):
    if tool == "analyze_pgn":
        return {"flags": [FLAG],
                "move_evals": [{k: FLAG[k] for k in ("move_san", "move_number", "side", "phase", "wdl_delta", "best_move_san")}],
                "summary": {"total_flags": 1, "phase_distribution": {"opening": 1}}}
    return POSITION


async def run(messages, replies):
    """replies: agent name -> list of replies, consumed in order. Returns (prompts, last output)."""
    prompts = []

    async def mock_sub(self, agent, prompt):
        prompts.append((agent.name, prompt))
        if agent.name == "intent_router":
            return ('{"is_move_query": true, "move_number": 2, "side": "white"}' if "move 2" in prompt
                    else '{"is_move_query": false}')
        return replies[agent.name].pop(0)

    service = InMemorySessionService()
    await service.create_session(app_name="app", user_id="u", session_id="s")
    runner = Runner(agent=root_agent, app_name="app", session_service=service)
    out = ""
    with patch("app.agent.call_mcp_tool_subprocess", new=mock_mcp), \
         patch("app.agent.CoachingAgent._run_sub_agent", new=mock_sub):
        for msg in messages:
            async for e in runner.run_async(user_id="u", session_id="s",
                                            new_message=types.Content(role="user", parts=[types.Part.from_text(text=msg)])):
                if e.content and e.content.parts:
                    out = e.content.parts[0].text
    return prompts, out


@pytest.mark.anyio
async def test_deep_dive_prompt_carries_engine_lines_and_features():
    prompts, out = await run(
        [PGN, "tell me about move 2 white"],
        {"analysing_openings": ["The engine prefers Nc3.", "2.d4 exd4 was the other try, and h3 stays weak."]},
    )
    deep = [p for name, p in prompts if name == "analysing_openings"][-1]
    assert "1) 2.Nc3 2...Nf6" in deep and "2) 2.d4 2...exd4" in deep
    assert "White weak squares: h3" in deep and "Black backward pawns: d6" in deep
    # citing an alternative line and a feature square is grounded: no retry, no fallback
    assert len([n for n, _ in prompts if n == "analysing_openings"]) == 2
    assert "2.d4 exd4 was the other try" in out


@pytest.mark.anyio
async def test_deep_dive_rejects_move_outside_engine_lines():
    prompts, out = await run(
        [PGN, "tell me about move 2 white"],
        {"analysing_openings": ["The engine prefers Nc3.", "Bc4 was better.", "Bc4 was still better."]},
    )
    assert "Bc4" not in out
    assert "engine preferred alternative is Nc3" in out


@pytest.mark.anyio
async def test_conversation_outside_flags_retries_then_falls_back():
    prompts, out = await run(
        [PGN, "what was my worst idea?"],
        {"analysing_openings": ["The engine prefers Nc3."],
         "conversational": ["You should have played Bc4.", "Really, Bc4 on g8 was best."]},
    )
    retry = [p for n, p in prompts if n == "conversational"][-1]
    assert "rejected because move Bc4 is not in the engine payload" in retry
    assert "Bc4" not in out and "only answer from the engine analysis" in out


@pytest.mark.anyio
async def test_conversation_citing_flags_and_played_moves_passes():
    prompts, out = await run(
        [PGN, "what was my worst idea?"],
        {"analysing_openings": ["The engine prefers Nc3."],
         "conversational": ["2.Nf3 let f3 go weak; Nc3 was preferred. Later 3.Bb5 was fine."]},
    )
    assert len([n for n, _ in prompts if n == "conversational"]) == 1
    assert out.startswith("2.Nf3 let f3 go weak")
