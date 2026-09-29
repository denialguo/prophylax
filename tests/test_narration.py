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
    # Move numbers ("46. Kf6", "46... Kh6") are not sentence ends
    body = re.sub(r'\b\d+\.(?:\.\.|\u2026)?\s*(?=[KQRBNO]|[a-h][1-8x])', '', text.strip())
    # Split by period followed by space and alphanumeric
    sentences = [s.strip() for s in re.split(r'(?<=\.)\s+(?=[A-Za-z0-9])', body) if s.strip()]
    return len(sentences)

def gate_failures(case: Dict[str, Any], narration: str, prompt: str) -> list:
    """The narration-gate checks on a final narration, as failure messages in check
    order (empty = pass): header not emitted, 2-4 sentences, no forbidden claim, the
    expected theme present. Shared by test_skills_narration and the M13 comparison."""
    flagged_move = case["input"]["flagged_move"]
    failures = []
    # 2. Header is emitted by code, never by the narrator (operator decision D1)
    first_line_of_prompt = prompt.splitlines()[0]
    expected_header = first_line_of_prompt.split("header: ", 1)[1].strip()
    first_line_of_narration = narration.splitlines()[0].strip()
    if not first_line_of_narration != expected_header:
        failures.append(f"Narrator emitted the header: '{first_line_of_narration}'")

    # 3. Sentence count within depth-1 shape (2-4 sentences)
    num_sentences = count_sentences(narration)
    if not 2 <= num_sentences <= 4:
        failures.append(f"Sentence count {num_sentences} not in range [2, 4] for case {case['name']}. Narration:\n{narration}")

    # 4. Every forbidden_claims entry is absent (case-insensitive substring check)
    for claim in case.get("forbidden_claims", []):
        if not claim.lower() not in narration.lower():
            failures.append(f"Forbidden claim '{claim}' found in narration for case {case['name']}")

    # 5. Expected primary theme check
    theme = case.get("expected_primary_theme", "none")
    if theme == "king_safety_delta":
        theme_tokens = ["king", "shield", "safety", "exposure", "expose"]
        if not any(token in narration.lower() for token in theme_tokens):
            failures.append(f"Expected king safety tokens for case {case['name']}")
    elif theme in ["new_weak_squares", "weak_squares"]:
        concessions = flagged_move.get("concessions", {})
        new_ws = concessions.get("new_weak_squares", [])
        if not len(new_ws) > 0:
            failures.append(f"No concessions found for case {case['name']}")
        elif not any(sq.lower() in narration.lower() for sq in new_ws):
            failures.append(f"Expected at least one concession square {new_ws} named in narration for case {case['name']}")
    elif theme == "none":
        positional_tokens = ["weak", "backward", "outpost", "structure", "safety"]
        if any(token in narration.lower() for token in positional_tokens):
            failures.append(f"Positional tokens found in 'none' theme case {case['name']}")
    return failures

@pytest.mark.parametrize("narration,passes", [
    ("The king should head to e3.", True),     # naming the king is not a king-safety theme
    ("This weakens king safety.", False),
])
def test_none_theme_forbids_themes_not_pieces(narration, passes):
    case = {"name": "t", "expected_primary_theme": "none", "input": {"flagged_move": {}}}
    failures = [f for f in gate_failures(case, narration, "header: x") if "Positional tokens" in f]
    assert (not failures) == passes


def test_count_sentences_skips_move_numbers():
    assert count_sentences("White wins through 46. Kf6, then 46... Kh6 and 47.Kf7 exd5. It is lost. Really.") == 3
    assert count_sentences("One. Two. Three.") == 3

def pytest_configure(config):
    """Log narrator and judge model names at session start."""
    print(f"\n--- Narration Test Config ---")
    print(f"Narrator model: {NARRATOR_MODEL_NAME}")
    print(f"Judge model:    {JUDGE_MODEL_NAME}")
    print(f"----------------------------\n")

@pytest.mark.anyio
@pytest.mark.narration
@pytest.mark.parametrize("case", load_eval_cases())
async def test_skills_narration(case: Dict[str, Any], record_property):
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
    # Tracked metric, persisted in the junit XML (<property name="retries" ...>)
    record_property("retries", retries)
    record_property("first_pass", first_pass)
    if retries > 0:
        print(f"  [METRIC] Case {case['name']} required {retries} retry (first-shot fabrication)")
    
    # 2-5. Narration-gate checks (shared with evals/compare_narration.py)
    failures = gate_failures(case, narration, prompt)
    assert not failures, failures[0]


# --- the gate_failures extraction gives the same verdicts as the inline asserts it replaced ---

def _legacy_gate(case, narration, prompt):
    """Checks 2-5 exactly as test_skills_narration asserted them before M13 (verbatim)."""
    flagged_move = case["input"]["flagged_move"]
    first_line_of_prompt = prompt.splitlines()[0]
    expected_header = first_line_of_prompt.split("header: ", 1)[1].strip()
    first_line_of_narration = narration.splitlines()[0].strip()
    assert first_line_of_narration != expected_header, f"Narrator emitted the header: '{first_line_of_narration}'"
    num_sentences = count_sentences(narration)
    assert 2 <= num_sentences <= 4, f"Sentence count {num_sentences} not in range [2, 4] for case {case['name']}. Narration:\n{narration}"
    for claim in case.get("forbidden_claims", []):
        assert claim.lower() not in narration.lower(), f"Forbidden claim '{claim}' found in narration for case {case['name']}"
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
        positional_tokens = ["weak", "backward", "outpost", "structure", "safety"]
        assert not any(token in narration.lower() for token in positional_tokens), f"Positional tokens found in 'none' theme case {case['name']}"


# Real narrations recorded on 2026-09-28 (CLI runs and the failing regen case), plus
# hand-made ones that each trip one check
CORPUS = [
    ("scandinavian_b4_blunder", "The move 13.b4 is a significant error that results in a 31.9% drop in White's win "
     "probability. By pushing the b-pawn, White creates a permanent weak square on a3 that can never be defended by a "
     "pawn again, while simultaneously leaving the c3-pawn permanently unsupported against Black’s d4-pawn. The "
     "engine refutation 13...a5 14.bxa5 14...Nc6 15.Nf5 15...dxc3 16.Bxc3 demonstrates how Black can immediately "
     "capitalize on these structural concessions. White should have instead favored 13.cxd4, with the "
     "engine-preferred line continuing 13...exd4 14.Be2 14...Bxg3 15.hxg3 15...c5."),
    ("scandinavian_h4_blunder", "The move 21.h4 is a significant strategic error that thins the pawn cover around "
     "White’s king, causing a 22.9% drop in win probability. The opponent immediately exploits this structural "
     "concession with the refutation 21...Nxf3 22.Kxf3 h5 23.Kg2 g6 24.Nh6+, putting immediate pressure on the white "
     "monarch. Instead, White should have maintained stability with the engine's preference of 21.Bd1, leading to "
     "the sequence 21...a6 22.Bb3 Ne6 23.a5 h5."),
    ("fools_mate_g4_blunder", "The move 2.g4 is a catastrophic blunder that immediately concedes the h3 square and "
     "compromises the kingside integrity. The engine refutation 2...Qh4# demonstrates the lethal vulnerability "
     "created by this structural commitment, as the king is left defenseless against an immediate checkmate. While "
     "2.e4 is the preferred development move, the played line ignores basic king safety and results in an instant "
     "loss of the game."),
    ("synthetic_endgame_zugzwang", "This move is a critical error that severely compromises king safety, triggering a "
     "substantial drop in winning prospects. The engine's refutation demonstrates how White seizes the initiative "
     "through 46. Kf6, followed by 46... Kh6, and 47. Kf7, effectively centralizing the king to restrict Black's "
     "position. This lapse in endgame technique allows White to gain the opposition and dictate the activity of the kings."),
    ("fools_mate_g4_blunder", "Move 2.g4 (White): WDL drop 34.1%\nIt weakens h3. Mate follows."),  # header
    ("fools_mate_g4_blunder", "It weakens h3 badly."),                                              # 1 sentence
    ("fools_mate_g4_blunder", "One. Two. Three. Four. Five on h3."),                                # 5 sentences
    ("fools_mate_g4_blunder", "It weakens h3. It also loses material at once."),                    # forbidden
    ("fools_mate_g4_blunder", "It weakens the kingside. Mate follows at once."),                    # no square
    ("scandinavian_h4_blunder", "The pawn push was slow. Black's knights take over."),              # no king token
    ("synthetic_opening_center_abandonment", "It abandons the centre. The structure suffers."),     # 'none' theme
    ("synthetic_opening_center_abandonment", "It abandons the centre. The engine prefers another plan."),
]


@pytest.mark.parametrize("name,narration", CORPUS)
def test_gate_failures_matches_the_legacy_asserts(name, narration):
    case = next(c for c in load_eval_cases() if c["name"] == name)
    prompt = format_flag_for_llm(case["input"]["flagged_move"], 1, 1800)
    try:
        _legacy_gate(case, narration, prompt)
        legacy = None
    except AssertionError as e:
        legacy = str(e).split("\nassert ")[0]
    failures = gate_failures(case, narration, prompt)
    # pytest's assert rewriting indents continuation lines of a message; compare without it
    norm = lambda m: m and "\n".join(line.strip() for line in m.splitlines())
    assert norm(failures[0] if failures else None) == norm(legacy)
