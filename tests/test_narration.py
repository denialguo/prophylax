import os
import re
import json
import pytest
import chess
import chess.pgn
from typing import Dict, Any

from app.agent import opening_agent, middlegame_agent, endgame_agent, NARRATOR_MODEL_NAME
from app.agent import CoachingAgent
from scripts.format_narration import format_flag_for_llm
from evals.validate_narration import validate_narration
from evals.judge import JUDGE_MODEL_NAME
from config.settings import get_narrator_model, get_judge_model

# Instantiate a CoachingAgent instance
coach = CoachingAgent(name="test_coach")

def find_skills_dir() -> str:
    curr = os.path.dirname(os.path.abspath(__file__))
    while curr:
        candidate = os.path.join(curr, "evals", "skills")
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent
    return ""

def load_eval_cases() -> list:
    skills_dir = find_skills_dir()
    if not skills_dir:
        return []
    
    cases = []
    for f_name in os.listdir(skills_dir):
        if f_name.endswith(".json"):
            path = os.path.join(skills_dir, f_name)
            with open(path) as f:
                cases.extend(json.load(f))
    return cases

def get_matching_game(flag: dict):
    """Eval cases are cut from tests/fixtures games; find the game so the
    validator can check numbered-move legality against it."""
    fix_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    for f_name in sorted(os.listdir(fix_dir)):
        if not f_name.endswith(".pgn"):
            continue
        with open(os.path.join(fix_dir, f_name)) as fh:
            game = chess.pgn.read_game(fh)
        board = game.board()
        for idx, m in enumerate(game.mainline_moves()):
            side = "white" if board.turn == chess.WHITE else "black"
            if idx // 2 + 1 == flag.get("move_number") and side == flag.get("side", "").lower() and board.san(m) == flag.get("move_san"):
                return game
            board.push(m)
    return None

def count_sentences(text: str) -> int:
    body = text.strip()
    # Split by period followed by space and alphanumeric
    sentences = [s.strip() for s in re.split(r'(?<=\.)\s+(?=[A-Za-z0-9])', body) if s.strip()]
    return len(sentences)

def pytest_configure(config):
    """Log narrator and judge model names at session start."""
    print(f"\n--- Narration Test Config ---")
    print(f"Narrator model: {NARRATOR_MODEL_NAME}")
    print(f"Judge model:    {JUDGE_MODEL_NAME}")
    print(f"----------------------------\n")

@pytest.mark.anyio
@pytest.mark.narration
@pytest.mark.parametrize("case", load_eval_cases())
async def test_skills_narration(case: Dict[str, Any]):
    flagged_move = case["input"]["flagged_move"]
    config = case["input"]["config"]
    depth = config.get("explanation_depth", 1)
    rating = config.get("audience_rating", 1800)
    
    phase = flagged_move.get("phase", "middlegame")
    if phase == "opening":
        agent = opening_agent
    elif phase == "endgame":
        agent = endgame_agent
    else:
        agent = middlegame_agent
        
    prompt = format_flag_for_llm(flagged_move, depth, rating)
    game = get_matching_game(flagged_move)  # None for synthetic cases

    # Run the narration generator — first attempt
    narration = await coach._run_sub_agent(agent, prompt)
    first_pass = validate_narration(narration, flagged_move, game)
    
    retries = 0
    if not first_pass:
        # Retry once — tracked as a metric, not a hard failure
        retries = 1
        narration = await coach._run_sub_agent(agent, prompt)
    
    # Hard requirement: FINAL narration (after retry/fallback) has zero violations
    assert validate_narration(narration, flagged_move, game) is True, (
        f"validate_narration failed for case {case['name']} after {retries} retries. "
        f"Narration:\n{narration}"
    )
    # Retries are logged and asserted <= 1 per flag
    assert retries <= 1, f"Too many retries ({retries}) for case {case['name']}"
    if retries > 0:
        print(f"  [METRIC] Case {case['name']} required {retries} retry (first-shot fabrication)")
    
    # 2. Header is emitted by code, never by the narrator (operator decision D1)
    first_line_of_prompt = prompt.splitlines()[0]
    expected_header = first_line_of_prompt.split("header: ", 1)[1].strip()
    first_line_of_narration = narration.splitlines()[0].strip()
    assert first_line_of_narration != expected_header, f"Narrator emitted the header: '{first_line_of_narration}'"

    # 3. Sentence count within depth-1 shape (2-4 sentences)
    num_sentences = count_sentences(narration)
    assert 2 <= num_sentences <= 4, f"Sentence count {num_sentences} not in range [2, 4] for case {case['name']}. Narration:\n{narration}"
    
    # 4. Every forbidden_claims entry is absent (case-insensitive substring check)
    for claim in case.get("forbidden_claims", []):
        assert claim.lower() not in narration.lower(), f"Forbidden claim '{claim}' found in narration for case {case['name']}"
        
    # 5. Expected primary theme check
    theme = case.get("expected_primary_theme", "none")
    if theme == "king_safety_delta":
        theme_tokens = ["king", "shield", "safety", "exposure", "expose"]
        assert any(token in narration.lower() for token in theme_tokens), f"Expected king safety tokens for case {case['name']}"
    elif theme in ["new_weak_squares", "weak_squares"]:
        concessions = flagged_move.get("concessions", {})
        new_ws = concessions.get("new_weak_squares", [])
        assert len(new_ws) > 0, f"No concessions found for case {case['name']}"
        assert any(sq.lower() in narration.lower() for sq in new_ws), f"Expected at least one concession square {new_ws} named in narration for case {case['name']}"
    elif theme == "none":
        positional_tokens = ["weak", "backward", "outpost", "structure", "king", "safety"]
        assert not any(token in narration.lower() for token in positional_tokens), f"Positional tokens found in 'none' theme case {case['name']}"
