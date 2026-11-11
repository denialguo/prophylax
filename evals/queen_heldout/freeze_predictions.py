"""Blind prediction snapshot: runs the frozen extractor and verifier over the raw QUEEN responses
and writes immutable artifacts. Run once, before any labelling. No engine, no LLM, no network.

Safeguards, each a refusal:
  * the semantic code differs from the frozen commit (--override-semantic-check REASON records why)
  * python-chess is not the frozen version
  * a snapshot already exists, whatever the flags (move it aside by hand to redo one)
  * any label exists: predictions made after labelling are not blind
  * a raw response is missing (--allow-partial records a reduced suite) or differs from its logged hash
Written files are read-only. score.py re-checks every hash.

Usage: ./venv/bin/python -m evals.queen_heldout.freeze_predictions"""
import argparse
import datetime
import hashlib
import json
import os
import stat
import subprocess

import chess

from domain.extract import extract
from domain.verify import verify_variation
from evals.queen_extraction import judge_fact

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(os.path.dirname(HERE))
FROZEN_COMMIT = "0370ce1"  # originally 046bb19; history rewritten, see predictions/COMMIT_MAP.md
FROZEN_CHESS_VERSION = "1.11.2"
SEMANTIC_FILES = ("domain/extract.py", "domain/verify.py", "domain/models.py", "evals/queen_extraction.py")


class Refused(Exception):
    pass


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def committed_source(path: str) -> str:
    return git("show", f"{FROZEN_COMMIT}:{path}")


def working_source(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8", newline="") as f:
        return f.read()


def semantic_changes(read_frozen=committed_source, read_current=working_source) -> list:
    return [p for p in SEMANTIC_FILES if read_frozen(p) != read_current(p)]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def dumps(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False, default=str) + "\n"


def labels_exist(labels_path: str) -> bool:
    if not os.path.exists(labels_path):
        return False
    with open(labels_path, encoding="utf-8") as f:
        return any(line.strip() for line in f)


def logged_hashes(log_path: str) -> dict:
    """case id -> its completed entry, with the Space and model revision then in force."""
    out, revisions = {}, {}
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                if entry["event"] == "start":
                    revisions = {"space_sha": entry["space_sha"], "model_sha": entry["model_sha"]}
                elif entry["event"] == "complete":
                    out[entry["case"]] = {**entry, **revisions}
    return out


def board_after(root_fen, prefix):
    if prefix is None:
        return None
    board = chess.Board(root_fen)
    for uci in prefix:
        board.push_uci(uci)
    return board.fen()


def predict_case(case: dict, text: str, raw_sha: str) -> dict:
    fen, result = case["fen"], extract(text, case["fen"], case["id"])
    facts = []
    for c in result.facts:
        judged = judge_fact(fen, c)
        facts.append({
            "status": c.status, "sentence": c.sentence, "span_start": c.span_start, "span_end": c.span_end,
            "source_text": text[c.span_start:c.span_end], "reason": c.reason,
            "predicate": c.fact.predicate if c.fact else None, "args": c.fact.args if c.fact else None,
            "guards": [g.model_dump() for g in c.guards],
            "change_from": c.change_from.model_dump() if c.change_from else None,
            "board_moves": list(c.prefix) if c.prefix is not None else None,
            "board_fen": board_after(fen, c.prefix) if c.status == "EXTRACTED" else None,
            "verifier_status": judged["status"] if judged else None,
            "verifier_reason": judged["reason"] if judged else None,
            "verifier_evidence": judged["results"] if judged else None})
    variations = []
    for v in result.variations:
        judged = verify_variation(fen, list(v.plies)) if v.status == "EXTRACTED" else None
        variations.append({
            "status": v.status, "kind": v.kind, "sentence": v.sentence, "span_start": v.span_start, "span_end": v.span_end,
            "reason": v.reason, "moves": list(v.ucis), "plies": [p.model_dump() for p in v.plies],
            "verifier_status": judged.status if judged else None,
            "verifier_first_false_ply": judged.first_false_ply if judged else None,
            "verifier_result": judged.model_dump() if judged else None})
    return {"case": case["id"], "category": case["category"], "fen": fen, "raw_explanation_sha256": raw_sha,
            "extractor_commit": commit_of("domain/extract.py"), "verifier_commit": commit_of("domain/verify.py"),
            "facts": facts, "variations": variations}


def commit_of(path: str) -> str:
    return git("log", "-1", "--format=%H", FROZEN_COMMIT, "--", path).strip()


def freeze(cases, raw_dir, out_dir, labels_path, log_path, settings, override_reason=None, allow_partial=False,
           changes=semantic_changes, chess_version=chess.__version__) -> dict:
    drift = changes()
    if drift and not override_reason:
        raise Refused(f"semantic code differs from {FROZEN_COMMIT}: {drift}. These predictions would not evaluate the frozen system.")
    if chess_version != FROZEN_CHESS_VERSION:
        raise Refused(f"python-chess is {chess_version}, the frozen system used {FROZEN_CHESS_VERSION}")
    manifest_path = os.path.join(out_dir, "MANIFEST.json")
    if os.path.exists(manifest_path):
        raise Refused(f"{manifest_path} exists: predictions are already frozen. To redo them, move that folder aside by hand and say why.")
    if labels_exist(labels_path):
        raise Refused(f"{labels_path} has labels: predictions made now would not be blind.")
    logged = logged_hashes(log_path)
    present, missing = [], []
    for case in cases:
        path = os.path.join(raw_dir, case["id"], "explanation.txt")
        (present if os.path.exists(path) else missing).append(case)
    if not present:
        raise Refused("no raw responses to freeze")
    if missing and not allow_partial:
        raise Refused(f"{len(missing)} of {len(cases)} responses missing: {[c['id'] for c in missing]}. "
                      "Generate them, or pass --allow-partial to freeze a reduced suite.")
    rows = []
    os.makedirs(out_dir, exist_ok=True)
    pending = []
    for case in present:
        path = os.path.join(raw_dir, case["id"], "explanation.txt")
        with open(path, "rb") as f:
            raw = f.read()
        raw_sha = sha256_bytes(raw)
        entry = logged.get(case["id"])
        if entry is None or entry["explanation_sha256"] != raw_sha:
            raise Refused(f"{case['id']}: the raw text does not match generation_log.jsonl")
        with open(os.path.join(raw_dir, case["id"], "details.json"), encoding="utf-8") as f:
            if json.load(f).get("fen") != case["fen"]:
                raise Refused(f"{case['id']}: the response is about a different position than the suite")
        body = dumps(predict_case(case, raw.decode("utf-8"), raw_sha))
        pending.append((case, body, raw_sha, entry))
    for case, body, raw_sha, entry in pending:
        name = case["id"] + ".json"
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        rows.append({"case": case["id"], "category": case["category"], "fen": case["fen"], "provenance": case.get("provenance"),
                     "raw_explanation_sha256": raw_sha, "raw_response_sha256": entry.get("response_sha256"),
                     "space_sha": entry.get("space_sha"), "model_sha": entry.get("model_sha"),
                     "prediction_file": name, "prediction_sha256": sha256_bytes(body.encode("utf-8"))})
    manifest = {"frozen_commit": FROZEN_COMMIT, "extractor_commit": commit_of("domain/extract.py"), "verifier_commit": commit_of("domain/verify.py"),
                "semantic_files": list(SEMANTIC_FILES), "semantic_override": override_reason, "semantic_drift": drift,
                "python_chess": chess_version, "model": settings["model"], "generation_settings": settings,
                "complete_suite": not missing, "missing_cases": [c["id"] for c in missing],
                "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "cases": rows}
    with open(manifest_path, "w", encoding="utf-8", newline="") as f:
        f.write(dumps(manifest))
    os.chmod(manifest_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--override-semantic-check", metavar="REASON", help="freeze despite changed semantic code; recorded in the manifest")
    parser.add_argument("--allow-partial", action="store_true", help="freeze the responses that exist; recorded in the manifest")
    args = parser.parse_args(argv)
    with open(os.path.join(HERE, "suite.json"), encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    with open(os.path.join(HERE, "generation_settings.json"), encoding="utf-8") as f:
        settings = json.load(f)
    try:
        m = freeze(cases, os.path.join(HERE, "raw"), os.path.join(HERE, "predictions"), os.path.join(HERE, "labels.jsonl"),
                   os.path.join(HERE, "generation_log.jsonl"), settings, args.override_semantic_check, args.allow_partial)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    print(f"Frozen {len(m['cases'])} cases at {FROZEN_COMMIT}. Manifest sha256 {sha256_file(os.path.join(HERE, 'predictions', 'MANIFEST.json'))}")
    print("Record that hash in labels_meta.json before labelling, and commit the predictions first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
