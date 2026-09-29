"""M8: one narrator definition (shared contract + phase section), deterministic
routing first, bounded concurrent narration reported in game order."""
import asyncio

import pytest
from unittest.mock import patch
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types

import app.agent as agent_mod
from app.agent import root_agent, narrator_for
from tests.payload_schema import pgn_payload, position_payload

PGN = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 *"


@pytest.mark.parametrize("phase,section", [
    ("opening", "In the opening phase"),
    ("middlegame", "In the middlegame phase"),
    ("endgame", "In the endgame phase"),
])
def test_each_phase_gets_contract_plus_its_section(phase, section):
    instruction = narrator_for(phase).instruction
    assert instruction.startswith("# Narration Contract")
    assert "new_pawn_unsupported" in instruction   # contract edited once, reaches every phase
    assert section in instruction
    others = {"In the opening phase", "In the middlegame phase", "In the endgame phase"} - {section}
    assert not any(o in instruction for o in others)


def flag(n, side, san):
    return {"move_san": san, "move_number": n, "side": side, "phase": "opening", "wdl_delta": -0.2,
            "best_move_san": "", "pv": [], "refutation_pv": [], "feature_deltas": {}, "concessions": {},
            "channel": "wdl"}


FLAGS = [flag(6, "black", "b5"), flag(2, "white", "Nf3"), flag(4, "white", "Ba4"),
         flag(3, "black", "a6"), flag(5, "white", "O-O"), flag(4, "black", "Nf6")]


async def run(messages, mock_sub):
    async def mcp(tool, args):
        if tool == "analyze_pgn":
            return pgn_payload(PGN, FLAGS)
        return position_payload()
    service = InMemorySessionService()
    await service.create_session(app_name="app", user_id="u", session_id="s")
    runner = Runner(agent=root_agent, app_name="app", session_service=service)
    out = []
    with patch("app.agent.call_mcp_tool_subprocess", new=mcp), \
         patch("app.agent.CoachingAgent._run_sub_agent", new=mock_sub):
        for msg in messages:
            async for e in runner.run_async(user_id="u", session_id="s",
                                            new_message=types.Content(role="user", parts=[types.Part.from_text(text=msg)])):
                if e.content and e.content.parts:
                    out.append(e.content.parts[0].text)
    return out


@pytest.mark.anyio
async def test_narration_is_concurrent_bounded_and_in_game_order(monkeypatch):
    monkeypatch.setattr(agent_mod, "NARRATION_CONCURRENCY", 2)
    active = peak = 0

    async def mock_sub(self, agent, prompt):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "The engine disliked it."

    out = await run([PGN], mock_sub)
    assert peak == 2
    report = out[-1]
    order = [report.index(h) for h in ("2.Nf3", "3...a6", "4.Ba4", "4...Nf6", "5.O-O", "6...b5")]
    assert order == sorted(order)


@pytest.mark.anyio
async def test_parsed_move_reference_skips_llm_router():
    seen = []

    async def mock_sub(self, agent, prompt):
        seen.append(agent.name)
        return "The engine disliked it."

    await run([PGN, "what about 6...b5?"], mock_sub)
    assert "intent_router" not in seen


@pytest.mark.anyio
async def test_report_persists_narration_stats():
    calls = 0

    async def mock_sub(self, agent, prompt):
        nonlocal calls
        calls += 1
        return "Bc4 was better." if calls <= 2 else "The engine disliked it."  # first flag: retry then fallback

    async def mcp(tool, args):
        return pgn_payload(PGN, [FLAGS[1]])
    service = InMemorySessionService()
    await service.create_session(app_name="app", user_id="u", session_id="s")
    runner = Runner(agent=root_agent, app_name="app", session_service=service)
    with patch("app.agent.call_mcp_tool_subprocess", new=mcp), \
         patch("app.agent.CoachingAgent._run_sub_agent", new=mock_sub):
        async for _ in runner.run_async(user_id="u", session_id="s",
                                        new_message=types.Content(role="user", parts=[types.Part.from_text(text=PGN)])):
            pass
    state = (await service.get_session(app_name="app", user_id="u", session_id="s")).state
    assert state["narration_stats"] == {"attempted": 1, "passed_first": 0, "passed_retry": 0, "fallback": 1}
