"""M13 / D12: controlled A/B comparison of the flag and claims narration inputs.

Protocol (PHASE_B_PLAN.md, M13, fixed before any run):
- the 9 narration eval cases x RUNS runs x 2 arms; each (case, run) pair runs both arms
  back to back, alternating which goes first; both arms of a pair share one seed
- one pinned narrator and judge, pinned generation settings, no narrator fallback
- a 429/503 is recorded as an infrastructure event and the whole trial is rerun with
  the same configuration; it never counts toward a metric
- D13: the judge gets, in both arms of a pair, the same frozen ground truth: the
  canonical claims and their evidence serialized neutrally (judge_ground_truth), built
  before either narration; the flag payload (scores included) and rubric are unchanged
- each trial goes through CoachingAgent._narrate, the production prompt / validate /
  retry / fallback path, with PROPHYLAX_NARRATION_INPUT set to the arm

Usage (free tier; ~110-130 model calls):
    set -a; . app/.env; set +a
    STOCKFISH_PATH=/opt/homebrew/bin/stockfish STOCKFISH_NODES=1000000 \\
        ./venv/bin/python -m evals.compare_narration [--runs 3] [--out artifacts/narration_compare.json]
"""
import argparse
import asyncio
import hashlib
import inspect
import json
import os
import sys
import time

NARRATOR = "gemini-3.1-flash-lite"
JUDGE = "groq/openai/gpt-oss-120b"
# Pinned before app/judge modules import them: no fallback (certification mode), fixed models
os.environ["NARRATOR_MODEL"] = NARRATOR
os.environ["PROPHYLAX_CERTIFY_NARRATOR"] = NARRATOR
os.environ["JUDGE_MODEL"] = JUDGE

from google.adk.agents import Agent  # noqa: E402
from google.genai import types  # noqa: E402

import evals.judge as judge_mod  # noqa: E402
from app.agent import CoachingAgent, _model_unavailable, _retry_delay, _status  # noqa: E402
from config.settings import resolve_model  # noqa: E402
from domain.claims import build_claims  # noqa: E402
from domain.convert import move_analysis_from_flag  # noqa: E402
from evals.validate_narration import claims_ground, concept_violation, narration_violation  # noqa: E402
from scripts.format_narration import format_claims_for_llm, format_flag_for_llm  # noqa: E402
from tests.test_narration import gate_failures, get_matching_game, load_eval_cases  # noqa: E402

ARMS = ("flag", "claims")
TOLERANCE = 1  # runs out of 27 (D12)
MAX_INFRA_RESTARTS = 8

NARRATOR_SETTINGS = dict(temperature=1.0, top_p=0.95, top_k=64, max_output_tokens=2048)
NARRATOR_THINKING = "low"
JUDGE_SETTINGS = dict(temperature=0.0, top_p=1.0, max_output_tokens=4096)


class InfraFailure(Exception):
    pass


def prompt_version() -> dict:
    """Hashes of everything that shapes each arm's prompt and instruction."""
    import app.agent
    import domain.claims
    import scripts.format_narration as fmt
    skills = os.path.join(os.path.dirname(app.agent.__file__), "..", ".agents", "skills")
    read = lambda *p: open(os.path.join(skills, *p)).read()
    common = read("narration_contract.md") + "".join(
        read(d, "SKILL.md") for d in ("opening_prep", "middlegame_analysis", "endgame_analysis"))
    h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:16]
    return {
        "flag": h(common + inspect.getsource(fmt.format_flag_for_llm)),
        "claims": h(common + read("narration_contract_claims.md") + inspect.getsource(fmt.format_claims_for_llm)
                    + inspect.getsource(fmt.claim_sentence) + inspect.getsource(domain.claims)),
    }


def case_inputs(case):
    """(flag, game, move analysis, claims) for one eval case; board evidence when the
    case comes from a fixture game."""
    flag = case["input"]["flagged_move"]
    game = get_matching_game(flag)
    fen = None
    if game is not None:
        board = game.board()
        for m in game.mainline_moves():
            if board.fullmove_number == flag["move_number"] and board.san(m) == flag["move_san"]:
                fen = board.fen()
                break
            board.push(m)
    move = move_analysis_from_flag(flag, fen)
    return flag, game, move, build_claims(move, game_id=case["name"])


def _value(v) -> str:
    if isinstance(v, (tuple, list)):
        return ", ".join(map(str, v)) or "none"
    return str(v)


def judge_ground_truth(claims) -> str:
    """D13: the claims as neutral structured data, not the sentences the claims arm saw.
    Deterministic: built from the flag/claim/board pipeline only, never from a narration."""
    lines = ["VERIFIED CLAIMS"]
    for c in claims:
        lines += [f"- type: {c.type}", f"  move: {c.move_number}{'.' if c.side == 'white' else '...'}{c.move_san}",
                  f"  subject: {c.subject or 'none'}", "  evidence:"]
        lines += [f"    {e.fact}: {_value(e.value)} (source: {e.provenance})" for e in c.evidence]
    return "\n".join(lines)


async def run_trial(coach, case, arm, seed, infra_log, ground_truth):
    """One arm of one (case, run). Returns the trial record; raises InfraFailure."""
    flag, game, move, claims = case_inputs(case)
    cfg = case["input"]["config"]
    depth, rating = cfg.get("explanation_depth", 1), cfg.get("audience_rating", 1800)
    calls = []

    async def pinned_sub_agent(agent, prompt):
        pinned = Agent(name=agent.name, model=resolve_model(NARRATOR), instruction=agent.instruction,
                       generate_content_config=types.GenerateContentConfig(
                           **NARRATOR_SETTINGS, seed=seed,
                           thinking_config=types.ThinkingConfig(thinking_level=NARRATOR_THINKING)))
        try:
            text = await coach._invoke_agent(pinned, prompt)
        except Exception as e:
            if not _model_unavailable(e):
                raise
            infra_log.append({"model": NARRATOR, "status": _status(e), "retry_after_s": _retry_delay(e)})
            raise InfraFailure(e) from e
        calls.append({"prompt": prompt, "response": text})
        return text

    object.__setattr__(coach, "_run_sub_agent", pinned_sub_agent)
    os.environ["PROPHYLAX_NARRATION_INPUT"] = arm
    stats = {"attempted": 1, "passed_first": 0, "passed_retry": 0, "fallback": 0}
    final = await coach._narrate(dict(flag), game, depth, rating, stats, move=move, game_id=case["name"])

    first = calls[0]["response"]
    if arm == "claims":
        prompt = format_claims_for_llm(flag, claims, depth, rating)
        enforced = lambda t: narration_violation(t, claims_ground(claims), game) or concept_violation(t, claims)
    else:
        prompt = format_flag_for_llm(flag, depth, rating)
        enforced = lambda t: narration_violation(t, flag, game)
    assert calls[0]["prompt"] == prompt  # the trial saw exactly the arm's prompt

    scores = await judge(flag, final, cfg, infra_log, ground_truth)
    return {
        "first_narration": first, "first_violation": enforced(first),
        "first_concept_violation": concept_violation(first, claims),  # measured on both arms (M-e)
        "retried": len(calls) > 1, "fallback": stats["fallback"] == 1,
        "final_narration": final, "final_violation": enforced(final),
        "final_concept_violation": concept_violation(final, claims),
        "gate_failures": gate_failures(case, final, prompt),
        "judge_ground_truth_sha": hashlib.sha256(ground_truth.encode()).hexdigest()[:16],
        "judge": scores, "judge_pass": judge_mod.evaluate_judge_result(scores),
    }


async def judge(flag, narration, cfg, infra_log, ground_truth):
    try:
        scores = await judge_mod.judge_narration(flag, narration, cfg, ground_truth=ground_truth)
    except Exception as e:
        if not _model_unavailable(e):
            raise
        infra_log.append({"model": JUDGE, "status": _status(e), "retry_after_s": _retry_delay(e)})
        raise InfraFailure(e) from e
    if str(scores.get("reason", "")).startswith("Parsing failed"):
        infra_log.append({"model": JUDGE, "status": "unparseable", "retry_after_s": None})
        raise InfraFailure("judge output was not JSON")
    return scores


def metrics(trials, arm):
    rows = [t for t in trials if t["arm"] == arm]
    return {
        "trials": len(rows),
        "M-a first_attempt_rejections": sum(t["first_violation"] is not None for t in rows),
        "M-b retries": sum(t["retried"] for t in rows),
        "M-c fallbacks": sum(t["fallback"] for t in rows),
        "M-d judge_passes": sum(t["judge_pass"] for t in rows),
        "M-e concept_violations_first_attempt": sum(t["first_concept_violation"] is not None for t in rows),
        "M-f gate_passes": sum(not t["gate_failures"] for t in rows),
        "final_invalid": sum(t["final_violation"] is not None for t in rows),
        "final_concept_violations": sum(t["final_concept_violation"] is not None for t in rows),
        # recorded, not part of the D12 rule: shows e.g. a D8 theme loss that stays above the pass mark
        "judge_subscore_totals": {k: sum(t["judge"].get(k, 0) for t in rows) for k in "abcd"},
    }


def decide(f, c):
    """The D12 rule, as written in PHASE_B_PLAN.md before any run."""
    hard = (c["final_invalid"] == 0 and c["final_concept_violations"] == 0
            and c["M-c fallbacks"] - f["M-c fallbacks"] <= TOLERANCE
            and f["M-f gate_passes"] - c["M-f gate_passes"] <= TOLERANCE)
    regressions = {
        "M-a": c["M-a first_attempt_rejections"] - f["M-a first_attempt_rejections"] > TOLERANCE,
        "M-b": c["M-b retries"] - f["M-b retries"] > TOLERANCE,
        "M-d": f["M-d judge_passes"] - c["M-d judge_passes"] > TOLERANCE,
        "M-e": c["M-e concept_violations_first_attempt"] - f["M-e concept_violations_first_attempt"] > TOLERANCE,
    }
    improvements = {
        "M-a": f["M-a first_attempt_rejections"] - c["M-a first_attempt_rejections"] > TOLERANCE,
        "M-d": c["M-d judge_passes"] - f["M-d judge_passes"] > TOLERANCE,
        "M-e": f["M-e concept_violations_first_attempt"] - c["M-e concept_violations_first_attempt"] > TOLERANCE,
    }
    if not hard:
        outcome = "do_not_switch"
    elif not any(regressions.values()) and any(improvements.values()):
        outcome = "recommend_switch"
    else:
        outcome = "mixed_stop_for_operator_review"
    return {"hard_requirements_hold": hard, "regressions_beyond_tolerance": regressions,
            "improvements_beyond_tolerance": improvements, "outcome": outcome}


def schedule(cases, runs):
    """(case index, run, arm) in paired, alternating order."""
    order, k = [], 0
    for i in range(len(cases)):
        for r in range(runs):
            arms = ARMS if k % 2 == 0 else ARMS[::-1]
            order += [(i, r, arm) for arm in arms]
            k += 1
    return order


async def main_async(runs: int, out: str) -> dict:
    judge_mod.judge_agent = Agent(name=judge_mod.judge_agent.name, model=resolve_model(JUDGE),
                                  instruction=judge_mod.judge_agent.instruction,
                                  generate_content_config=types.GenerateContentConfig(**JUDGE_SETTINGS))
    cases = load_eval_cases()
    coach = CoachingAgent(name="compare")
    report = {
        "config": {
            "narrator_model": NARRATOR, "judge_model": JUDGE, "narrator_fallback": "disabled",
            "narrator_settings": {**NARRATOR_SETTINGS, "thinking_level": NARRATOR_THINKING,
                                  "seed": "shared per (case, run): case_index * 10 + run"},
            "judge_settings": {**JUDGE_SETTINGS, "seed": "not applied (ADK LiteLlm does not forward seed)"},
            "prompt_version": prompt_version(), "runs_per_case": runs, "tolerance": TOLERANCE,
            "cases": [c["name"] for c in cases],
        },
        "trials": [], "infrastructure_events": [],
    }
    # D13: frozen before any narration is generated; both arms of every pair get these bytes
    truths = [judge_ground_truth(case_inputs(c)[3]) for c in cases]
    report["config"]["judge_ground_truths"] = dict(zip(report["config"]["cases"], truths))
    start = time.time()
    for i, r, arm in schedule(cases, runs):
        case, seed = cases[i], i * 10 + r
        for attempt in range(MAX_INFRA_RESTARTS + 1):
            infra = []
            try:
                record = await run_trial(coach, case, arm, seed, infra, truths[i])
                break
            except InfraFailure:
                wait = next((e["retry_after_s"] for e in infra if e["retry_after_s"]), None) or min(60, 10 * 2 ** attempt)
                report["infrastructure_events"].append({"case": case["name"], "run": r, "arm": arm,
                                                        "attempt": attempt, "events": infra, "waited_s": wait})
                print(f"infra: {case['name']} run {r} {arm}: {infra[-1]}; rerunning in {wait:g}s", file=sys.stderr)
                await asyncio.sleep(wait)
        else:
            raise SystemExit(f"{case['name']} run {r} {arm}: infrastructure failures exhausted the restarts")
        report["trials"].append({"case": case["name"], "run": r, "arm": arm, "seed": seed, **record})
        print(f"{len(report['trials']):3d} {case['name']:42s} run {r} {arm:6s} "
              f"first={'ok' if record['first_violation'] is None else 'REJ'} "
              f"fallback={record['fallback']} gate={'ok' if not record['gate_failures'] else 'FAIL'} "
              f"judge={'pass' if record['judge_pass'] else 'fail'}", file=sys.stderr)
        with open(out, "w") as fh:  # saved after every trial
            json.dump(report, fh, indent=1)
    report["metrics"] = {arm: metrics(report["trials"], arm) for arm in ARMS}
    report["decision"] = decide(report["metrics"]["flag"], report["metrics"]["claims"])
    report["wall_clock_s"] = round(time.time() - start, 1)
    with open(out, "w") as fh:
        json.dump(report, fh, indent=1)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default="artifacts/narration_compare.json")
    args = ap.parse_args(argv)
    report = asyncio.run(main_async(args.runs, args.out))
    print(json.dumps({"metrics": report["metrics"], "decision": report["decision"],
                      "infrastructure_events": len(report["infrastructure_events"]),
                      "wall_clock_s": report["wall_clock_s"], "artifact": args.out}, indent=1))


if __name__ == "__main__":
    main()
