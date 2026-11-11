"""Extractor V2.1 evaluation. Replays V2's recorded answers through the hybrid: this module never calls a
model, so it cannot spend quota or touch a recording. Frozen V1 artifacts are only read; results go to
evals/queen_v2_1.

  dev      the set the hybrid may be iterated on
  compare  POST-HOC, once, after the hybrid's decisions are frozen on dev. Those 24 outputs were read
           before this design existed and shaped it, so the numbers say nothing about generalization.

Usage:
  uv run python -m evals.queen_extraction_v2_1 dev
  uv run python -m evals.queen_extraction_v2_1 compare"""
import argparse
import json
import os

from domain import extract_v2_1 as h
from domain.extract_v2 import DEFAULT_MODEL
from domain.verify import verify_fact
from evals import queen_extraction as qe
from evals import queen_extraction_v2 as q2
from evals.queen_heldout import score as sc

OUT = os.path.join(os.path.dirname(__file__), "queen_v2_1")
RECORDINGS = os.path.join(q2.OUT, "recordings")
POST_HOC = "post-hoc evaluation on previously inspected held-out set"


def judge_hybrid(fen, candidate):
    """V2's judge, plus "no longer": what is denied now must have held before the last move."""
    judged = q2.judge_fact_v2(fen, candidate)
    if judged is None or judged["status"] != "VERIFIED" or getattr(candidate, "held_before", None) is None:
        return judged
    if not candidate.prefix:
        return {**judged, "status": "UNKNOWN", "reason": "no move before this claim to compare with"}
    earlier = verify_fact(q2.board_after(fen, candidate.prefix[:-1]).fen(), candidate.held_before)
    if earlier.status == "FALSE":
        return {**judged, "status": "FALSE", "reason": "it did not hold before the move, so it cannot have stopped"}
    if earlier.status == "UNKNOWN":
        return {**judged, "status": "UNKNOWN", "reason": earlier.reason}
    return judged


def replay(name, model=DEFAULT_MODEL) -> dict:
    return {cid: h.extract_hybrid(text, fen, q2.RecordingBackend(os.path.join(RECORDINGS, name), cid, None, model), cid) for cid, (fen, text) in q2.load_set(name).items()}


def dev_row(result) -> dict:
    e, v = result["extraction"], result["verification"]
    verified = v.get("VERIFIED", 0)
    return {"exact": (e.get("exact_match", 0), e.get("facts_labelled", 0)), "board": (e.get("board_match", 0), e.get("exact_match", 0)),
            "unsupported": (e.get("unsupported_detected", 0), e.get("unsupported_labelled", 0)), "ambiguous": (e.get("ambiguous_detected", 0), e.get("ambiguous_labelled", 0)),
            "false_capture": (v.get("label_FALSE_got_FALSE", 0), sum(n for k, n in v.items() if k.startswith("label_FALSE_got_"))),
            "precision": (v.get("label_VERIFIED_got_VERIFIED", 0), verified), "false_acceptances": v.get("false_verification_acceptance", 0),
            "unlabelled_verified": result.get("unlabelled_verified", 0), "hallucinated": e.get("hallucinated_on_non_claim", 0), "variations": (e.get("variations_exact", 0), e.get("variations_labelled", 0))}


def dev_table() -> dict:
    v1 = qe.evaluate()
    v2 = q2.evaluate_dev(q2.replay("dev", DEFAULT_MODEL))
    hybrid = q2.evaluate_dev(replay("dev"), judge=judge_hybrid)
    return {"V1": dev_row(v1), "V2": dev_row(v2), "V2.1": dev_row(hybrid), "hybrid_details": hybrid["details"], "hybrid_unlabelled": hybrid["unlabelled_candidates"]}


def to_predictions(extractions) -> dict:
    _, frozen = sc.load_predictions(os.path.join(q2.HELDOUT, "predictions"))
    cases = q2.load_set("heldout")
    out = {}
    for cid, (fen, text) in cases.items():
        prediction = q2.to_prediction(cid, fen, text, extractions[cid], frozen[cid]["variations"], judge=judge_hybrid)
        for row, c in zip(prediction["facts"], extractions[cid].facts):
            row.update(produced_by=list(c.produced_by), normalized=c.normalized, conflict=c.conflict, alignment=c.alignment, competing=list(c.competing))
        out[cid] = prediction
    return out


def false_verdicts_unconfirmed(predictions, labels) -> dict:
    """FALSE verdicts no label confirms, which no safety metric counts: the verifier calling something
    false where the label is a TRUE fact on another board, a non-fact, or absent. Each is a possible false alarm."""
    mirrored, mirrored_labels = q2.mirror_negatives(predictions, labels)
    rows = {c: p["facts"] for c, p in mirrored.items()}
    owner = sc.assign_candidates(rows, mirrored_labels)
    tally = {"on_non_fact": 0, "true_fact_other_board": 0, "no_label": 0, "material_rule_on_non_fact": 0}
    for case, facts in rows.items():
        for i, c in enumerate(facts):
            if c["status"] != "EXTRACTED" or c["verifier_status"] != "FALSE":
                continue
            label = owner.get((case, i))
            if label is None:
                tally["no_label"] += 1
            elif label["kind"] == "non_fact":
                tally["on_non_fact"] += 1
                tally["material_rule_on_non_fact"] += c["predicate"] == "material_balance_is"
            elif label["chess_truth"] != "FALSE":
                tally["true_fact_other_board"] += 1
    return tally


def catch_overlap(first, second, third) -> dict:
    return {"v1": len(first), "v2": len(second), "hybrid": len(third), "v1_and_v2": len(first & second), "v1_only_vs_v2": len(first - second), "v2_only_vs_v1": len(second - first),
            "v1_or_v2_union": len(first | second), "in_union_but_lost_by_hybrid": sorted((first | second) - third), "hybrid_new_beyond_union": sorted(third - (first | second)),
            "lost_from_v1": sorted(first - third), "lost_from_v2": sorted(second - third), "all_three": len(first & second & third)}


def report(v1, v2, hybrid, overlap, alarms=None) -> str:
    a, b, c = q2.heldout_metrics(v1), q2.heldout_metrics(v2), q2.heldout_metrics(hybrid)
    rows = [("Exact predicate + args", "exact"), ("Correct board, given exact", "board"), ("Unsupported recall", "unsupported"), ("Ambiguous recall", "ambiguous"), ("Verdict accuracy on correct reads", "verdicts"),
            ("False-claim capture", "false_capture"), ("VERIFIED fact precision", "precision"), ("Variation exact match", "variations"), ("False variation capture", "false_lines")]
    lines = [f"# V1 vs V2 vs V2.1 hybrid: {POST_HOC}", "", f"**This is a {POST_HOC}.** These outputs and labels shaped this design, so the numbers do not measure generalization.", "",
             "| Metric | V1 | V2 | V2.1 hybrid |", "|---|---:|---:|---:|"]
    lines += [f"| {label} | {q2._ratio(*a[k])} | {q2._ratio(*b[k])} | {q2._ratio(*c[k])} |" for label, k in rows]
    for label, k in (("False VERIFIED on correct reads", "false_verified"), ("False claims VERIFIED, including stray candidates", "false_end_to_end_verified"), ("False variations VERIFIED", "false_variation_verified"),
                     ("Extractions on labelled non-facts", "hallucinated_on_non_fact"), ("Extractions matching no label", "no_label")):
        lines.append(f"| {label} | {a[k]} | {b[k]} | {c[k]} |")
    lines += ["", "## False claims caught", "", f"V1 {overlap['v1']}, V2 {overlap['v2']}, measured hybrid {overlap['hybrid']}. V1 and V2 share {overlap['v1_and_v2']}. Union of V1 and V2: {overlap['v1_or_v2_union']}, which is not a system result.", "",
              f"In that union but lost by the hybrid: {overlap['in_union_but_lost_by_hybrid'] or 'none'}. Caught only by the hybrid: {overlap['hybrid_new_beyond_union'] or 'none'}.", ""]
    if alarms:
        lines += ["## FALSE verdicts no label confirms", "", "No metric above counts these. Each is a possible false alarm: the verifier called a reading false, but no FALSE label agrees.", "",
                  "| | V1 | V2 | V2.1 hybrid |", "|---|---:|---:|---:|"]
        lines += [f"| {label} | {alarms['v1'][k]} | {alarms['v2'][k]} | {alarms['hybrid'][k]} |" for label, k in (("On a labelled non-fact", "on_non_fact"), ("Of those, material rules", "material_rule_on_non_fact"), ("On a TRUE fact, other board", "true_fact_other_board"), ("With no label", "no_label"))]
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("dev", "compare"))
    args = parser.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    if args.command == "dev":
        table = dev_table()
        q2.write_json(os.path.join(OUT, "dev_scores.json"), table)
        for key in ("exact", "board", "unsupported", "ambiguous", "false_capture", "precision", "false_acceptances", "unlabelled_verified", "hallucinated", "variations"):
            print(f"{key:20} " + " | ".join(f"{name} {table[name][key]}" for name in ("V1", "V2", "V2.1")))
        return 0
    labels = sc.load_jsonl(os.path.join(q2.HELDOUT, "labels.jsonl"))
    with open(os.path.join(q2.HELDOUT, "scores.json"), encoding="utf-8") as f:
        v1 = json.load(f)
    _, frozen = sc.load_predictions(os.path.join(q2.HELDOUT, "predictions"))
    v2_predictions = q2.heldout_predictions(q2.replay("heldout", DEFAULT_MODEL))
    v2 = q2.score_heldout(v2_predictions, labels)
    predictions = to_predictions(replay("heldout"))
    hybrid = q2.score_heldout(predictions, labels)
    overlap = catch_overlap(q2.caught_false_facts(frozen, labels), q2.caught_false_facts(v2_predictions, labels), q2.caught_false_facts(predictions, labels))
    hybrid["meta"], hybrid["overlap"] = {"set": POST_HOC, "hybrid_version": h.VERSION}, overlap
    q2.write_json(os.path.join(OUT, "heldout_posthoc_scores.json"), hybrid)
    alarms = {"v1": false_verdicts_unconfirmed(frozen, labels), "v2": false_verdicts_unconfirmed(v2_predictions, labels), "hybrid": false_verdicts_unconfirmed(predictions, labels)}
    hybrid["false_verdicts_unconfirmed"] = alarms
    q2.write_json(os.path.join(OUT, "heldout_posthoc_scores.json"), hybrid)
    text = report(v1, v2, hybrid, overlap, alarms)
    with open(os.path.join(OUT, "COMPARISON.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
