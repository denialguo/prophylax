"""Extractor V2.2 evaluation. Frozen artifacts and earlier recordings are only read; results go to
evals/queen_v2_2. V2.2 itself is scored on development only. `false-alarms` reads the held-out labels to
describe V2.1's existing verdicts and replays recordings, so it calls no model and relabels nothing.

Usage:
  uv run python -m evals.queen_extraction_v2_2 false-alarms
  set -a; . app/.env; set +a
  uv run python -m evals.queen_extraction_v2_2 record --model MODEL --min-seconds 15
  uv run python -m evals.queen_extraction_v2_2 dev --model MODEL"""
import argparse
import collections
import json
import os
import re

from domain import extract_v2_2 as h22
from domain.extract_v2 import DEFAULT_MODEL, BackendError, LiteLLMBackend, extract_v2
from evals import queen_extraction as qe
from evals import queen_extraction_v2 as q2
from evals import queen_extraction_v2_1 as q21
from evals.queen_heldout import score as sc

OUT = os.path.join(os.path.dirname(__file__), "queen_v2_2")
CLASSES = {"A": "overlaps a TRUE fact on the same board", "B": "overlaps a TRUE fact on a different board", "C": "overlaps UNSUPPORTED text", "D": "overlaps AMBIGUOUS text",
           "E": "has no gold label", "F": "overlaps a gold concrete claim with a different predicate or args"}
FAMILIES = {"material_balance_is": "material", "piece_count_is": "count", "attacks": "attack", "defends": "defend", "piece_at": "placement", "is_passed_pawn": "passed pawn",
            "is_open_file": "file", "is_semi_open_file": "file", "pinned_against": "pin"}


def false_alarm_rows(predictions, labels) -> list:
    """Each FALSE verdict no label confirms, by the same definition as V2.1's tally, classified from the
    labels alone. A concrete claim the candidate overlaps but does not match outranks broad prose."""
    mirrored, mirrored_labels = q2.mirror_negatives(predictions, labels)
    rows = {c: p["facts"] for c, p in mirrored.items()}
    owner = sc.assign_candidates(rows, mirrored_labels)
    out = []
    for case, facts in rows.items():
        for i, c in enumerate(facts):
            if c["status"] != "EXTRACTED" or c["verifier_status"] != "FALSE":
                continue
            label = owner.get((case, i))
            if label is not None and label["kind"] == "fact" and label["chess_truth"] == "FALSE":
                continue
            near = [r for r in mirrored_labels if r["case"] == case and r["kind"] == "fact" and sc._overlap(r["span_start"], r["span_end"], c["span_start"], c["span_end"])]
            if label is not None and label["kind"] == "fact":
                kind = "A" if c["board_moves"] == label["branch"] else "B"
            elif near:
                kind = "F"
            elif label is None:
                kind = "E"
            else:
                kind = {"UNSUPPORTED": "C", "AMBIGUOUS": "D"}[label["extraction_class"]]
            sources = c["produced_by"]
            out.append({"case": case, "class": kind, "predicate": c["predicate"], "args": c["args"], "polarity": c["polarity"], "source": sources[0] if len(sources) == 1 else "merged", "produced_by": sources,
                        "family": "change" if c["change_from"] else FAMILIES.get(c["predicate"].removeprefix("not:"), "other"), "from_rule": "rules" in sources, "board": _board_state(c, label),
                        "label_id": label["label_id"] if label else None, "false_concrete": bool(label and label.get("false_concrete")),
                        "near": [{"label_id": r["label_id"], "predicate": r["predicate"], "args": r["args"], "truth": r["chess_truth"]} for r in near if not label or r["label_id"] != label["label_id"]],
                        "text": c["source_text"], "reason": c["verifier_reason"]})
    return out


def _board_state(candidate, label) -> str:
    moves = candidate["board_moves"]
    state = "no board" if moves is None else "root" if not moves else f"{len(moves)} moves in"
    return f"{state}, {'matches' if moves == label['branch'] else 'differs from'} label" if label and label["kind"] == "fact" else state


def false_alarm_report(rows, title="the current V2.1 hybrid") -> str:
    lines = ["# Possible false alarms: diagnostic only", "", f"The {len(rows)} FALSE verdicts from {title} with no confirming FALSE label on the 24 previously inspected held-out outputs. Classified mechanically from the labels. No label changed, and nothing here shows whether a verdict was right.", ""]
    for title_, key in (("Class", "class"), ("Predicate", "predicate"), ("Source", "source"), ("Family", "family"), ("Board", "board"), ("From a deterministic rule", "from_rule")):
        tally = collections.Counter(r[key] for r in rows)
        lines += [f"## By {title_.lower()}", "", f"| {title_} | Count |", "|---|---:|"]
        lines += [f"| {f'{k}: {CLASSES[k]}' if key == 'class' else k} | {n} |" for k, n in sorted(tally.items(), key=lambda kv: str(kv[0]))] + [""]
    material = [r for r in rows if r["family"] == "material"]
    lines += [f"## The {len(material)} material candidates", ""]
    lines += [f"- `{r['case']}` {r['class']}, {r['source']}: \"{r['text'].strip()}\" read as {r['args']}. Verifier: {r['reason']}." for r in material] + [""]
    lines += ["## Every candidate", "", "| Case | Class | Claim | Source | Board | Text |", "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["class"], r["case"])):
        text = " ".join(r["text"].split()).replace("|", "\\|")[:110]
        lines.append(f"| `{r['case']}` | {r['class']} | `{r['predicate']}` {r['args']} | {r['source']} | {r['board']} | {text} |")
    return "\n".join(lines) + "\n"


# ── development comparison ───────────────────────────────────────────────────
COUNT_FAMILIES = (("inventory: X and Y against Z", r"\bagainst\b"), ("has no pieces", r"\bhas no pieces\b"), ("has no X", r"\bhas no\b"), ("has N X", r"\bhas (?:one|two|three|four|five|six|seven|eight)\b"),
                  ("only N X remains", r"\bonly\b.*\bremains?\b"), ("both X", r"\bboth\b"), ("bishop pair", r"\bbishop pair\b"), ("lost a X", r"\blost\b"))
_CHANGE_CLAIM = re.compile(r"\b(?:creat\w*|open(?:s|ing|ed)|produc\w*|becom\w*|became)\b", re.I)
_SQUARE = re.compile(r"\b[a-h][1-8]\b")


def family_of_count(quote: str) -> str:
    return next((name for name, pattern in COUNT_FAMILIES if re.search(pattern, quote)), "other")


def count_taxonomy(gold, cases=None) -> dict:
    """Development count labels by wording family, from the labels' own quotes."""
    rows = [(g["quote"], sum(f[0] == "piece_count_is" for f in g["facts"])) for g in gold if g.get("status") == "EXTRACTED" and any(f[0] == "piece_count_is" for f in g["facts"])]
    families = collections.Counter()
    for quote, n in rows:
        families[family_of_count(quote)] += n
    return {"labels": sum(n for _, n in rows), "sentences": len(rows), "families": dict(families), "quotes": [q for q, _ in rows]}


def replay_v2(model, folder, reader=None) -> dict:
    """Cases with a complete recording for this model. Incomplete ones are left out and named by the caller."""
    out = {}
    for cid, (fen, text) in q2.load_set("dev").items():
        try:
            out[cid] = extract_v2(text, fen, q2.RecordingBackend(os.path.join(folder, "recordings", "dev"), cid, None, model), cid, reader=reader)
        except BackendError:
            pass
    return out


def replay_hybrid(model, folder) -> dict:
    out = {}
    for cid, (fen, text) in q2.load_set("dev").items():
        try:
            out[cid] = h22.extract_hybrid(text, fen, q2.RecordingBackend(os.path.join(folder, "recordings", "dev"), cid, None, model), cid)
        except BackendError:
            pass
    return out


def _on(gold, case_ids):
    return [g for g in gold if g["case"] in case_ids]


def _labelled_facts(gold, cases):
    for g in gold:
        if g.get("kind") == "variation" or g["status"] != "EXTRACTED":
            continue
        for predicate, args in g["facts"]:
            yield g, predicate, args


def _found(extraction, span, predicate, args):
    return next((c for c in extraction.facts if c.status == "EXTRACTED" and c.polarity == "positive" and qe._overlaps(span, c.span_start, c.span_end) and qe._core(c.fact) == (predicate, tuple(sorted(args.items())))), None)


def _mixed_quotes(gold, cases) -> set:
    """Concrete labels in the same sentence as an UNSUPPORTED or AMBIGUOUS label: concrete plus strategic prose."""
    out = set()
    for cid, c in cases.items():
        text, spans = c["text"], []
        for m in re.finditer(r"[^.\n]+", text):
            spans.append(m.span())
        rows = [g for g in gold if g["case"] == cid and g.get("kind") != "variation"]
        for s, e in spans:
            inside = [g for g in rows if s <= qe._span_of(text, g["quote"])[0] < e]
            if any(g["status"] != "EXTRACTED" for g in inside):
                out.update((cid, g["quote"]) for g in inside if g["status"] == "EXTRACTED")
    return out


def v2_metrics(extractions, gold, cases, judge=q2.judge_fact_v2) -> dict:
    """V1's own development metric, plus the slices the model comparison asks about."""
    cases = {k: v for k, v in cases.items() if k in extractions}
    gold = _on(gold, cases)
    r = q2.evaluate_dev(extractions, cases, gold, judge=judge)
    row = q21.dev_row(r)
    by_predicate, slices = collections.defaultdict(lambda: [0, 0]), collections.defaultdict(lambda: [0, 0])
    mixed = _mixed_quotes(gold, cases)
    for g, predicate, args in _labelled_facts(gold, cases):
        span = qe._span_of(cases[g["case"]]["text"], g["quote"])
        hit = _found(extractions[g["case"]], span, predicate, args)
        by_predicate[predicate][1] += 1
        by_predicate[predicate][0] += hit is not None
        squares = [v for k, v in args.items() if k in ("source", "target", "square", "slider", "blocker", "pinner", "pinned")]
        tags = []
        if predicate in ("attacks", "defends", "blocks", "pinned_against"):
            tags.append("attacks" if predicate == "attacks" else "other relations")
        if squares and not all(s in _SQUARE.findall(g["quote"]) for s in squares):
            tags.append("referent resolution")
        if _CHANGE_CLAIM.search(g["quote"]) and predicate in ("is_open_file", "is_semi_open_file", "is_open_diagonal", "is_passed_pawn", "has_passed_pawn", "ray_clear"):
            tags.append("change claims")
        if (g["case"], g["quote"]) in mixed:
            tags.append("mixed concrete + strategic")
        if predicate == "piece_count_is":
            tags.append("counts")
        if predicate == "material_balance_is":
            tags.append("material")
        if g.get("board"):
            tags.append("branch selection (non-root board)")
        for tag in tags:
            slices[tag][1] += 1
            slices[tag][0] += hit is not None and (tag != "branch selection (non-root board)" or hit.prefix == tuple(g["board"]))
    facts = [c for x in extractions.values() for c in x.facts]
    negatives = [c for c in facts if c.status == "EXTRACTED" and c.polarity == "negative"]
    return {**{k: list(v) if isinstance(v, tuple) else v for k, v in row.items()}, "cases": len(cases), "by_predicate": {k: list(v) for k, v in sorted(by_predicate.items())}, "slices": {k: list(v) for k, v in sorted(slices.items())},
            "negative_candidates": len(negatives), "negative_verdicts": dict(collections.Counter(judge(cases[x.source_id]["fen"], c)["status"] for x in extractions.values() for c in x.facts if c.status == "EXTRACTED" and c.polarity == "negative")),
            "rejected": sum(c.status == "PARSE_ERROR" for c in facts), "invented_quotes": sum(c.status == "PARSE_ERROR" and "quote" in (c.reason or "") for c in facts),
            "branch_rejected": sum(c.status != "EXTRACTED" and "branch" in (c.reason or "") for c in facts), "residual": sum(bool(c.residual) for c in facts), "false_verdicts_unlabelled": sum(u[5] == "FALSE" for u in r["unlabelled_candidates"])}


def dev_comparison(models) -> dict:
    """Every system on the cases each stronger model has fully recorded, so a partial run stays a fair pair.
    `models` maps a name to (model id, recordings folder). The first is the baseline."""
    gold, cases = qe.load_gold(), qe.load_cases()
    replays = {name: replay_v2(model, folder) for name, (model, folder) in models.items()}
    shared = set(cases)
    for extractions in replays.values():
        shared &= set(extractions)
    sub = {k: cases[k] for k in shared}
    pick = lambda extractions: {k: extractions[k] for k in shared}
    out = {"cases": sorted(shared), "of": len(cases), "count_taxonomy": count_taxonomy(gold), "systems": {name: v2_metrics(pick(x), gold, sub) for name, x in replays.items()}}
    out["systems"]["V1"] = q21.dev_row(qe.evaluate(sub, _on(gold, sub)))
    out["systems"]["V2.1"] = v2_metrics(pick(q21.replay("dev")), gold, sub, judge=q21.judge_hybrid)
    for name, (model, folder) in models.items():
        out["systems"][f"V2.2 + {name}"] = v2_metrics(pick(replay_hybrid(model, folder)), gold, sub, judge=q21.judge_hybrid)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("false-alarms", "record", "dev"))
    parser.add_argument("--model")
    parser.add_argument("--min-seconds", type=float, default=0.0)
    parser.add_argument("--reasoning-effort", help="a thinking model's level, kept in the recording's settings")
    parser.add_argument("--max-tokens", type=int, default=65000, help="a ceiling, not a setting that changes the answer: thinking models spend it before they write")
    args = parser.parse_args(argv)
    if args.command == "record":  # development only: no option reaches the held-out set
        return q2.record("dev", args.model, args.min_seconds, folder=OUT, live=LiteLLMBackend(args.model, max_tokens=args.max_tokens, timeout=600, reasoning_effort=args.reasoning_effort))
    if args.command == "dev":
        models = {"Flash-Lite": (DEFAULT_MODEL, q2.OUT)}
        if args.model:
            models[args.model] = (args.model, OUT)
        result = dev_comparison(models)
        q2.write_json(os.path.join(OUT, "dev_scores_paired.json" if args.model else "dev_scores.json"), result)
        print(json.dumps(result, indent=1, sort_keys=True, default=str))
        return 0
    labels = sc.load_jsonl(os.path.join(q2.HELDOUT, "labels.jsonl"))
    rows = false_alarm_rows(q21.to_predictions(q21.replay("heldout")), labels)
    q2.write_json(os.path.join(OUT, "false_alarms.json"), rows)
    text = false_alarm_report(rows)
    with open(os.path.join(OUT, "FALSE_ALARM_ANALYSIS.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
