"""Checks the label files against the format: schema, spans, branches, predicates, argument names,
text hashes. It says nothing about whether a reading is right, and reads nothing outside this folder.

Usage: python validate_labels.py"""
import hashlib
import json
import os
import sys

import chess
import jsonschema

HERE = os.path.dirname(os.path.abspath(__file__))


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise SystemExit(f"{os.path.basename(path)} line {number}: not JSON: {e}")
    return rows


def load_cases(base):
    cases = {}
    folder = os.path.join(base, "cases")
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        with open(os.path.join(folder, name, "case.json"), encoding="utf-8") as f:
            case = json.load(f)
        with open(os.path.join(folder, name, "response.txt"), encoding="utf-8", newline="") as f:
            case["text"] = f.read()
        cases[name] = case
    return cases


def check_args(row, predicates):
    spec = predicates[row["predicate"]]
    required, allowed = set(spec["args"]), set(spec["args"]) | set(spec["optional"])
    given = set(row["args"])
    return [f"{row['label_id']}: {row['predicate']} needs argument '{a}'" for a in sorted(required - given)] + \
           [f"{row['label_id']}: {row['predicate']} takes no argument '{a}'" for a in sorted(given - allowed)]


def check_labels(labels, cases, schema, predicates):
    problems, seen = [], set()
    for row in labels:
        lid = row.get("label_id", "?")
        try:
            jsonschema.validate(row, schema)
        except jsonschema.ValidationError as e:
            problems.append(f"{lid}: {e.message}")
            continue
        if lid in seen:
            problems.append(f"{lid}: duplicate label_id")
        seen.add(lid)
        case = cases.get(row["case"])
        if case is None:
            problems.append(f"{lid}: unknown case {row['case']}")
            continue
        if case["text"][row["span_start"]:row["span_end"]] != row["source_text"]:
            problems.append(f"{lid}: source_text is not the text at that span")
        if row["kind"] == "fact":
            if row["predicate"] not in predicates:
                problems.append(f"{lid}: {row['predicate']} is not an allowed predicate")
            else:
                problems += check_args(row, predicates)
            if (row["branch"] is None) != (row["chess_truth"] == "UNDETERMINED"):
                problems.append(f"{lid}: chess_truth UNDETERMINED goes with branch null, and only then")
            if row["branch"] is not None:
                board = chess.Board(case["fen"])
                try:
                    for uci in row["branch"]:
                        board.push(board.parse_uci(uci))
                except ValueError as e:
                    problems.append(f"{lid}: branch does not replay: {e}")
        elif row["kind"] == "variation" and row["label_class"] == "LINE":
            if (row["legal"] and row["annotations_hold"]) != (row["failure_ply"] is None):
                problems.append(f"{lid}: failure_ply must be set exactly when legal or annotations_hold is false")
            if row["failure_ply"] is not None and not 0 <= row["failure_ply"] < len(row["moves"]):
                problems.append(f"{lid}: failure_ply is outside moves")
    return problems


def check_reviews(reviews, cases):
    problems, seen = [], set()
    for row in reviews:
        rid = row.get("review_id", "?")
        missing = [k for k in ("review_id", "case", "source_text", "question") if not row.get(k)]
        if missing:
            problems.append(f"review {rid}: missing {missing}")
            continue
        if rid in seen:
            problems.append(f"review {rid}: duplicate review_id")
        seen.add(rid)
        if row["case"] not in cases:
            problems.append(f"review {rid}: unknown case {row['case']}")
        elif row["source_text"] not in cases[row["case"]]["text"]:
            problems.append(f"review {rid}: source_text is not in that response")
    return problems


def check_hashes(cases, base):
    with open(os.path.join(base, "package.json"), encoding="utf-8") as f:
        package = json.load(f)
    problems = []
    for row in package["cases"]:
        case = cases.get(row["id"])
        if case is None:
            problems.append(f"{row['id']}: case folder is missing")
        elif hashlib.sha256(case["text"].encode("utf-8")).hexdigest() != row["response_sha256"]:
            problems.append(f"{row['id']}: response.txt has been changed")
    return problems


def validate(base=HERE):
    with open(os.path.join(base, "labels.schema.json"), encoding="utf-8") as f:
        schema = json.load(f)
    with open(os.path.join(base, "predicates.json"), encoding="utf-8") as f:
        predicates = json.load(f)["predicates"]
    cases = load_cases(base)
    labels = load_jsonl(os.path.join(base, "labels.jsonl"))
    reviews = load_jsonl(os.path.join(base, "review.jsonl"))
    problems = check_hashes(cases, base) + check_labels(labels, cases, schema, predicates) + check_reviews(reviews, cases)
    return problems, len(labels), len(reviews), sorted({r["case"] for r in labels if "case" in r}), sorted(cases)


def main():
    problems, labels, reviews, labelled, all_cases = validate()
    for problem in problems:
        print(problem)
    untouched = [c for c in all_cases if c not in labelled]
    print(f"{labels} labels, {reviews} open questions, {len(problems)} problems.")
    if untouched:
        print(f"No labels yet for: {', '.join(untouched)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
