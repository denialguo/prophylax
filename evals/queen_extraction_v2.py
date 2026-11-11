"""Extractor V2 evaluation: stored QUEEN output -> domain/extract_v2.py -> verifier -> metrics. A
separate path: frozen V1 labels, predictions and scores are only read, and results go to evals/queen_v2.

Recorded model answers make every score reproducible offline. Only `record` calls a model.

  development set  18 outputs the grammar was tuned on, scored like V1's benchmark
  held-out set     POST-HOC: 24 outputs already inspected, so this says nothing about generalization

Usage:
  set -a; . app/.env; set +a
  uv run python -m evals.queen_extraction_v2 record --set dev [--min-seconds 20]
  uv run python -m evals.queen_extraction_v2 score --set dev
  uv run python -m evals.queen_extraction_v2 record --set heldout
  uv run python -m evals.queen_extraction_v2 compare"""
import argparse
import collections
import copy
import datetime
import json
import os
import re
import time
from unittest import mock

import chess

from domain.extract_v2 import (DEFAULT_MODEL, PROMPT_VERSION, BackendError, ExtractedFactV2, ExtractionV2, LiteLLMBackend, QuotaError, SemanticExtractorBackend,
                               extract_v2, request_key, split_chunks)
from domain.verify import verify_fact
from evals import queen_extraction as qe
from evals.queen_heldout import score as sc

OUT = os.path.join(os.path.dirname(__file__), "queen_v2")
HELDOUT = sc.HERE
POST_HOC = "post-hoc evaluation on previously inspected held-out set"
SUBJECT_ARGS = ("source", "slider", "pinner", "pinned", "square")
NEEDS_SUBJECT = ("attacks", "defends", "blocks", "pinned_against", "is_passed_pawn", "promotion_distance_is")
V1_BASELINE = {"exact": (91, 468), "board": (81, 91), "unsupported": (438, 498), "false_capture": (32, 92), "precision": (45, 52), "variations": (143, 175), "false_lines": (18, 27), "false_verified": 0}


# ── judging: the frozen verifier decides, always ─────────────────────────────
def board_after(fen, moves):
    board = chess.Board(fen)
    for uci in moves or ():
        board.push_uci(uci)
    return board


def judge_fact_v2(fen, candidate: ExtractedFactV2):
    """The verifier's answer. A positive claim goes through V1's judge unchanged. A negative claim is
    the opposite of the predicate, but only about something there: "the rook does not attack e7" with
    no rook on that square is not a true statement about a rook, so it is UNKNOWN, never VERIFIED."""
    if candidate.status != "EXTRACTED":
        return None
    if candidate.polarity == "positive":
        return qe.judge_fact(fen, candidate)
    if candidate.prefix is None:
        return {"status": "UNKNOWN", "reason": "no board resolved for this claim", "results": []}
    position = board_after(fen, candidate.prefix)
    guards = [verify_fact(position.fen(), g) for g in candidate.guards]
    if any(g.status == "FALSE" for g in guards):
        return {"status": "FALSE", "reason": next(g.reason for g in guards if g.status == "FALSE"), "results": [g.model_dump() for g in guards]}
    args = candidate.fact.args
    if candidate.fact.predicate in NEEDS_SUBJECT:
        subject = next((args[k] for k in SUBJECT_ARGS if k in args), None)
        piece = position.piece_at(chess.parse_square(subject)) if subject else None
        if piece is None or (args.get("piece") and chess.piece_name(piece.piece_type) != args["piece"]):
            return {"status": "UNKNOWN", "reason": "the piece the claim denies something about is not there", "results": []}
    result = verify_fact(position.fen(), candidate.fact)
    status = {"VERIFIED": "FALSE", "FALSE": "VERIFIED", "UNKNOWN": "UNKNOWN"}[result.status]
    return {"status": status, "reason": "the predicate holds, so the denial is false" if status == "FALSE" else result.reason, "results": [result.model_dump()]}


def to_prediction(case_id, fen, text, extraction: ExtractionV2, variations, judge=judge_fact_v2) -> dict:
    """The shape score.py reads, with polarity added. Variations come from the frozen predictions."""
    facts = []
    for c in extraction.facts:
        judged = judge(fen, c)
        facts.append({"status": c.status, "sentence": c.sentence, "span_start": c.span_start, "span_end": c.span_end, "source_text": text[c.span_start:c.span_end],
                      "reason": c.reason, "predicate": c.fact.predicate if c.fact else None, "args": c.fact.args if c.fact else None,
                      "polarity": c.polarity, "guards": [g.model_dump() for g in c.guards], "change_from": c.change_from.model_dump() if c.change_from else None,
                      "board_moves": list(c.prefix) if c.prefix is not None else None, "interpretations": list(c.interpretations), "branch_source": c.branch_source, "residual": c.residual,
                      "verifier_status": judged["status"] if judged else None, "verifier_reason": judged["reason"] if judged else None})
    return {"case": case_id, "fen": fen, "facts": facts, "variations": variations}


def mirror_negatives(predictions, labels):
    """score.py only knows positive candidates. Negative claims go through it under a separate predicate
    name on both sides, so a negative candidate matches only a negative label and its verdict is
    compared with the label's truth. The scorer and every V1 number stay exactly as they were."""
    predictions, labels = copy.deepcopy(predictions), copy.deepcopy(labels)
    for p in predictions.values():
        for c in p["facts"]:
            if c.get("polarity") == "negative" and c["status"] == "EXTRACTED":
                c["predicate"] = "not:" + c["predicate"]
    for row in labels:
        if row["kind"] == "fact" and row.get("polarity", "positive") == "negative":
            row["predicate"], row["polarity"] = "not:" + row["predicate"], "positive"
    return predictions, labels


def score_heldout(predictions, labels) -> dict:
    mirrored, mirrored_labels = mirror_negatives(predictions, labels)
    result = sc.score(mirrored, mirrored_labels)
    sc.add_unmatched_variations(result, mirrored, mirrored_labels)
    negative = [r for r in labels if r["kind"] == "fact" and r.get("polarity", "positive") == "negative"]
    owner = sc.assign_candidates({c: p["facts"] for c, p in mirrored.items()}, mirrored_labels)
    matched = {o["label_id"] for o in owner.values() if o["kind"] == "fact" and o["predicate"].startswith("not:")}
    result["extraction"].update(negative_labelled=len(negative), negative_matched=len(matched),
                                negative_missed=len(negative) - len(matched), wrong_polarity=_wrong_polarity(predictions, labels))
    return result


def _wrong_polarity(predictions, labels) -> int:
    """A candidate that reads a claim with the opposite polarity from its label."""
    count = 0
    for row in labels:
        if row["kind"] != "fact" or row["case"] not in predictions:
            continue
        want = row.get("polarity", "positive")
        count += any(c["status"] == "EXTRACTED" and c.get("polarity", "positive") != want and c["predicate"] == row["predicate"] and sc.args_match(row["args"], c["args"])
                     and sc._overlap(row["span_start"], row["span_end"], c["span_start"], c["span_end"]) for c in predictions[row["case"]]["facts"])
    return count


def evaluate_dev(extractions: dict, cases=None, gold=None, judge=judge_fact_v2) -> dict:
    """V1's own development metric on V2's candidates. Its labels class negative statements as
    UNSUPPORTED, so a negative candidate counts as that here and is reported separately."""
    cases = cases or qe.load_cases()
    # One audited label has no board, because none can be determined. V1 never matched it, so the
    # frozen metric never read the field. None is what it means.
    gold = [{**g, "board": g.get("board")} if g.get("kind") != "variation" and g.get("status") == "EXTRACTED" else g for g in (gold if gold is not None else qe.load_gold())]

    def rows(case):
        facts = []
        for c in extractions[case["id"]].facts:
            if c.status == "EXTRACTED" and c.polarity == "negative":
                c = c.model_copy(update={"status": "UNSUPPORTED", "fact": None, "guards": (), "change_from": None})
            facts.append({"candidate": c, "judged": judge(case["fen"], c)})
        v1 = qe.extract(case["text"], case["fen"], case["id"])
        return {"facts": facts, "variations": [{"candidate": v, "judged": qe.judge_variation(case["fen"], v)} for v in v1.variations]}
    with mock.patch.object(qe, "audit_rows", rows):  # the frozen module computes the metric; only its candidates differ
        result = qe.evaluate(cases, gold)
    result["negative_candidates"] = sum(c.status == "EXTRACTED" and c.polarity == "negative" for x in extractions.values() for c in x.facts)
    result["rejected_proposals"] = sum(c.status == "PARSE_ERROR" for x in extractions.values() for c in x.facts)
    result["residual_unsupported"] = sum(c.residual for x in extractions.values() for c in x.facts)
    return result


# ── recordings ───────────────────────────────────────────────────────────────
class RecordingBackend(SemanticExtractorBackend):
    """Answers from a case's recording; asks the live backend only for a chunk not yet recorded."""

    def __init__(self, folder, case_id, live=None, model=DEFAULT_MODEL, min_seconds=0.0):
        self.path = os.path.join(folder, case_id + ".json")
        self.live, self.model, self.min_seconds = live, model, min_seconds
        self.answers = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.answers = json.load(f)["answers"]
        self.info = {"provider": model.split("/")[0], "model": model, "settings": (live.settings if live else next((a["settings"] for a in self.answers.values()), {}))}

    def extract(self, request):
        key = request_key(request, self.model)
        if key in self.answers:
            return self.answers[key]["raw"]
        if self.live is None:
            raise BackendError(f"no recording for {request['case']} chunk {request['chunk']} with {self.model} prompt {request['prompt_version']}")
        started = time.monotonic()
        raw = self.live.extract(request)
        self.answers[key] = {"chunk": request["chunk"], "model": self.model, "provider": self.info["provider"], "settings": self.live.settings, "prompt_version": request["prompt_version"],
                             "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "seconds": round(time.monotonic() - started, 1), "raw": raw}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"case": request["case"], "answers": self.answers}, f, indent=1, ensure_ascii=False)
        time.sleep(max(0.0, self.min_seconds - (time.monotonic() - started)))
        return raw


def load_set(name):
    """{case id: (fen, text)} for a set, read only."""
    if name == "dev":
        return {cid: (c["fen"], c["text"]) for cid, c in qe.load_cases().items()}
    with open(os.path.join(HELDOUT, "suite.json"), encoding="utf-8") as f:
        suite = json.load(f)["cases"]
    out = {}
    for c in suite:
        with open(os.path.join(HELDOUT, "raw", c["id"], "explanation.txt"), encoding="utf-8", newline="") as f:
            out[c["id"]] = (c["fen"], f.read())
    return out


def _wait_seconds(message):
    """Seconds a per-minute limit asks for; None when the limit is a daily one, which waiting will not fix."""
    if re.search(r"per day|\bTPD\b|\bRPD\b|PerDay", message):
        return None
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", message)
    return (int(m.group(1) or 0) * 60 + float(m.group(2)) + 1) if m else None


def record(name, model, min_seconds, folder=OUT, attempts=8, live=None) -> int:
    live = live or LiteLLMBackend(model)
    for case_id, (fen, text) in load_set(name).items():
        backend = RecordingBackend(os.path.join(folder, "recordings", name), case_id, live, model, min_seconds)
        for _ in range(attempts):
            try:
                extraction = extract_v2(text, fen, backend, case_id)
                break
            except QuotaError as e:
                wait = _wait_seconds(str(e))
                if wait is None:
                    print(f"{case_id}: quota, stopping. Rerun the same command after it resets; recorded chunks are kept. {str(e)[:200]}", flush=True)
                    return 3
                print(f"{case_id}: rate limit, waiting {wait:.0f}s", flush=True)
                time.sleep(wait)
            except BackendError as e:
                if re.search(r"ServiceUnavailable|\b503\b|\b500\b|Timeout", str(e)):
                    print(f"{case_id}: provider unavailable, waiting 30s", flush=True)
                    time.sleep(30)
                    continue
                print(f"{case_id}: request failed, stopping: {str(e)[:300]}", flush=True)
                return 1
        else:
            print(f"{case_id}: still rate limited after {attempts} attempts, stopping", flush=True)
            return 3
        print(case_id, collections.Counter(c.status for c in extraction.facts).most_common(), flush=True)
    return 0


def replay(name, model, folder=OUT) -> dict:
    return {cid: extract_v2(text, fen, RecordingBackend(os.path.join(folder, "recordings", name), cid, None, model), cid) for cid, (fen, text) in load_set(name).items()}


# ── results ──────────────────────────────────────────────────────────────────
def _ratio(n, d):
    return f"{n}/{d} = {n / d:.1%}" if d else f"{n}/{d}"


def heldout_predictions(extractions) -> dict:
    """V2's facts with the frozen V1 variations, re-checked against the manifest as they are read."""
    _, frozen = sc.load_predictions(os.path.join(HELDOUT, "predictions"))
    cases = load_set("heldout")
    return {cid: to_prediction(cid, fen, text, extractions[cid], frozen[cid]["variations"]) for cid, (fen, text) in cases.items()}


def heldout_metrics(r) -> dict:
    e, v, end, var, vp = r["extraction"], r["verification"], r["end_to_end_facts"], r["variations"], r["verified_precision"]
    return {"exact": (e["predicate_and_args_match"], e["facts_labelled"]), "board": (e["board_match"], e["predicate_and_args_match"]),
            "unsupported": (e["unsupported_detected"], e["unsupported_labelled"]), "ambiguous": (e["ambiguous_detected"], e["ambiguous_labelled"]),
            "verdicts": (v["verdict_correct"], v["correctly_extracted"]), "false_verified": v["false_verified"], "false_capture": (end["false_caught"], end["false_claims"]),
            "precision": (vp["facts_true"], vp["facts_verified"]), "variations": (var["exact"], var["labelled"]), "false_lines": (var["false_caught"], var["false_lines"]),
            "false_end_to_end_verified": end["false_incorrectly_verified"], "false_variation_verified": var["false_incorrectly_verified"], "hallucinated_on_non_fact": e["hallucinated_on_non_fact"], "no_label": e["extraction_with_no_label"]}


def caught_false_facts(predictions, labels) -> set:
    """Labels of false facts caught: exact read, right board, verdict FALSE."""
    mirrored, mirrored_labels = mirror_negatives(predictions, labels)
    rows = {c: p["facts"] for c, p in mirrored.items()}
    return {r["label_id"] for (case, i), r in sc.assign_candidates(rows, mirrored_labels).items()
            if r["kind"] == "fact" and r["chess_truth"] == "FALSE" and rows[case][i]["board_moves"] == r["branch"] and rows[case][i]["verifier_status"] == "FALSE"}


def verified_mentions_on_non_facts(predictions, labels) -> int:
    """VERIFIED piece_at candidates owned by a broad non-fact label: "the rook on e7" inside a strategic
    sentence, for which the labels hold no child fact. The strict metric counts each against precision."""
    rows = {c: p["facts"] for c, p in predictions.items()}
    return sum(r["kind"] == "non_fact" and rows[case][i]["predicate"] == "piece_at" and rows[case][i]["verifier_status"] == "VERIFIED"
               for (case, i), r in sc.assign_candidates(rows, labels).items())


def comparison_report(v1, v2, model, chunks, analysis=None) -> str:
    a, b = heldout_metrics(v1), heldout_metrics(v2)
    rows = [("Exact predicate + args", "exact"), ("Correct board, given exact", "board"), ("Unsupported recall", "unsupported"), ("Ambiguous recall", "ambiguous"),
            ("Verdict accuracy on correct reads", "verdicts"), ("False-claim capture", "false_capture"), ("VERIFIED fact precision", "precision"),
            ("Variation exact match", "variations"), ("False variation capture", "false_lines")]
    lines = [f"# Extractor V2 vs V1: {POST_HOC}", "",
             f"**This is a {POST_HOC}.** These 24 outputs and their labels were read before V2 existed, so these numbers do not measure generalization. Any such claim needs a fresh blind benchmark.", "",
             f"Model: `{model}`, prompt `{PROMPT_VERSION}`, temperature 0, {chunks} recorded answers. Frozen V1 artifacts were only read.", "",
             "| Metric | V1 (frozen) | V2 |", "|---|---:|---:|"]
    lines += [f"| {label} | {_ratio(*a[key])} | {_ratio(*b[key])} |" for label, key in rows]
    lines += [f"| False VERIFIED on correct reads | {a['false_verified']} | {b['false_verified']} |", f"| False claims VERIFIED end to end, including stray candidates | {a['false_end_to_end_verified']} | {b['false_end_to_end_verified']} |", f"| False variations VERIFIED | {a['false_variation_verified']} | {b['false_variation_verified']} |",
              f"| Extractions on labelled non-facts | {a['hallucinated_on_non_fact']} | {b['hallucinated_on_non_fact']} |", f"| Extractions matching no label | {a['no_label']} | {b['no_label']} |"]
    e = v2["extraction"]
    lines += ["", f"Negative claims read: {e['negative_matched']}/{e['negative_labelled']}. V1 could not represent them. Candidates with the opposite polarity from their label: {e['wrong_polarity']}.", ""]
    if analysis:
        n, d = b["precision"]
        lines += ["## Reading these numbers", "",
                  f"- **False-claim capture matches by coincidence.** V1 caught {analysis['v1_caught']}, V2 caught {analysis['v2_caught']}, and only {analysis['both_caught']} are the same claims. Together they cover {analysis['either_caught']}/{analysis['false_claims']}, which is a set union and not a measured system.",
                  f"- **Precision, sensitivity only.** The primary figure stays {_ratio(n, d)}. Of the VERIFIED facts it counts against V2, {analysis['mentions']} are piece_at mentions inside broad non-fact labels with no child fact. Leaving those out gives {_ratio(n, d - analysis['mentions'])}. This view was chosen after seeing the result.",
                  f"- **Unsupported recall.** {e['unsupported_undetected_child_facts_only']} undetected parents hold only extractions matched to child facts. {analysis['residual']} of V2's UNSUPPORTED candidates are sentences the model skipped, marked residual, not its own reading.", ""]
    return "\n".join(lines) + "\n"


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True, ensure_ascii=False, default=str)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("record", "score", "compare"))
    parser.add_argument("--set", choices=("dev", "heldout"), default="dev")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--min-seconds", type=float, default=0.0, help="least time per request, to stay under a provider's rate limit")
    args = parser.parse_args(argv)
    if args.command == "record":
        return record(args.set, args.model, args.min_seconds)
    extractions = replay("dev" if args.command == "score" and args.set == "dev" else "heldout", args.model)
    chunks = sum(len(split_chunks(text)) for _, text in load_set("heldout").values())
    if args.command == "score" and args.set == "dev":
        result = evaluate_dev(extractions)
        write_json(os.path.join(OUT, "dev_scores.json"), result)
        print(json.dumps({k: result[k] for k in ("extraction", "verification", "negative_candidates", "rejected_proposals", "residual_unsupported", "unlabelled_verified")}, indent=1, sort_keys=True))
        return 0
    labels = sc.load_jsonl(os.path.join(HELDOUT, "labels.jsonl"))
    predictions = heldout_predictions(extractions)
    result = score_heldout(predictions, labels)
    result["meta"] = {"set": POST_HOC, "model": args.model, "prompt_version": PROMPT_VERSION}
    write_json(os.path.join(OUT, "heldout_posthoc_scores.json"), result)
    with open(os.path.join(HELDOUT, "scores.json"), encoding="utf-8") as f:
        v1 = json.load(f)
    _, frozen = sc.load_predictions(os.path.join(HELDOUT, "predictions"))
    first, second = caught_false_facts(frozen, labels), caught_false_facts(predictions, labels)
    analysis = {"v1_caught": len(first), "v2_caught": len(second), "both_caught": len(first & second), "either_caught": len(first | second), "false_claims": result["end_to_end_facts"]["false_claims"],
                "mentions": verified_mentions_on_non_facts(predictions, labels), "residual": sum(c.residual for x in extractions.values() for c in x.facts)}
    result["analysis"] = analysis
    report = comparison_report(v1, result, args.model, chunks, analysis)
    write_json(os.path.join(OUT, "heldout_posthoc_scores.json"), result)
    with open(os.path.join(OUT, "COMPARISON.md"), "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
