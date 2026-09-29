"""M13 / D12: the A/B comparison harness, offline (models mocked)."""
import asyncio
import json

import pytest


@pytest.fixture
def compare(monkeypatch):
    # The script pins these at import; register them so teardown restores the originals
    for key in ("NARRATOR_MODEL", "PROPHYLAX_CERTIFY_NARRATOR", "JUDGE_MODEL", "PROPHYLAX_NARRATION_INPUT"):
        monkeypatch.setenv(key, "x")
    import evals.compare_narration as cmp
    monkeypatch.setenv("NARRATOR_MODEL", cmp.NARRATOR)
    return cmp


def test_schedule_pairs_and_alternates(compare):
    order = compare.schedule(["a", "b"], 2)
    assert order == [(0, 0, "flag"), (0, 0, "claims"), (0, 1, "claims"), (0, 1, "flag"),
                     (1, 0, "flag"), (1, 0, "claims"), (1, 1, "claims"), (1, 1, "flag")]


def M(a=0, b=0, c=0, d=20, e=0, f=25, invalid=0, concept=0):
    return {"M-a first_attempt_rejections": a, "M-b retries": b, "M-c fallbacks": c, "M-d judge_passes": d,
            "M-e concept_violations_first_attempt": e, "M-f gate_passes": f,
            "final_invalid": invalid, "final_concept_violations": concept}


@pytest.mark.parametrize("flag,claims,outcome", [
    (M(a=4), M(a=2), "recommend_switch"),                  # 2-run improvement, nothing worse
    (M(d=20), M(d=22), "recommend_switch"),
    (M(a=4), M(a=3), "mixed_stop_for_operator_review"),     # 1 run is within tolerance: no improvement
    (M(a=4, d=20), M(a=1, d=18), "mixed_stop_for_operator_review"),  # improves one, regresses another
    (M(), M(invalid=1), "do_not_switch"),                   # hard requirement: a final narration invalid
    (M(), M(concept=1), "do_not_switch"),
    (M(c=0), M(c=2), "do_not_switch"),                      # fallbacks worse beyond tolerance
    (M(f=25), M(f=23), "do_not_switch"),                    # gate passes worse beyond tolerance
    (M(a=4, c=0, f=25), M(a=2, c=1, f=24), "recommend_switch"),  # hard limits sit at exactly 1 run
])
def test_decision_rule(compare, flag, claims, outcome):
    assert compare.decide(flag, claims)["outcome"] == outcome


def test_harness_end_to_end_with_mocked_models(compare, tmp_path, monkeypatch):
    """Every trial goes through _narrate; a 503 reruns the trial and is only logged."""
    from google.genai import errors
    import app.agent
    calls = {"n": 0}

    async def fake_invoke(self, agent, prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})
        assert agent.generate_content_config.seed is not None
        assert agent.generate_content_config.temperature == 1.0
        return "The engine disliked this move. Its preferred line keeps the balance."

    async def fake_judge(flag, narration, cfg, ground_truth=None):
        return {"a": 2, "b": 2, "c": 2, "d": 2, "reason": "ok"}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(app.agent.CoachingAgent, "_invoke_agent", fake_invoke)
    monkeypatch.setattr(compare.judge_mod, "judge_narration", fake_judge)
    monkeypatch.setattr(compare.asyncio, "sleep", no_sleep)
    out = tmp_path / "compare.json"
    report = asyncio.run(compare.main_async(1, str(out)))

    assert len(report["trials"]) == 18 and json.loads(out.read_text())["metrics"]
    assert len(report["infrastructure_events"]) == 1  # the 503: logged, trial rerun
    assert report["infrastructure_events"][0]["events"][0]["status"] == 503
    for arm in ("flag", "claims"):
        m = report["metrics"][arm]
        assert m["trials"] == 9 and m["M-d judge_passes"] == 9
    assert report["config"]["prompt_version"]["flag"] != report["config"]["prompt_version"]["claims"]
    pairs = [(t["case"], t["run"]) for t in report["trials"]]
    assert pairs[0::2] == pairs[1::2]  # both arms of a pair run back to back
    assert all(t["seed"] == u["seed"] for t, u in zip(report["trials"][0::2], report["trials"][1::2]))
