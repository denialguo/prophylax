import os
import inspect
import pytest
from unittest.mock import patch, AsyncMock
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types

from app.agent import root_agent, call_mcp_tool_subprocess

# Dummy PGN for testing
TEST_PGN = """[Event "Test Game"]
[Site "Local"]
[Date "2026.07.05"]
[Round "1"]
[White "Player1"]
[Black "Player2"]
[Result "*"]

1. e4 d5 2. Nc3 d4 3. Nce2 e5 4. d3 Nc6 5. Ng3 Nf6 6. Nf3 Bg4 7. Be2 Bxf3 8. Bxf3 Bd6 9. O-O O-O 10. Bg5 h6 11. Bd2 Re8 12. c3 Ne7 13. b4 c5 *"""

@pytest.mark.anyio
@pytest.mark.narration
async def test_mcp_subprocess_launch():
    # Assert that call_mcp_tool_subprocess uses create_subprocess_exec to launch mcp_server/server.py
    src = inspect.getsource(call_mcp_tool_subprocess)
    assert "create_subprocess_exec" in src or "create_subprocess_shell" in src
    assert "mcp_server/server.py" in src
    assert "sys.executable" in src

@pytest.mark.anyio
@pytest.mark.narration
async def test_analyze_pgn_trajectory():
    calls = []
    
    async def mock_call(tool_name: str, arguments: dict) -> dict:
        calls.append((tool_name, arguments))
        if tool_name == "analyze_pgn":
            return {
                "flags": [
                    {
                        "move_san": "b4",
                        "move_number": 13,
                        "side": "white",
                        "phase": "middlegame",
                        "wdl_delta": -0.31,
                        "best_move_san": "cxd4",
                        "pv": ["cxd4", "exd4"],
                        "refutation_pv": ["a5", "bxa5"],
                        "concessions": {"new_weak_squares": ["a3", "c3"]}
                    }
                ],
                "summary": {
                    "total_flags": 1,
                    "phase_distribution": {"middlegame": 1}
                }
            }
        return {}

    session_service = InMemorySessionService()
    await session_service.create_session(app_name="app", user_id="user", session_id="s1")
    runner = Runner(agent=root_agent, app_name="app", session_service=session_service)
    
    # 1. "Analyze this PGN" session: tool trajectory is EXACTLY [analyze_pgn]
    with patch("app.agent.call_mcp_tool_subprocess", new=mock_call), \
         patch("app.agent.CoachingAgent._run_sub_agent", new_callable=AsyncMock) as mock_sub:
        
        mock_sub.return_value = "Move 13.b4 (White): WDL drop 31.0%\nDummy explanation."
        
        async for event in runner.run_async(
            user_id="user",
            session_id="s1",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=TEST_PGN)])
        ):
            pass
            
        assert len(calls) == 1
        assert calls[0][0] == "analyze_pgn"
        assert "pgn" in calls[0][1]

@pytest.mark.anyio
@pytest.mark.narration
async def test_deep_dive_trajectory():
    calls = []
    
    async def mock_call(tool_name: str, arguments: dict) -> dict:
        calls.append((tool_name, arguments))
        if tool_name == "analyze_pgn":
            return {
                "flags": [
                    {
                        "move_san": "b4",
                        "move_number": 13,
                        "side": "white",
                        "phase": "middlegame",
                        "wdl_delta": -0.31,
                        "best_move_san": "cxd4",
                        "pv": ["cxd4", "exd4"],
                        "refutation_pv": ["a5", "bxa5"],
                        "concessions": {"new_weak_squares": ["a3", "c3"]}
                    }
                ],
                "summary": {
                    "total_flags": 1,
                    "phase_distribution": {"middlegame": 1}
                }
            }
        elif tool_name == "analyze_position":
            return {
                "pv": ["cxd4", "exd4"],
                "multipv": []
            }
        return {}

    session_service = InMemorySessionService()
    await session_service.create_session(app_name="app", user_id="user", session_id="s2")
    runner = Runner(agent=root_agent, app_name="app", session_service=session_service)
    
    with patch("app.agent.call_mcp_tool_subprocess", new=mock_call), \
         patch("app.agent.CoachingAgent._run_sub_agent", new_callable=AsyncMock) as mock_sub:
        
        mock_sub.return_value = "Move 13.b4 (White): WDL drop 31.0%\nDummy explanation."
        
        # First send PGN to populate state
        async for event in runner.run_async(
            user_id="user",
            session_id="s2",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=TEST_PGN)])
        ):
            pass
            
        # Do not clear calls, so we can verify IN_ORDER [analyze_pgn, analyze_position]
        
        # Second, ask for a deep dive
        async for event in runner.run_async(
            user_id="user",
            session_id="s2",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="ask about move 13 white")])
        ):
            pass
            
        # 2. "Deep-dive move X": trajectory is [analyze_pgn, analyze_position] IN_ORDER
        # Verify FEN belongs to the correct position (before move 13.b4)
        assert len(calls) == 2
        assert calls[0][0] == "analyze_pgn"
        assert calls[1][0] == "analyze_position"
        assert "fen" in calls[1][1]
        
        # Verify FEN has Black to move or the correct structure before 13.b4
        # Board turn should be White (since 13.b4 is White's move)
        fen = calls[1][1]["fen"]
        assert " w " in fen
