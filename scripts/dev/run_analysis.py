import asyncio
import os
from app.agent import root_agent
from google.genai import types
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner

async def main():
    temp_service = InMemorySessionService()
    await temp_service.create_session(app_name="app", user_id="temp", session_id="s")
    runner = Runner(agent=root_agent, app_name="app", session_service=temp_service)
    
    with open("jyotibikash_vs_DankSonPotato_2026.06.20.pgn", "r") as f:
        pgn = f.read()
        
    async for event in runner.run_async(
        user_id="temp",
        session_id="s",
        new_message=types.Content(role="user", parts=[types.Part.from_text(text=pgn)])
    ):
        if event.is_final_response():
            print(event.content.parts[0].text)

if __name__ == "__main__":
    asyncio.run(main())
