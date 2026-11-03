"""Builds the experiment report from frozen artifacts. The original 18 positions are the
development/tuning set and the 24 new ones the held-out evaluation set: they appear side by side
and their metrics are never added together. Sections needing human judgement are marked.

Usage: ./venv/bin/python -m evals.queen_heldout.generate_report [--template] [--out FILE]"""
import argparse
import json
import os

from evals.queen_extraction import evaluate, load_gold

HERE = os.path.dirname(__file__)
PENDING = "_pending_"
CONTAMINATION = [
    "The grammar's author chose the rules that selected the suite, though not the positions themselves.",
    "If the labeller also wrote the grammar, labels may lean toward what it can read. The development labels did: an audit moved 12 AMBIGUOUS and 3 UNSUPPORTED fact labels and 2 AMBIGUOUS variation labels to extractable, and exact fact matches there fell from 83/83 to 83/106.",
    "Half the suite comes from real games and half from self-play. The self-play half came from v1 of the suite, which is also where the only outputs obtained before v2 (h01, h02) came from. Those two were not read before predictions froze.",
    "All outputs come from one model revision, one seed and one template. Results do not cover other models, prompts or formats.",
    "One labeller and no inter-rater measure, unless the report says otherwise.",
    "Self-play positions at 50k nodes may differ in style from the positions QUEEN was trained on.",
]


def fmt(value):
    if value is None:
        return PENDING
    if isinstance(value, float):
        return f"{value:.1%}"
    return str(value)


def ratio(n, d):
    return PENDING if n is None or not d else f"{n} / {d}"


def dev_summary() -> dict:
    r = evaluate()
    e, v = r["extraction"], r["verification"]
    gold = load_gold()
    variation_rows = [g for g in gold if g.get("kind") == "variation"]
    false_rows = [g for g in gold if g.get("status") == "EXTRACTED" and g.get("verdict") == "FALSE"]
    return {"cases": 18, "labels": len(gold), "facts": ratio(e.get("exact_match", 0), e.get("facts_labelled", 0)),
            "board": ratio(e.get("board_match", 0), e.get("exact_match", 0)),
            "unsupported": ratio(e.get("unsupported_detected", 0), e.get("unsupported_labelled", 0)),
            "ambiguous": ratio(e.get("ambiguous_detected", 0), e.get("ambiguous_labelled", 0)),
            "variations": ratio(e.get("variations_exact", 0), e.get("variations_labelled", 0)),
            "false_verified": v.get("false_verification_acceptance", 0),
            "false_rows_caught": ratio(v.get("label_FALSE_got_FALSE", 0), len(false_rows)),
            "variation_rows": len(variation_rows)}


def _examples(details, key, n=5):
    rows = details.get(key, [])[:n]
    return [f"- `{r.get('case')}` «{r.get('text', '')}» {json.dumps({k: v for k, v in r.items() if k not in ('case', 'text')}, ensure_ascii=False)}" for r in rows] or ["- none"]


def render(scores, manifest, suite, miss_review=()) -> str:
    done = scores is not None
    s = scores or {}
    ex, ver, e2e, var, vp = (s.get(k, {}) for k in ("extraction", "verification", "end_to_end_facts", "variations", "verified_precision"))
    meta, details = s.get("meta", {}), s.get("details", {})
    dev = dev_summary()
    m = manifest or {}
    settings = m.get("generation_settings", {})
    categories = {}
    for c in suite["cases"]:
        categories[c["category"]] = categories.get(c["category"], 0) + 1
    real = sum("player history" in c.get("provenance", "") for c in suite["cases"])
    title = "# QUEEN extraction: blind held-out evaluation"
    if not done:
        title += "\n\n**TEMPLATE. No held-out results exist yet.**"
    elif meta.get("draft"):
        title += "\n\n**DRAFT. Labels are not frozen; do not report these numbers.**"
    lines = [title, "", "## Frozen system", "",
             f"- Frozen commit: `{m.get('frozen_commit', '046bb19')}`",
             f"- Extractor commit: `{m.get('extractor_commit', PENDING)}`", f"- Verifier commit: `{m.get('verifier_commit', PENDING)}`",
             f"- Semantic-code override: {m.get('semantic_override') or 'none'}", f"- python-chess: {m.get('python_chess', PENDING)}", "",
             "## Sets (never combined)", "",
             f"- **Development/tuning set:** 18 positions, {dev['labels']} labels. The grammar was written while reading these, so its numbers are optimistic.",
             f"- **Held-out evaluation set:** {len(suite['cases'])} positions: " + ", ".join(f"{v} {k}" for k, v in categories.items()) +
             f"; {real} from real games, {len(suite['cases']) - real} from self-play.",
             f"- Complete suite: {m.get('complete_suite', PENDING)}; missing: {m.get('missing_cases', PENDING)}", "",
             "## QUEEN", "",
             f"- Model: {m.get('model', PENDING)}", f"- Settings: {json.dumps({k: settings[k] for k in ('max_tokens', 'temperature', 'seed') if k in settings}) or PENDING}",
             "- Revisions: " + (", ".join(sorted({f"Space {r.get('space_sha')} / model {r.get('model_sha')}" for r in m.get("cases", [])})) or PENDING), "",
             "### Raw output hashes", "", "| Case | Category | Provenance | Explanation SHA-256 | Prediction SHA-256 |", "|---|---|---|---|---|"]
    lines += [f"| {r['case']} | {r['category']} | {r.get('provenance')} | `{r['raw_explanation_sha256']}` | `{r['prediction_sha256']}` |" for r in m.get("cases", [])] or [f"| {PENDING} | | | | |"]
    lines += ["", "## Labels", "",
              f"- Labels: {meta.get('labels', PENDING)}; corrections logged: {meta.get('corrections', PENDING)}",
              f"- Labeller: {meta.get('labeller') or PENDING}; independent of the grammar's author: {meta.get('independent_of_grammar_author', PENDING)}",
              f"- Predictions opened before labels froze: {meta.get('predictions_opened_before_labels_frozen', PENDING)}", "",
              "## Development vs held-out", "",
              "Rates on different sets, side by side. The development column uses the audited labels.", "",
              "| Metric | Development/tuning set | Held-out evaluation set |", "|---|---|---|",
              f"| Exact predicate + args | {dev['facts']} | {ratio(ex.get('predicate_and_args_match'), ex.get('facts_labelled'))} |",
              f"| Correct board, of those | {dev['board']} | {ratio(ex.get('board_match'), ex.get('predicate_and_args_match'))} |",
              f"| Unsupported detected | {dev['unsupported']} | {ratio(ex.get('unsupported_detected'), ex.get('unsupported_labelled'))} |",
              f"| Ambiguous detected | {dev['ambiguous']} | {ratio(ex.get('ambiguous_detected'), ex.get('ambiguous_labelled'))} |",
              f"| Variations exact | {dev['variations']} | {ratio(var.get('exact'), var.get('labelled'))} |",
              f"| False VERIFIED | {dev['false_verified']} | {fmt(ver.get('false_verified'))} |", "",
              "## Extraction", "", "| Metric | Value |", "|---|---|"]
    for key, label in (("predicate_accuracy", "Exact predicate"), ("args_accuracy_given_predicate", "Exact args, given predicate"),
                       ("predicate_and_args_accuracy", "Exact predicate + args"), ("board_binding_accuracy", "Correct board, given exact"),
                       ("unsupported_recall", "Unsupported classification"), ("ambiguous_recall", "Ambiguous classification"),
                       ("missed", "Missed extractable claims"), ("hallucinated_on_non_fact", "Extractions on labelled non-facts"),
                       ("extraction_with_no_label", "Extractions matching no label")):
        lines.append(f"| {label} | {fmt(ex.get(key))} |")
    lines.append(f"| Variation exact-match rate | {fmt(var.get('exact_match_rate'))} |")
    lines += ["", "## Verification (on correctly extracted claims)", "", "| Metric | Value |", "|---|---|"]
    for key, label in (("VERIFIED", "VERIFIED"), ("FALSE", "FALSE"), ("UNKNOWN", "UNKNOWN"), ("verdict_accuracy", "Verdict accuracy"),
                       ("false_verified", "False VERIFIED"), ("false_verification_acceptance_rate", "False verification acceptance rate")):
        lines.append(f"| {label} | {fmt(ver.get(key, 0 if done and key in ('VERIFIED', 'FALSE', 'UNKNOWN') else None))} |")
    lines += ["", "## End-to-end error capture", "",
              "Of all false concrete QUEEN claims in the labels, including those no predicate can represent. A verifier cannot catch what the extractor never emits.", "",
              "| Outcome | Facts | Variations |", "|---|---|---|",
              f"| All false | {fmt(e2e.get('false_claims'))} | {fmt(var.get('false_lines'))} |",
              f"| Caught as FALSE | {fmt(e2e.get('false_caught'))} | {fmt(var.get('false_caught'))} |",
              f"| Became UNKNOWN | {fmt(e2e.get('false_unknown'))} | {fmt(var.get('false_became_unknown'))} |",
              f"| Missed by extraction | {fmt(e2e.get('false_missed'))} | {fmt(var.get('false_missed'))} |",
              f"| Incorrectly VERIFIED | {fmt(e2e.get('false_incorrectly_verified'))} | {fmt(var.get('false_incorrectly_verified'))} |",
              f"| **Capture rate** | **{fmt(e2e.get('false_claim_capture_rate'))}** | **{fmt(var.get('false_variation_capture_rate'))}** |",
              f"| Capture rate, representable claims only | {fmt(e2e.get('false_claim_capture_rate_representable'))} | n/a |", "",
              f"False claims outside every predicate: {fmt(e2e.get('false_outside_predicates'))}. FALSE on the wrong board, not counted as caught: {fmt(e2e.get('false_found_on_wrong_board'))}.", "",
              "## Verified-claim precision", "", "The critical safety metric: of everything the system marks VERIFIED, how much is true.", "",
              "| | Facts | Variations |", "|---|---|---|",
              f"| Marked VERIFIED | {fmt(vp.get('facts_verified'))} | {fmt(vp.get('variations_verified'))} |",
              f"| Actually true | {fmt(vp.get('facts_true'))} | {fmt(vp.get('variations_true'))} |",
              f"| **Actually false** | **{fmt(vp.get('facts_false'))}** | **{fmt(vp.get('variations_false'))}** |",
              f"| Not covered by a label (counted against precision) | {fmt(vp.get('facts_unlabelled'))} | {fmt(vp.get('variations_unlabelled'))} |",
              f"| Precision | {fmt(vp.get('fact_precision'))} | {fmt(vp.get('variation_precision'))} |", "",
              "## Failure taxonomy", "", "| Failure | Layer | Count |", "|---|---|---|"]
    layers = {"verifier_disagrees_with_label": "verifier", "variation_verifier_disagrees_with_label": "verifier", "variation_failure_ply_differs": "verifier"}
    tax = s.get("failure_taxonomy", {})
    lines += [f"| {k} | {layers.get(k, 'extraction')} | {v} |" for k, v in sorted(tax.items(), key=lambda kv: (-kv[1], kv[0]))] or [f"| {PENDING if not done else 'none'} | | |"]
    known = dict(s.get("known_issues", {}))  # tagged after scoring: a blind labeller never sees these names
    for r in miss_review:
        if r.get("known_issue"):
            known[r["known_issue"]] = known.get(r["known_issue"], 0) + 1
    lines += ["", "### Known issues left unfixed", "", "| Issue | Labels affected |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(known.items())] or [f"| {PENDING if not done else 'none tagged'} | |"]
    review = {}
    for r in miss_review:
        review[r["miss_class"]] = review.get(r["miss_class"], 0) + 1
    lines += ["", "### What it would take to address the misses", "", "From `miss_review.jsonl`, written after scoring.", "",
              f"- Deterministically addressable: {review.get('deterministic_grammar', PENDING)}",
              f"- Needs a new chess predicate: {review.get('needs_predicate', PENDING)}",
              f"- Fundamentally ambiguous language: {review.get('ambiguous_language', PENDING)}", "",
              "## Examples", "", "### False VERIFIED (highest severity)", ""]
    lines += _examples(details, "FALSE_VERIFIED") + _examples(details, "FALSE_VERIFIED_VARIATION") if done else [PENDING]
    lines += ["", "### Missed extractable claims", ""] + (_examples(details, "extractable_claim_missed") if done else [PENDING])
    lines += ["", "### Wrong board", ""] + (_examples(details, "board_binding_wrong") if done else [PENDING])
    lines += ["", "### Extractions matching no label", ""] + (_examples(details, "extraction_with_no_label") if done else [PENDING])
    lines += ["", "### Successful generalization", "", "_Analyst: quote correct extraction + FALSE and + VERIFIED from the predictions._",
              "", "## Claims that stay unsupported", "", "_Analyst: list from the UNSUPPORTED labels by category._", "",
              "## Limitations and contamination risks", ""] + [f"- {c}" for c in CONTAMINATION]
    return "\n".join(lines) + "\n"


def load_optional(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", action="store_true", help="render with no held-out results")
    parser.add_argument("--out", default=os.path.join(HERE, "REPORT.md"))
    args = parser.parse_args(argv)
    suite = load_optional(os.path.join(HERE, "suite.json"))
    scores = None if args.template else load_optional(os.path.join(HERE, "scores.json"))
    manifest = None if args.template else load_optional(os.path.join(HERE, "predictions", "MANIFEST.json"))
    review_path = os.path.join(HERE, "miss_review.jsonl")
    review = []
    if os.path.exists(review_path):
        with open(review_path, encoding="utf-8") as f:
            review = [json.loads(line) for line in f if line.strip()]
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(scores, manifest, suite, review))
    print(f"wrote {args.out}" + (" (template)" if scores is None else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
