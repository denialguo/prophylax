"""M3 agent side: tool-call timeout, typed errors, no internals shown to the user."""
import os
import stat
import subprocess
import sys
import time

import pytest
from unittest.mock import patch
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types

from app.agent import root_agent, call_mcp_tool_subprocess, ToolError
from mcp_server.server import ENGINE_TIMEOUT, ENGINE_CRASHED, INVALID_PARAMS

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_uci_engine.py")
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
PGN = '[Event "T"]\n\n1. e4 e5 2. Nf3 Nc6 *'
SECRET = "/Users/someone/secret/path Traceback"


@pytest.mark.anyio
async def test_tool_call_timeout_kills_server_and_engine(tmp_path, monkeypatch):
    exe = tmp_path / "fake_stockfish"
    exe.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("STOCKFISH_PATH", str(exe))
    monkeypatch.setenv("STOCKFISH_NODES", "1000")
    monkeypatch.setenv("FAKE_ENGINE_MODE", "hang")
    monkeypatch.setenv("STOCKFISH_SEARCH_TIMEOUT_S", "60")  # agent deadline fires first
    monkeypatch.setenv("PROPHYLAX_TOOL_TIMEOUT_S", "2")

    start = time.monotonic()
    with pytest.raises(ToolError) as exc:
        await call_mcp_tool_subprocess("analyze_position", {"fen": FEN, "multipv": 1})
    assert exc.value.code == ENGINE_TIMEOUT
    assert time.monotonic() - start < 10
    time.sleep(0.5)
    leftover = subprocess.run(["pgrep", "-f", str(exe)], capture_output=True, text=True).stdout
    assert leftover == "", "engine process survived the tool timeout"


@pytest.mark.anyio
async def test_sanitizer_rejection_is_typed():
    with pytest.raises(ToolError) as exc:
        await call_mcp_tool_subprocess("analyze_position", {"fen": "not a fen"})
    assert exc.value.code == INVALID_PARAMS


async def _run(msg):
    service = InMemorySessionService()
    await service.create_session(app_name="app", user_id="u", session_id="s")
    runner = Runner(agent=root_agent, app_name="app", session_service=service)
    texts = []
    async for e in runner.run_async(user_id="u", session_id="s",
                                    new_message=types.Content(role="user", parts=[types.Part.from_text(text=msg)])):
        if e.content and e.content.parts:
            texts.append(e.content.parts[0].text)
    return "\n".join(texts)


@pytest.mark.anyio
@pytest.mark.parametrize("code,expect", [
    (ENGINE_TIMEOUT, "timed out"),
    (ENGINE_CRASHED, "engine stopped"),
])
async def test_engine_errors_become_short_user_messages(code, expect):
    async def boom(tool, args):
        raise ToolError(code, SECRET)
    with patch("app.agent.call_mcp_tool_subprocess", new=boom):
        out = await _run(PGN)
    assert expect in out.lower()
    assert "Traceback" not in out and "/Users/" not in out


@pytest.mark.anyio
async def test_invalid_input_reason_is_shown():
    # The server's invalid-params message describes the user's own input; surface it
    async def boom(tool, args):
        raise ToolError(INVALID_PARAMS, "Game has 600 plies; the limit is 400.")
    with patch("app.agent.call_mcp_tool_subprocess", new=boom):
        out = await _run(PGN)
    assert "couldn't be analysed" in out and "the limit is 400" in out


@pytest.mark.anyio
async def test_unexpected_errors_hide_internals():
    async def mcp(tool, args):
        return {"flags": [{"move_san": "Nf3", "move_number": 2, "side": "white", "phase": "opening", "wdl_delta": -0.2}],
                "summary": {}}
    async def llm(self, agent, prompt):
        raise RuntimeError(SECRET)
    with patch("app.agent.call_mcp_tool_subprocess", new=mcp), \
         patch("app.agent.CoachingAgent._run_sub_agent", new=llm):
        out = await _run(PGN)
    assert "Traceback" not in out and "/Users/" not in out
    assert "went wrong" in out.lower()


@pytest.mark.anyio
async def test_server_is_reused_and_respawned_after_death(tmp_path, monkeypatch):
    import asyncio
    import signal
    import app.agent as agent
    exe = tmp_path / "fake_stockfish"
    exe.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("STOCKFISH_PATH", str(exe))
    monkeypatch.setenv("STOCKFISH_NODES", "1000")
    monkeypatch.setenv("FAKE_ENGINE_MODE", "answer")
    spawns = []
    real_exec = asyncio.create_subprocess_exec
    async def counting_exec(*a, **kw):
        spawns.append(a)
        return await real_exec(*a, **kw)
    monkeypatch.setattr(agent.asyncio, "create_subprocess_exec", counting_exec)

    await call_mcp_tool_subprocess("analyze_position", {"fen": FEN, "multipv": 1})
    await call_mcp_tool_subprocess("analyze_pgn", {"pgn": PGN})
    assert len(spawns) == 1

    os.killpg(agent._server[0].pid, signal.SIGKILL)
    await agent._server[0].wait()
    res = await call_mcp_tool_subprocess("analyze_position", {"fen": FEN, "multipv": 2})
    assert len(spawns) == 2 and res["multipv_lines"]
    agent._discard_server()
