import pytest
from google.adk.events import Event
from google.genai import types
from app.agent import CoachingAgent

@pytest.mark.anyio
async def test_ask_about_unflagged():
    agent = CoachingAgent(name="test")
    # Mock session state
    class MockContent:
        role = "user"
        parts = [types.Part.from_text(text="what about 2. Nf3")]
    class MockEvent:
        content = MockContent()
    
    class MockSession:
        state = {
            "pgn_text": '[Event "Test"]\n\n1. e4 e5 2. Nf3 Nc6',
            "config": {},
            "flags": [],
            "move_evals": [{"move_number": 2, "side": "white", "wdl_delta": -0.01, "phase": "opening"}]
        }
        events = [MockEvent()]
        
    class MockCtx:
        session = MockSession()
    
    # Mock intent router
    async def mock_run_sub_agent(sub_agent, prompt):
        if sub_agent.name == "intent_router":
            return '{"is_move_query": true, "move_number": 2, "side": "white", "requested_san": "Nf3"}'
        else:
            return "This is the explanation."
            
    agent._run_sub_agent = mock_run_sub_agent
    
    events = [e async for e in agent._run_async_impl(MockCtx())]
    assert len(events) == 1
    content = events[0].content.parts[0].text
    print("SUCCESS:", content)

