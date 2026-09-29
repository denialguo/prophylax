"""M14: the claim benchmark, in two tiers (PHASE_B_PLAN.md, M14).
Tier 1, every case: the facts a label rests on are true on the board / in the payload.
Tier 2, approved cases only: expected claims emitted, forbidden claims absent.
Draft and ambiguous cases are reported next to the builder's output, never asserted."""
import json

import pytest

from evals.claim_metrics import compare, coverage, deterministic_metrics, evidence_mismatches, expressed, load_cases

CASES = load_cases()
IDS = [c["id"] for c in CASES]


def test_case_format():
    assert len(set(IDS)) == len(IDS)
    for c in CASES:
        assert c["status"] in ("draft", "approved", "ambiguous"), c["id"]
        assert (c["question"] is not None) == (c["status"] == "ambiguous"), c["id"]
        assert c["label_rationale"], c["id"]
        if c["status"] == "ambiguous":  # D11: nothing is forced
            assert not c["expected_claims"] and not c["forbidden_claims"], c["id"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_label_evidence_is_true(case):
    assert evidence_mismatches(case) == []


@pytest.mark.parametrize("case", [c for c in CASES if c["status"] == "approved"],
                         ids=[c["id"] for c in CASES if c["status"] == "approved"])
def test_approved_labels_hold(case):
    r = compare(case)
    assert r["unsupported"] is None, r["unsupported"]
    assert not r["missing"], f"expected but not emitted: {sorted(r['missing'], key=str)}"
    assert not r["forbidden_emitted"], f"forbidden but emitted: {sorted(r['forbidden_emitted'], key=str)}"


def test_report_unapproved_and_metrics(capsys):
    """Reported, not asserted: the builder next to each draft/ambiguous label."""
    lines = []
    for c in CASES:
        if c["status"] == "approved":
            continue
        r = compare(c)
        lines.append(f"{c['status']:9s} {c['id']:34s} got={sorted(r['got'], key=str)} "
                     f"missing={sorted(r['missing'], key=str)} forbidden={sorted(r['forbidden_emitted'], key=str)}")
    print("\n".join(lines))
    print(json.dumps(deterministic_metrics([c for c in CASES if c["status"] == "approved"]), indent=1))


def test_mirrors_come_from_the_labels():
    by_id = {c["id"]: c for c in CASES}
    for c in CASES:
        if c.get("mirror_of"):
            orig = by_id[c["mirror_of"]]
            assert [x["subject"] and x["subject"][1] for x in c["expected_claims"]] == \
                   [x["subject"] and str(9 - int(x["subject"][1])) for x in orig["expected_claims"]]


def test_required_claim_coverage(tmp_path):
    claim = {"type": "weak_square_created", "subject": "h3", "required": True}
    ks = {"type": "king_safety_reduced", "subject": None, "required": True}
    assert expressed(claim, "It leaves h3 weak.") and not expressed(claim, "It weakens the kingside.")
    assert not expressed(claim, "The bishop on h31")  # whole squares only
    assert expressed(ks, "The king's shield is gone.") and not expressed(ks, "The king walks.")
    assert expressed(ks, "It thins the pawn cover around White's king.")  # the claims arm's wording
    cases = [{"eval_case": "e1", "expected_claims": [claim, {"type": "weak_square_created", "subject": "a3"}]},
             {"eval_case": "e2", "expected_claims": [ks]}]
    trials = [{"case": "e1", "run": 0, "arm": "flag", "final_narration": "h3 is weak."},
              {"case": "e1", "run": 0, "arm": "claims", "final_narration": "Mate follows."},
              {"case": "e2", "run": 0, "arm": "claims", "final_narration": "King safety drops."}]
    path = tmp_path / "a.json"
    path.write_text(json.dumps({"trials": trials}))
    cov = coverage(cases, str(path))
    assert cov["flag"] == {"expressed": 1, "required": 1, "misses": []}
    assert cov["claims"] == {"expressed": 1, "required": 2, "misses": ["e1 run 0: weak_square_created h3"]}
