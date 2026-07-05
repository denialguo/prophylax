import pytest
from google.adk.events import Event
from google.genai import types
import chess
from app.agent import CoachingAgent

@pytest.mark.anyio
async def test_subject_anchoring_mismatch():
    agent = CoachingAgent(name="test")
    # Mock session state
    class MockContent:
        role = "user"
        parts = [types.Part.from_text(text="what about 2. Bxc4")]
    class MockEvent:
        content = MockContent()
    
    class MockSession:
        state = {
            "pgn_text": '[Event "Test"]\n\n1. e4 e5 2. Nf3 Nc6',
            "config": {}
        }
        events = [MockEvent()]
        
    class MockCtx:
        session = MockSession()
    
    # Mock intent router
    async def mock_run_sub_agent(sub_agent, prompt):
        return '{"is_move_query": true, "move_number": 2, "side": "white", "requested_san": "Bxc4"}'
    
    agent._run_sub_agent = mock_run_sub_agent
    
    events = [e async for e in agent._run_async_impl(MockCtx())]
    
    assert len(events) == 1
    content = events[0].content.parts[0].text
    # Must reject because actual move is Nf3
    assert "You asked about 'Bxc4'" in content
    assert "was Nf3" in content
    assert "Would you like to analyze Nf3 instead?" in content

def test_validate_narration_header():
    from evals.validate_narration import validate_narration
    fake_flag = {"move_san": "Nf3", "move_number": 2, "side": "white", "pv": ["Nf3"]}
    
    # Valid output
    assert validate_narration("This move improves control of the center.", fake_flag)
    
    # Invalid output (starts with Move)
    assert not validate_narration("Move 2.Nf3 (White): WDL drop 10%\nThis is bad.", fake_flag)
