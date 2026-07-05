import os
import json
import pytest
from typing import Dict, Any

from evals.judge import judge_narration, evaluate_judge_result

def find_calibration_file() -> str:
    curr = os.path.dirname(os.path.abspath(__file__))
    while curr:
        candidate = os.path.join(curr, "evals", "judge_calibration.json")
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent
    return ""

def load_calibration_cases() -> list:
    path = find_calibration_file()
    if not path:
        return []
    with open(path) as f:
        return json.load(f)

@pytest.mark.anyio
@pytest.mark.judge
@pytest.mark.parametrize("case", load_calibration_cases())
async def test_judge_calibration(case: Dict[str, Any]):
    flagged_move = case["input"]["flagged_move"]
    config = case["input"]["config"]
    narration = case["narration"]
    should_pass = case["should_pass"]
    
    scores = await judge_narration(flagged_move, narration, config, ground_truth=case.get("ground_truth"))
    is_passing = evaluate_judge_result(scores)
    
    print(f"DEBUG: Case: {case['name']} | Scores: {scores} | Passed: {is_passing}")
    
    if should_pass:
        assert is_passing is True, f"Calibration case '{case['name']}' was expected to PASS but FAILED. Scores: {scores}"
    else:
        assert is_passing is False, f"Calibration case '{case['name']}' was expected to FAIL but PASSED. Scores: {scores}"
        assert scores.get("b", 0) == 0, f"Calibration case '{case['name']}' category (b) was expected to be 0. Scores: {scores}"
