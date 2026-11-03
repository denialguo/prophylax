"""Deterministic offline scoring of the frozen held-out predictions against the gold labels.
Extraction, verification and end-to-end capture are reported separately and never blended.
The held-out set is never merged with the development/tuning set.

Usage: ./venv/bin/python -m evals.queen_heldout.score [--draft]"""
import argparse
import collections
import hashlib
import json
import os

import chess
import jsonschema

from domain.verify import PREDICATES

HERE = os.path.dirname(__file__)
TRUTH_TO_STATUS = {"TRUE": "VERIFIED", "FALSE": "FALSE", "UNDETERMINED": "UNKNOWN"}
STRUCTURED = ("best_move", "promising_move")  # single moves from the answer block; not labelled


class ScoreError(Exception):
    pass


def rate(n, d):
    return None if not d else round(n / d, 4)


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_predictions(pred_dir):
    """The snapshot, with every hash re-checked."""
    with open(os.path.join(pred_dir, "MANIFEST.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    predictions = {}
    for row in manifest["cases"]:
        path = os.path.join(pred_dir, row["prediction_file"])
        if sha256_file(path) != row["prediction_sha256"]:
            raise ScoreError(f"{row['prediction_file']} no longer matches the manifest")
        with open(path, encoding="utf-8") as f:
            predictions[row["case"]] = json.load(f)
    return manifest, predictions


def validate_labels(labels, texts, fens, schema) -> list:
    """Problems that make a label unusable. Never compares a label with a prediction."""
    problems, seen = [], set()
    for row in labels:
        lid = row.get("label_id", "?")
        try:
            jsonschema.validate(row, schema)
        except jsonschema.ValidationError as e:
            problems.append(f"{lid}: schema: {e.message}")
            continue
        if lid in seen:
            problems.append(f"{lid}: duplicate id")
        seen.add(lid)
        if row["case"] not in texts:
            problems.append(f"{lid}: unknown case {row['case']}")
            continue
        if texts[row["case"]][row["span_start"]:row["span_end"]] != row["source_text"]:
            problems.append(f"{lid}: source_text is not the text at that span")
        if row["kind"] == "fact":
            if row["predicate"] not in PREDICATES:
                problems.append(f"{lid}: {row['predicate']} is not a predicate")
            if (row["branch"] is None) != (row["chess_truth"] == "UNDETERMINED"):
                problems.append(f"{lid}: chess_truth UNDETERMINED goes with branch null, and only then")
            if row["branch"] is not None:
                board = chess.Board(fens[row["case"]])
                try:
                    for uci in row["branch"]:
                        board.push(board.parse_uci(uci))
                except ValueError as e:
                    problems.append(f"{lid}: branch does not replay: {e}")
                    continue
                if row.get("board_fen") not in (None, board.fen()):
                    problems.append(f"{lid}: board_fen is not the replayed branch")
        elif row["kind"] == "variation" and row["extraction_class"] == "EXTRACTED":
            if (row["legal"] and row["annotations_hold"]) != (row["failure_ply"] is None):
                problems.append(f"{lid}: failure_ply must be set exactly when the line fails")
    return problems


def _overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def args_match(want: dict, got: dict) -> bool:
    """Same required arguments. `piece` and `color` are optional identities in the verifier's API:
    a stated value must agree, but a side that leaves one out is not wrong."""
    optional = ("piece", "color")
    if set(want) - set(optional) != set(got) - set(optional):
        return False
    return all(want[k] == got[k] for k in want if k in got)


def score(predictions, labels):
    """One deterministic pass. A candidate is matched by at most one label."""
    ex, ver, e2e, var = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    tax, details = collections.Counter(), collections.defaultdict(list)
    known = collections.Counter()
    used = set()
    for counter, names in ((ex, ("facts_labelled", "predicate_match", "args_match_given_predicate", "predicate_and_args_match", "board_match",
                                 "missed", "unsupported_labelled", "unsupported_detected", "ambiguous_labelled", "ambiguous_detected",
                                 "hallucinated_on_non_fact", "negative_labelled", "negative_missed", "polarity_flipped")),
                           (ver, ("correctly_extracted", "VERIFIED", "FALSE", "UNKNOWN", "verdict_correct", "not_true_claims", "false_verified")),
                           (e2e, ("false_claims", "false_claims_representable", "false_claims_negative", "false_outside_predicates", "false_caught", "false_unknown", "false_missed",
                                  "false_incorrectly_verified", "false_found_on_wrong_board", "false_caught_by_stray_candidate")),
                           (var, ("labelled", "exact", "missed", "false_lines", "false_caught", "false_became_unknown", "false_missed",
                                  "false_incorrectly_verified", "ambiguous_labelled", "ambiguous_detected", "verified_true", "verified_false", "verified_unlabelled"))):
        counter.update({name: 0 for name in names})  # a zero is a result; report it
    fact_rows = {cid: [f for f in p["facts"]] for cid, p in predictions.items()}
    var_rows = {cid: [v for v in p["variations"] if v["kind"] not in STRUCTURED] for cid, p in predictions.items()}
    labelled_candidate = {}   # (case, index) -> label

    for row in sorted(labels, key=lambda r: r["label_id"]):
        if row["case"] not in predictions:
            continue
        case, candidates = row["case"], fact_rows[row["case"]]
        here = [(i, c) for i, c in enumerate(candidates) if _overlap(row["span_start"], row["span_end"], c["span_start"], c["span_end"])]
        extracted = [(i, c) for i, c in here if c["status"] == "EXTRACTED" and (case, i) not in used]
        if row["kind"] == "fact" and row.get("polarity", "positive") == "negative":
            # Candidates only ever assert that a predicate holds, so a negative claim cannot be
            # matched: it is a missed concrete claim. A candidate for the same fact here says the
            # opposite of the text: it is consumed, and a VERIFIED from it is not a true reading.
            ex["facts_labelled"] += 1
            ex["negative_labelled"] += 1
            ex["missed"] += 1
            ex["negative_missed"] += 1
            if row.get("known_issue"):
                known[row["known_issue"]] += 1
            if row["chess_truth"] == "FALSE":
                e2e["false_claims"] += 1
                e2e["false_claims_representable"] += 1
                e2e["false_claims_negative"] += 1
                e2e["false_missed"] += 1
            flipped = [(i, c) for i, c in extracted if c["predicate"] == row["predicate"] and args_match(row["args"], c["args"])]
            if flipped:
                ex["polarity_flipped"] += 1
                tax["polarity_flipped"] += 1
                details["polarity_flipped"].append(_brief(row, flipped[0][1]))
                used.add((case, flipped[0][0]))
                labelled_candidate[(case, flipped[0][0])] = row
            else:
                tax["negative_claim_missed"] += 1
                details["negative_claim_missed"].append(_brief(row, None))
        elif row["kind"] == "fact":
            ex["facts_labelled"] += 1
            same = [(i, c) for i, c in extracted if c["predicate"] == row["predicate"]]
            exact = [(i, c) for i, c in same if args_match(row["args"], c["args"])]
            ex["predicate_match"] += bool(same)
            ex["args_match_given_predicate"] += bool(exact)
            ex["predicate_and_args_match"] += bool(exact)
            if row.get("known_issue"):
                known[row["known_issue"]] += 1
            want = row["chess_truth"]
            false_claim = want == "FALSE"
            if false_claim:
                e2e["false_claims"] += 1
                e2e["false_claims_representable"] += 1
            if not exact:
                ex["missed"] += 1
                reason = "predicate_right_args_wrong" if same else "extractable_claim_missed"
                tax[reason] += 1
                details[reason].append(_brief(row, same and same[0][1]))
                if false_claim:
                    e2e["false_missed"] += 1
                continue
            best = next(((i, c) for i, c in exact if c["board_moves"] == row["branch"]), exact[0])
            i, c = best
            used.add((case, i))
            labelled_candidate[(case, i)] = row
            right_board = c["board_moves"] == row["branch"]
            ex["board_match"] += right_board
            status = c["verifier_status"]
            if not right_board:
                tax["board_binding_wrong"] += 1
                details["board_binding_wrong"].append(_brief(row, c))
                if false_claim:
                    e2e["false_found_on_wrong_board"] += status == "FALSE"
                    e2e["false_unknown"] += status == "UNKNOWN"
                    e2e["false_incorrectly_verified"] += status == "VERIFIED"
                continue
            ver["correctly_extracted"] += 1
            ver[status] += 1
            right = status == TRUTH_TO_STATUS[want]
            ver["verdict_correct"] += right
            if not right:
                tax["verifier_disagrees_with_label"] += 1
                details["verifier_disagrees_with_label"].append(_brief(row, c))
            if want != "TRUE":
                ver["not_true_claims"] += 1
                if status == "VERIFIED":
                    ver["false_verified"] += 1
                    details["FALSE_VERIFIED"].append(_brief(row, c))
            if false_claim:
                e2e["false_caught"] += status == "FALSE"
                e2e["false_unknown"] += status == "UNKNOWN"
                e2e["false_incorrectly_verified"] += status == "VERIFIED"
        elif row["kind"] == "non_fact":
            cls = row["extraction_class"].lower()
            ex[f"{cls}_labelled"] += 1
            if row.get("known_issue"):
                known[row["known_issue"]] += 1
            statuses = {c["status"] for _, c in here}
            if row["false_concrete"]:
                e2e["false_claims"] += 1
                e2e["false_outside_predicates"] += 1
            if extracted:
                ex["hallucinated_on_non_fact"] += 1
                tax["hallucinated_on_non_fact"] += 1
                details["hallucinated_on_non_fact"].append(_brief(row, extracted[0][1]))
                for i, c in extracted:
                    used.add((case, i))
                    labelled_candidate[(case, i)] = row
                if row["false_concrete"]:  # each false claim lands in exactly one outcome
                    found = {c["verifier_status"] for _, c in extracted}
                    outcome = ("false_incorrectly_verified" if "VERIFIED" in found else
                               "false_caught_by_stray_candidate" if "FALSE" in found else "false_unknown")
                    e2e[outcome] += 1
            else:
                if row["extraction_class"] in statuses:
                    ex[f"{cls}_detected"] += 1
                else:
                    tax[f"{cls}_misclassified"] += 1
                    details[f"{cls}_misclassified"].append(_brief(row, None, sorted(statuses)))
                if row["false_concrete"]:
                    e2e["false_missed"] += 1
        else:
            _score_variation(row, var_rows[case], var, tax, details, used)

    hallucinated, verified_true, verified_false, verified_unlabelled = 0, 0, 0, 0
    for case, rows in fact_rows.items():
        for i, c in enumerate(rows):
            if c["status"] != "EXTRACTED":
                continue
            label = labelled_candidate.get((case, i))
            if label is None:
                hallucinated += 1
                tax["extraction_with_no_label"] += 1
                details["extraction_with_no_label"].append({"case": case, "text": c["source_text"], "predicate": c["predicate"],
                                                           "args": c["args"], "verifier": c["verifier_status"]})
            if c["verifier_status"] == "VERIFIED":
                if label is None:
                    verified_unlabelled += 1
                elif (label["kind"] == "fact" and label.get("polarity", "positive") == "positive"
                      and label["chess_truth"] == "TRUE" and c["board_moves"] == label["branch"]):
                    verified_true += 1
                else:
                    verified_false += 1
    ex["extraction_with_no_label"] = hallucinated

    all_verified = verified_true + verified_false + verified_unlabelled
    vp = var["verified_true"] + var["verified_false"] + var["verified_unlabelled"]
    verification = {**ver, "verdict_accuracy": rate(ver["verdict_correct"], ver["correctly_extracted"]),
                    "false_verified": ver["false_verified"],
                    "false_verification_acceptance_rate": rate(ver["false_verified"], ver["not_true_claims"])}
    return {
        "extraction": {**ex,
                       "predicate_accuracy": rate(ex["predicate_match"], ex["facts_labelled"]),
                       "args_accuracy_given_predicate": rate(ex["args_match_given_predicate"], ex["predicate_match"]),
                       "predicate_and_args_accuracy": rate(ex["predicate_and_args_match"], ex["facts_labelled"]),
                       "board_binding_accuracy": rate(ex["board_match"], ex["predicate_and_args_match"]),
                       "unsupported_recall": rate(ex["unsupported_detected"], ex["unsupported_labelled"]),
                       "ambiguous_recall": rate(ex["ambiguous_detected"], ex["ambiguous_labelled"])},
        "verification": verification,
        "end_to_end_facts": {**e2e,
                             "false_claim_capture_rate": rate(e2e["false_caught"], e2e["false_claims"]),
                             "false_claim_capture_rate_representable": rate(e2e["false_caught"], e2e["false_claims_representable"])},
        "variations": {**var,
                       "exact_match_rate": rate(var["exact"], var["labelled"]),
                       "false_variation_capture_rate": rate(var["false_caught"], var["false_lines"])},
        "verified_precision": {
            "facts_verified": all_verified, "facts_true": verified_true, "facts_false": verified_false, "facts_unlabelled": verified_unlabelled,
            "fact_precision": rate(verified_true, all_verified),
            "variations_verified": vp, "variations_true": var["verified_true"], "variations_false": var["verified_false"],
            "variations_unlabelled": var["verified_unlabelled"], "variation_precision": rate(var["verified_true"], vp)},
        "failure_taxonomy": dict(tax), "known_issues": dict(known),
        "details": {k: v for k, v in details.items()},
    }


def _score_variation(row, candidates, var, tax, details, used):
    case = row["case"]
    fails = not (row["legal"] and row["annotations_hold"]) if row["extraction_class"] == "EXTRACTED" else False
    here = [(i, c) for i, c in enumerate(candidates) if _overlap(row["span_start"], row["span_end"], c["span_start"], c["span_end"])]
    if row["extraction_class"] == "AMBIGUOUS":
        var["ambiguous_labelled"] += 1
        hit = any(c["status"] == "AMBIGUOUS" for _, c in here)
        var["ambiguous_detected"] += hit
        if not hit:
            tax["variation_ambiguous_misclassified"] += 1
            details["variation_ambiguous_misclassified"].append(_brief(row, None))
        return
    var["labelled"] += 1
    var["false_lines"] += fails
    exact = next(((i, c) for i, c in enumerate(candidates)
                  if c["status"] == "EXTRACTED" and c["moves"] == row["moves"] and ("v", case, i) not in used), None)
    if exact is None:
        tax["variation_missed"] += 1
        details["variation_missed"].append(_brief(row, here and here[0][1]))
        var["missed"] += 1
        if fails:
            ambiguous = any(c["status"] == "AMBIGUOUS" for _, c in here)
            var["false_became_unknown" if ambiguous else "false_missed"] += 1
        return
    i, c = exact
    used.add(("v", case, i))
    var["exact"] += 1
    status = c["verifier_status"]
    var["status_" + status] += 1
    if fails:
        var["false_caught"] += status == "FALSE"
        var["false_incorrectly_verified"] += status == "VERIFIED"
        var["false_became_unknown"] += status == "UNKNOWN"
        if status == "VERIFIED":
            details["FALSE_VERIFIED_VARIATION"].append(_brief(row, c))
    elif status != "VERIFIED":
        tax["variation_verifier_disagrees_with_label"] += 1
        details["variation_verifier_disagrees_with_label"].append(_brief(row, c))
    if status == "FALSE" and fails and c["verifier_first_false_ply"] != row["failure_ply"]:
        tax["variation_failure_ply_differs"] += 1
        details["variation_failure_ply_differs"].append(_brief(row, c))
    if status == "VERIFIED":
        var["verified_false" if fails else "verified_true"] += 1


def add_unmatched_variations(result, predictions, labels):
    """Prose or critical lines the labels never mention: unreviewed, so not counted as true."""
    wanted = {(r["case"], tuple(r["moves"])) for r in labels if r["kind"] == "variation" and r["extraction_class"] == "EXTRACTED"}
    rows = []
    for case, p in predictions.items():
        for v in p["variations"]:
            if v["kind"] in STRUCTURED or v["status"] != "EXTRACTED" or (case, tuple(v["moves"])) in wanted:
                continue
            rows.append({"case": case, "text": v["sentence"][:200], "moves": v["moves"], "verifier": v["verifier_status"]})
    result["variations"]["unlabelled"] = len(rows)
    n = sum(r["verifier"] == "VERIFIED" for r in rows)
    vp = result["verified_precision"]
    vp["variations_unlabelled"] = n
    vp["variations_verified"] = vp["variations_true"] + vp["variations_false"] + n
    vp["variation_precision"] = rate(vp["variations_true"], vp["variations_verified"])
    if rows:
        result["failure_taxonomy"]["variation_with_no_label"] = len(rows)
        result["details"]["variation_with_no_label"] = rows
    structured = [(v["verifier_status"]) for p in predictions.values() for v in p["variations"] if v["kind"] in STRUCTURED]
    result["variations"]["structured_answer_moves"] = dict(collections.Counter(structured))


def _brief(row, candidate, statuses=None):
    out = {"label_id": row["label_id"], "case": row["case"], "text": row["source_text"][:200], "class": row["extraction_class"]}
    for k in ("predicate", "args", "polarity", "branch", "chess_truth", "moves", "failure_ply"):
        if k in row:
            out["want_" + k] = row[k]
    if candidate:
        out["got"] = {k: candidate.get(k) for k in ("status", "predicate", "args", "board_moves", "moves", "verifier_status") if k in candidate}
    if statuses is not None:
        out["got_statuses"] = statuses
    return out


def run(base=HERE, draft=False) -> dict:
    with open(os.path.join(base, "labels_meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    manifest_path = os.path.join(base, "predictions", "MANIFEST.json")
    if not os.path.exists(manifest_path):
        raise ScoreError("no frozen predictions yet: run freeze_predictions.py")
    manifest_sha = sha256_file(manifest_path)
    if meta["predictions_manifest_sha256"] != manifest_sha:
        raise ScoreError("labels_meta.json does not name this prediction snapshot")
    if meta["status"] != "frozen" and not draft:
        raise ScoreError("labels are not frozen. Use --draft for a working score that must not be reported.")
    manifest, predictions = load_predictions(os.path.join(base, "predictions"))
    texts = {}
    for case in predictions:
        with open(os.path.join(base, "raw", case, "explanation.txt"), encoding="utf-8", newline="") as f:
            texts[case] = f.read()
    fens = {c: p["fen"] for c, p in predictions.items()}
    labels = load_jsonl(os.path.join(base, "labels.jsonl"))
    with open(os.path.join(base, "labels.schema.json"), encoding="utf-8") as f:
        schema = json.load(f)
    problems = validate_labels(labels, texts, fens, schema)
    if problems:
        raise ScoreError("invalid labels:\n  " + "\n  ".join(problems))
    result = score(predictions, labels)
    add_unmatched_variations(result, predictions, labels)
    result["meta"] = {"set": "held-out evaluation set", "draft": meta["status"] != "frozen", "labels": len(labels),
                      "labels_sha256": sha256_file(os.path.join(base, "labels.jsonl")), "manifest_sha256": manifest_sha,
                      "corrections": len(load_jsonl(os.path.join(base, meta["corrections_file"]))), "labeller": meta["labeller"],
                      "independent_of_grammar_author": meta["independent_of_grammar_author"],
                      "predictions_opened_before_labels_frozen": meta["predictions_opened_before_labels_frozen"]}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--draft", action="store_true", help="score labels that are not frozen; the output is marked and must not be reported")
    parser.add_argument("--out", default=os.path.join(HERE, "scores.json"))
    args = parser.parse_args(argv)
    try:
        result = run(draft=args.draft)
    except ScoreError as e:
        print(f"REFUSED: {e}")
        return 2
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, sort_keys=True, ensure_ascii=False)
    print(json.dumps({k: v for k, v in result.items() if k != "details"}, indent=1, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
