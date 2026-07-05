import pytest
import io
import chess.pgn
from unittest.mock import patch, AsyncMock
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types

from app.agent import root_agent

TEST_PGN = """[Event "Test Game"]
[Site "Local"]
[Date "2026.07.05"]
[Round "1"]
[White "Player1"]
[Black "Player2"]
[Result "*"]

1. e4 d5 2. Nc3 d4 3. Nce2 e5 *"""

FLATTENED_PGN = '[Event "Test"] [Site "Bonn"] 1. e4 e5 2. Nf3 Nc6'

@pytest.mark.anyio
async def test_routing_flattened_pgn():
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="app", user_id="user", session_id="s1")
    runner = Runner(agent=root_agent, app_name="app", session_service=session_service)
    
    with patch("app.agent.call_mcp_tool_subprocess", new_callable=AsyncMock) as mock_mcp, \
         patch("app.agent.CoachingAgent._run_sub_agent", new_callable=AsyncMock) as mock_sub:
        
        mock_mcp.return_value = {"flags": [], "summary": {"total_flags": 0, "phase_distribution": {}}, "multipv": []}
        
        async for event in runner.run_async(
            user_id="user", session_id="s1",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=FLATTENED_PGN)])
        ): pass
        
        assert len(mock_mcp.call_args_list) == 1
        assert mock_mcp.call_args_list[0].args[0] == "analyze_pgn"

@pytest.mark.anyio
async def test_routing_no_game_loaded():
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="app", user_id="user", session_id="s2")
    runner = Runner(agent=root_agent, app_name="app", session_service=session_service)
    
    responses = []
    async for event in runner.run_async(
        user_id="user", session_id="s2",
        new_message=types.Content(role="user", parts=[types.Part.from_text(text="What about move 15?")])
    ):
        if event.is_final_response():
            responses.append(event.content.parts[0].text)
            
    assert len(responses) > 0
    assert "no game loaded" in responses[0].lower()

@pytest.mark.anyio
async def test_routing_ask_about_move():
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="app", user_id="user", session_id="s3")
    runner = Runner(agent=root_agent, app_name="app", session_service=session_service)
    
    with patch("app.agent.call_mcp_tool_subprocess", new_callable=AsyncMock) as mock_mcp, \
         patch("app.agent.CoachingAgent._run_sub_agent", new_callable=AsyncMock) as mock_sub:
        
        mock_mcp.return_value = {"flags": [], "summary": {}, "multipv": []}
        
        async for event in runner.run_async(
            user_id="user", session_id="s3",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=TEST_PGN)])
        ): pass
        
        mock_sub.return_value = '{"is_move_query": true, "move_number": 2, "side": "black"}'
        
        async for event in runner.run_async(
            user_id="user", session_id="s3",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="What about move 2 black?")])
        ): pass
        
        assert len(mock_mcp.call_args_list) == 2
        assert mock_mcp.call_args_list[1].args[0] == "analyze_position"
