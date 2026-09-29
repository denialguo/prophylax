import os
import json
import asyncio
from google.adk.agents import Agent
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types

from config.settings import get_judge_model, resolve_model
JUDGE_MODEL_NAME = get_judge_model()  # validates at startup (collision check)

judge_agent = Agent(
    name="llm_as_judge",
    model=resolve_model(JUDGE_MODEL_NAME),
    instruction=(
        "You are an expert chess coaching quality judge. Your task is to evaluate the quality of a generated positional chess coaching narration against a structured move payload and an optional Ground Truth Interpretation.\n"
        "You must return your evaluation in JSON format containing scores (0, 1, or 2) for the following four criteria:\n"
        "a) primary_theme: Scored 0-2. Score 2 if the primary theme of the narration matches the top-ranked feature delta in the payload (or if the delta is 0 and the theme is none). Score 1 if it is partially described. Score 0 if it is completely mismatched.\n"
        "b) line_interpretation: Scored 0-2. Score 2 if every quoted line's interpretation matches the moves' actual point (including side attribution and purpose; a capture is a capture, an escape is not an attack). Score 1 if there are minor interpretation errors. Score 0 if there are critical errors, incorrect mover attributions, or claims that are opposite to the chess facts. If a Ground Truth Interpretation is provided, you MUST use it as the absolute source of truth for the moves' purpose and meaning; score 2 if the narration matches the ground truth, and 0 if it contradicts it (such as claiming king exposure/checkmate when the ground truth says it is a trapped piece/escape attempt).\n"
        "c) payload_support: Scored 0-2. Score 2 if there are no claims unsupported by the payload signals. If a Ground Truth Interpretation is provided, treat it as supported/payload-correct, and do not mark named squares/pieces in the ground truth (e.g. 'f5 knight') as unsupported hallucinations.\n"
        "d) vocabulary_rating: Scored 0-2. Score 2 if the vocabulary and explanation depth are appropriate to the audience_rating in the config. Score 0 if inappropriate.\n\n"
        "Your output must be a single valid JSON object containing exactly the keys 'a', 'b', 'c', 'd', and a brief string 'reason' explaining the scoring.\n"
        "Example output:\n"
        "{\n"
        "  \"a\": 2,\n"
        "  \"b\": 2,\n"
        "  \"c\": 2,\n"
        "  \"d\": 2,\n"
        "  \"reason\": \"All criteria met perfectly.\"\n"
        "}"
    )
)

async def judge_narration(flagged_move: dict, narration: str, config: dict, ground_truth: str = None) -> dict:
    prompt = ""
    if ground_truth:
        prompt += f"Ground Truth Interpretation of Refutation/PV Lines:\n{ground_truth}\n\n"
    prompt += (
        f"Config:\n{json.dumps(config, indent=2)}\n\n"
        f"Flagged Move Payload:\n{json.dumps(flagged_move, indent=2)}\n\n"
        f"Generated Narration:\n{narration}\n\n"
        f"Please evaluate the generated narration against the payload and ground truth, and return the JSON object."
    )
    
    temp_service = InMemorySessionService()
    await temp_service.create_session(app_name="judge_app", user_id="temp_judge", session_id="judge_s")
    runner = Runner(agent=judge_agent, app_name="judge_app", session_service=temp_service)
    
    response_text = ""
    async for event in runner.run_async(
        user_id="temp_judge",
        session_id="judge_s",
        new_message=types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    ):
        if event.is_final_response():
            if event.content and event.content.parts:
                response_text = event.content.parts[0].text
                
    try:
        clean_text = response_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()
        scores = json.loads(clean_text)
        return scores
    except Exception as e:
        return {"a": 0, "b": 0, "c": 0, "d": 0, "reason": f"Parsing failed: {str(e)}: {response_text}"}

def evaluate_judge_result(scores: dict) -> bool:
    """
    Returns True if:
      - total score (a + b + c + d) >= 6
      - category (b) is not 0
      - category (c) is not 0
    """
    a = scores.get("a", 0)
    b = scores.get("b", 0)
    c = scores.get("c", 0)
    d = scores.get("d", 0)
    total = a + b + c + d
    return total >= 6 and b != 0 and c != 0
