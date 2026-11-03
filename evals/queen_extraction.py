"""QUEEN claim extraction benchmark: stored output -> candidates (domain/extract.py) ->
verifier (domain/verify.py) -> audit row. Offline: no QUEEN, no engine, no LLM.

Two separate questions, reported separately:
1. extraction    did it read what the sentence says? (evals/queen_benchmark/gold.jsonl)
2. verification  of what it read correctly, what did the verifier say?

The labels are hand-drafted from the boards and QUEEN_audit_01_18.md, pending operator review.
The most dangerous number is false verification acceptance: VERIFIED where the label says otherwise.

Usage: ./venv/bin/python -m evals.queen_extraction [--json FILE]
"""
import argparse
import collections
import json
import os
from typing import Optional

import chess

from domain.extract import ExtractedFact, ExtractedVariation, extract
from domain.verify import FactResult, verify_fact, verify_variation

BENCH = os.path.join(os.path.dirname(__file__), "queen_benchmark")


def load_cases() -> dict:
    with open(os.path.join(BENCH, "cases.json"), encoding="utf-8") as f:
        cases = {c["id"]: c for c in json.load(f)["cases"]}
    for c in cases.values():
        with open(os.path.join(BENCH, "outputs", c["id"] + ".txt"), encoding="utf-8", newline="") as f:
            c["text"] = f.read()
    return cases


def load_gold() -> list:
    with open(os.path.join(BENCH, "gold.jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def judge_fact(root_fen: str, candidate: ExtractedFact) -> Optional[dict]:
    """The verifier's answer to one candidate, on the board the prose reached. Combines the
    fact, the identities the sentence stated and any change-of-state requirement: FALSE if any
    is false, UNKNOWN if any cannot be proven, VERIFIED only if all hold."""
    if candidate.status != "EXTRACTED":
        return None
    if candidate.prefix is None:
        return {"status": "UNKNOWN", "reason": "no board resolved for this claim", "results": []}
    board = chess.Board(root_fen)
    before = None
    for uci in candidate.prefix:
        before = board.fen()
        board.push_uci(uci)
    results = [verify_fact(board.fen(), candidate.fact)] + [verify_fact(board.fen(), g) for g in candidate.guards]
    reason = next((r.reason for r in results if r.status == "FALSE"), None)
    status = "FALSE" if reason or any(r.status == "FALSE" for r in results) else \
        "UNKNOWN" if any(r.status == "UNKNOWN" for r in results) else "VERIFIED"
    if status == "VERIFIED" and candidate.change_from is not None:
        if before is None:
            status, reason = "UNKNOWN", "no move before this claim to compare with"
        else:
            earlier = verify_fact(before, candidate.change_from)
            results.append(earlier)
            if earlier.status == "VERIFIED":
                status, reason = "FALSE", "it already held before the move"
            elif earlier.status == "UNKNOWN":
                status, reason = "UNKNOWN", earlier.reason
    return {"status": status, "reason": reason, "results": [r.model_dump() for r in results]}


def judge_variation(root_fen: str, candidate: ExtractedVariation) -> Optional[str]:
    if candidate.status != "EXTRACTED":
        return None
    return verify_variation(root_fen, list(candidate.plies)).status


def audit_rows(case: dict) -> dict:
    extraction = extract(case["text"], case["fen"], case["id"])
    return {"facts": [{"candidate": c, "judged": judge_fact(case["fen"], c)} for c in extraction.facts],
            "variations": [{"candidate": v, "judged": judge_variation(case["fen"], v)} for v in extraction.variations]}


def _span_of(text: str, quote: str) -> tuple:
    start = text.index(quote)
    return start, start + len(quote)


def _overlaps(a: tuple, s: int, e: int) -> bool:
    return a[0] < e and s < a[1]


def _core(fact) -> tuple:
    return fact.predicate, tuple(sorted((fact.args or {}).items()))


def evaluate(cases: dict = None, gold: list = None) -> dict:
    """Scores extraction against the labels, then what the verifier said about correct reads."""
    cases, gold = cases or load_cases(), gold if gold is not None else load_gold()
    rows = {cid: audit_rows(c) for cid, c in cases.items()}
    e = collections.Counter()
    v = collections.Counter()
    details = collections.defaultdict(list)
    matched = collections.defaultdict(set)     # (case, candidate index) of candidates a label accounts for
    in_quote = collections.defaultdict(set)    # EXTRACTED candidates inside a label's span
    for g in gold:
        case, text = g["case"], cases[g["case"]]["text"]
        span = _span_of(text, g["quote"])
        if g.get("kind") == "variation":
            _variation(g, span, rows[case]["variations"], e, v, details)
            continue
        here = [(i, r) for i, r in enumerate(rows[case]["facts"])
                if _overlaps(span, r["candidate"].span_start, r["candidate"].span_end)]
        extracted = [(i, r) for i, r in here if r["candidate"].status == "EXTRACTED"]
        in_quote[case].update(i for i, _ in extracted)
        if g["status"] != "EXTRACTED":
            e[f"{g['status'].lower()}_labelled"] += 1
            statuses = {r["candidate"].status for _, r in here}
            if extracted:
                e["hallucinated_on_non_claim"] += 1
                details["hallucinated"].append((case, g["quote"], [_core(r["candidate"].fact) for _, r in extracted]))
            elif g["status"] in statuses:
                e[f"{g['status'].lower()}_detected"] += 1
            else:
                details["status_mismatch"].append((case, g["quote"], g["status"], sorted(statuses)))
            continue
        e["facts_labelled"] += len(g["facts"])
        rows_for = []
        for predicate, args in g["facts"]:
            hit = next(((i, r) for i, r in extracted if _core(r["candidate"].fact) == (predicate, tuple(sorted(args.items())))), None)
            if hit is None:
                near = next(((i, r) for i, r in extracted if r["candidate"].fact.predicate == predicate), None)
                e["predicate_right_args_wrong" if near else "missed"] += 1
                details["args_wrong" if near else "missed"].append((case, g["quote"], predicate, args,
                                                                    near and near[1]["candidate"].fact.args))
                continue
            matched[case].add(hit[0])
            e["exact_match"] += 1
            rows_for.append(hit[1])
        for r in rows_for:
            want = None if g["board"] is None else tuple(g["board"])
            if r["candidate"].prefix == want:
                e["board_match"] += 1
            else:
                e["board_wrong"] += 1
                details["board_wrong"].append((case, g["quote"], want, r["candidate"].prefix))
        if len(rows_for) == len(g["facts"]) and rows_for:
            statuses = [r["judged"]["status"] for r in rows_for]
            got = "FALSE" if "FALSE" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "VERIFIED"
            v[got] += 1
            v[f"label_{g['verdict']}_got_{got}"] += 1
            if got == "VERIFIED" and g["verdict"] != "VERIFIED":
                v["false_verification_acceptance"] += 1
                details["FALSE_ACCEPTANCE"].append((case, g["quote"]))
            elif got != g["verdict"]:
                details["verdict_differs"].append((case, g["quote"], g["verdict"], got))
    extra, extra_verified = [], 0
    for case, r in rows.items():
        for i, row in enumerate(r["facts"]):
            if row["candidate"].status == "EXTRACTED" and i not in matched[case]:
                in_label = i in in_quote[case]
                extra.append((case, in_label, row["candidate"].sentence, row["candidate"].fact.predicate,
                              row["candidate"].fact.args, row["judged"]["status"]))
                extra_verified += row["judged"]["status"] == "VERIFIED"
    return {"extraction": dict(e), "verification": dict(v), "details": dict(details),
            "unlabelled_candidates": extra, "unlabelled_verified": extra_verified}


def _variation(g, span, candidates, e, v, details) -> None:
    case = g["case"]
    here = [r for r in candidates if _overlaps(span, r["candidate"].span_start, r["candidate"].span_end)]
    if g.get("status") == "AMBIGUOUS":
        e["variation_ambiguous_labelled"] += 1
        if any(r["candidate"].status == "AMBIGUOUS" for r in here):
            e["variation_ambiguous_detected"] += 1
        else:
            details["variation_status"].append((case, g["quote"], [r["candidate"].ucis for r in here]))
        return
    e["variations_labelled"] += 1
    want = tuple(g["plies"])
    hit = next((r for r in here if r["candidate"].status == "EXTRACTED" and r["candidate"].ucis == want), None)
    if hit is None:
        hit = next((r for r in candidates if r["candidate"].status == "EXTRACTED" and r["candidate"].ucis == want), None)
    if hit is None:
        e["variations_missed"] += 1
        details["variation_missed"].append((case, g["quote"], want, [r["candidate"].ucis for r in here]))
        return
    e["variations_exact"] += 1
    got = hit["judged"]
    v[f"variation_{got}"] += 1
    if got == "VERIFIED" and g["verdict"] != "VERIFIED":
        v["false_verification_acceptance"] += 1
        details["FALSE_ACCEPTANCE"].append((case, g["quote"]))
    elif got != g["verdict"]:
        details["verdict_differs"].append((case, g["quote"], g["verdict"], got))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", help="write the full result here")
    args = parser.parse_args(argv)
    result = evaluate()
    print("extraction:", json.dumps(result["extraction"], indent=1, sort_keys=True))
    print("verification:", json.dumps(result["verification"], indent=1, sort_keys=True))
    for name, rows in sorted(result["details"].items()):
        print(f"\n{name} ({len(rows)})")
        for row in rows:
            print("  ", row)
    print(f"\nunlabelled EXTRACTED candidates: {len(result['unlabelled_candidates'])}, "
          f"of which VERIFIED: {result['unlabelled_verified']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
