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

@pytest.mark.anyio
async def test_ask_about_move_fen_derivation_white():
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="app", user_id="user", session_id="s1")
    runner = Runner(agent=root_agent, app_name="app", session_service=session_service)
    
    with patch("app.agent.call_mcp_tool_subprocess", new_callable=AsyncMock) as mock_mcp, \
         patch("app.agent.CoachingAgent._run_sub_agent", new_callable=AsyncMock) as mock_sub:
        
        def mock_sub_side_effect(agent, prompt):
            if agent.name == "intent_router":
                if "move 3 white" in prompt:
                    return '{"is_move_query": true, "move_number": 3, "side": "white"}'
                return '{"is_move_query": false}'
            return "dummy explanation"
        
        mock_mcp.return_value = {"flags": [], "summary": {}, "multipv": []}
        mock_sub.side_effect = mock_sub_side_effect
        
        async for event in runner.run_async(
            user_id="user", session_id="s1",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=TEST_PGN)])
        ): pass
        
        async for event in runner.run_async(
            user_id="user", session_id="s1",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="ask about move 3 white")])
        ): pass
        
        pos_call = mock_mcp.call_args_list[1]
        assert pos_call.args[0] == "analyze_position"
        fen = pos_call.args[1]["fen"]
        
        board = chess.Board()
        board.push_san("e4")
        board.push_san("d5")
        board.push_san("Nc3")
        board.push_san("d4")
        assert fen == board.fen()

@pytest.mark.anyio
async def test_ask_about_move_fen_derivation_black():
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="app", user_id="user", session_id="s2")
    runner = Runner(agent=root_agent, app_name="app", session_service=session_service)
    
    with patch("app.agent.call_mcp_tool_subprocess", new_callable=AsyncMock) as mock_mcp, \
         patch("app.agent.CoachingAgent._run_sub_agent", new_callable=AsyncMock) as mock_sub:
        
        def mock_sub_side_effect(agent, prompt):
            if agent.name == "intent_router":
                if "move 2 black" in prompt:
                    return '{"is_move_query": true, "move_number": 2, "side": "black"}'
                return '{"is_move_query": false}'
            return "dummy explanation"
            
        mock_mcp.return_value = {"flags": [], "summary": {}, "multipv": []}
        mock_sub.side_effect = mock_sub_side_effect
        
        async for event in runner.run_async(
            user_id="user", session_id="s2",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=TEST_PGN)])
        ): pass
        
        async for event in runner.run_async(
            user_id="user", session_id="s2",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="ask about move 2 black")])
        ): pass
        
        pos_call = mock_mcp.call_args_list[1]
        assert pos_call.args[0] == "analyze_position"
        fen = pos_call.args[1]["fen"]
        
        board = chess.Board()
        board.push_san("e4")
        board.push_san("d5")
        board.push_san("Nc3")
        assert fen == board.fen()

@pytest.mark.anyio
async def test_ask_about_move_out_of_range():
    session_service = InMemorySessionService()
    await session_service.create_session(app_name="app", user_id="user", session_id="s3")
    runner = Runner(agent=root_agent, app_name="app", session_service=session_service)
    
    with patch("app.agent.call_mcp_tool_subprocess", new_callable=AsyncMock) as mock_mcp, \
         patch("app.agent.CoachingAgent._run_sub_agent", new_callable=AsyncMock) as mock_sub:
         
        def mock_sub_side_effect(agent, prompt):
            if agent.name == "intent_router":
                if "move 4 white" in prompt:
                    return '{"is_move_query": true, "move_number": 4, "side": "white"}'
                return '{"is_move_query": false}'
            return "dummy explanation"
            
        mock_mcp.return_value = {"flags": [], "summary": {}, "multipv": []}
        mock_sub.side_effect = mock_sub_side_effect
        
        async for event in runner.run_async(
            user_id="user", session_id="s3",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=TEST_PGN)])
        ): pass
        
        responses = []
        async for event in runner.run_async(
            user_id="user", session_id="s3",
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="ask about move 4 white")])
        ):
            if event.is_final_response():
                responses.append(event.content.parts[0].text)
                
        assert len(responses) > 0
        assert "outside the range" in responses[0].lower()
