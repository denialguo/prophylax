"""Brings a blind labeller's returned files into the experiment. Checks them first, against the
frozen snapshot, and never overwrites existing labels. Open questions block freezing until they
are resolved, by moving each into labels or dropping it, in the returned files.

Usage: ./venv/bin/python -m evals.queen_heldout.import_blind_labels RETURNED_DIR [--freeze]"""
import argparse
import datetime
import json
import os
import shutil

import jsonschema

from evals.queen_heldout.build_labeling_package import labeller_schema
from evals.queen_heldout.freeze_predictions import HERE, labels_exist, sha256_file
from evals.queen_heldout.score import load_jsonl, load_predictions, validate_labels

TO_INTERNAL_CLASS = {"FACT": "EXTRACTED", "LINE": "EXTRACTED", "UNSUPPORTED": "UNSUPPORTED", "AMBIGUOUS": "AMBIGUOUS"}


class Refused(Exception):
    pass


def to_internal(row: dict) -> dict:
    """The labeller's neutral label_class becomes the project's extraction_class."""
    out = {k: v for k, v in row.items() if k != "label_class"}
    out["extraction_class"] = TO_INTERNAL_CLASS[row["label_class"]]
    return out


def import_labels(returned, base=HERE, freeze=False) -> dict:
    manifest_path = os.path.join(base, "predictions", "MANIFEST.json")
    if not os.path.exists(manifest_path):
        raise Refused("predictions are not frozen")
    with open(os.path.join(returned, "package.json"), encoding="utf-8") as f:
        package = json.load(f)
    manifest_sha = sha256_file(manifest_path)
    if package["snapshot"] != manifest_sha:
        raise Refused("these labels were made from a package for a different snapshot")
    if labels_exist(os.path.join(base, "labels.jsonl")):
        raise Refused("labels.jsonl already has labels: corrections go in label_corrections.jsonl")
    returned_labels = load_jsonl(os.path.join(returned, "labels.jsonl"))
    reviews = load_jsonl(os.path.join(returned, "review.jsonl"))
    if not returned_labels:
        raise Refused("no labels in the returned files")
    if freeze and reviews:
        raise Refused(f"{len(reviews)} open questions in review.jsonl: resolve them before freezing")
    _, predictions = load_predictions(os.path.join(base, "predictions"))
    texts = {}
    for case in predictions:
        with open(os.path.join(base, "raw", case, "explanation.txt"), encoding="utf-8", newline="") as f:
            texts[case] = f.read()
    with open(os.path.join(base, "labels.schema.json"), encoding="utf-8") as f:
        schema = json.load(f)
    external = labeller_schema(schema)
    for row in returned_labels:
        try:
            jsonschema.validate(row, external)
        except jsonschema.ValidationError as e:
            raise Refused(f"invalid labels:\n  {row.get('label_id', '?')}: {e.message}")
    labels = [to_internal(row) for row in returned_labels]
    problems = validate_labels(labels, texts, {c: p["fen"] for c, p in predictions.items()}, schema)
    if problems:
        raise Refused("invalid labels:\n  " + "\n  ".join(problems))
    with open(os.path.join(returned, "label_meta.json"), encoding="utf-8") as f:
        returned_meta = json.load(f)
    if not returned_meta.get("labeller"):
        raise Refused("label_meta.json does not name the labeller")
    if returned_meta.get("opened_anything_outside_this_folder") is not False:
        raise Refused("label_meta.json does not confirm the labeller stayed inside the package; record what happened before importing")
    with open(os.path.join(base, "labels.jsonl"), "w", encoding="utf-8") as f:
        f.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in labels)
    shutil.copyfile(os.path.join(returned, "labels.jsonl"), os.path.join(base, "labels_as_returned.jsonl"))
    shutil.copyfile(os.path.join(returned, "review.jsonl"), os.path.join(base, "labels_review.jsonl"))
    meta_path = os.path.join(base, "labels_meta.json")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    meta.update({"status": "frozen" if freeze else "in_review", "predictions_manifest_sha256": manifest_sha, "labeller": returned_meta["labeller"],
                 "independent_of_grammar_author": True, "predictions_opened_before_labels_frozen": False,
                 "labels_frozen_utc": datetime.datetime.now(datetime.timezone.utc).isoformat() if freeze else None})
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    return {"labels": len(labels), "open_questions": len(reviews), "status": meta["status"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("returned")
    parser.add_argument("--freeze", action="store_true", help="freeze the labels; refused while questions are open")
    args = parser.parse_args(argv)
    try:
        print(import_labels(args.returned, freeze=args.freeze))
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
