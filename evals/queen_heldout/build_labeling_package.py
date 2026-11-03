"""Builds the standalone folder for a blind labeller: case ids, root FENs, QUEEN's text, the label
format and the predicate vocabulary. Nothing about how any software reads these texts goes in.
Refuses until predictions are frozen, so labelling cannot start out of order.

Usage: ./venv/bin/python -m evals.queen_heldout.build_labeling_package --out DIR [--template-only]
Hand over the folder or its .zip only, not this repository."""
import argparse
import hashlib
import json
import os
import shutil

from evals.queen_heldout.freeze_predictions import HERE, sha256_file

TEMPLATE = os.path.join(HERE, "labeling_template")
TEMPLATE_FILES = ("LABELING_INSTRUCTIONS.md", "HANDOFF.md", "predicates.json", "validate_labels.py", "label_tools.py")
LABEL_DESCRIPTION = ("One label per line of labels.jsonl. See LABELING_INSTRUCTIONS.md. Label what the text means and "
                     "whether it is true on the board.")


class Refused(Exception):
    pass


def labeller_schema(schema: dict) -> dict:
    """The held-out schema without its internal notes. A subset, so labels written against
    it are valid against the full one."""
    out = json.loads(json.dumps(schema))
    out["description"] = LABEL_DESCRIPTION
    out["properties"].pop("known_issue", None)
    return out


def build(out_dir, base=HERE, template_only=False, make_zip=True) -> dict:
    if os.path.exists(out_dir) and os.listdir(out_dir):
        raise Refused(f"{out_dir} already has files: choose an empty folder")
    cases, manifest_sha = [], None
    if not template_only:
        manifest_path = os.path.join(base, "predictions", "MANIFEST.json")
        if not os.path.exists(manifest_path):
            raise Refused("predictions are not frozen yet: run freeze_predictions.py first, so labels cannot precede them")
        with open(manifest_path, encoding="utf-8") as f:
            cases = json.load(f)["cases"]
        manifest_sha = sha256_file(manifest_path)
    os.makedirs(out_dir, exist_ok=True)
    for name in TEMPLATE_FILES:
        shutil.copyfile(os.path.join(TEMPLATE, name), os.path.join(out_dir, name))
    with open(os.path.join(base, "labels.schema.json"), encoding="utf-8") as f:
        schema = labeller_schema(json.load(f))
    with open(os.path.join(out_dir, "labels.schema.json"), "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=1)
    for name in ("labels.jsonl", "review.jsonl"):
        open(os.path.join(out_dir, name), "w").close()
    with open(os.path.join(out_dir, "label_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"labeller": None, "finished_utc": None, "opened_anything_outside_this_folder": None, "notes": None}, f, indent=1)
    rows = []
    os.makedirs(os.path.join(out_dir, "cases"), exist_ok=True)
    for row in cases:
        source = os.path.join(base, "raw", row["case"], "explanation.txt")
        if sha256_file(source) != row["raw_explanation_sha256"]:
            raise Refused(f"{row['case']}: the raw text no longer matches the frozen manifest")
        folder = os.path.join(out_dir, "cases", row["case"])
        os.makedirs(folder)
        shutil.copyfile(source, os.path.join(folder, "response.txt"))
        with open(os.path.join(folder, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"id": row["case"], "fen": row["fen"]}, f, indent=1)
        rows.append({"id": row["case"], "response_sha256": row["raw_explanation_sha256"]})
    package = {"package": "chess analysis labelling", "snapshot": manifest_sha, "cases": rows}
    with open(os.path.join(out_dir, "package.json"), "w", encoding="utf-8") as f:
        json.dump(package, f, indent=1)
    if make_zip:
        shutil.make_archive(out_dir.rstrip(os.sep), "zip", out_dir)
    return package


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--template-only", action="store_true", help="the package with no cases, to review its contents")
    args = parser.parse_args(argv)
    try:
        package = build(args.out, template_only=args.template_only)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    print(f"Built {args.out} with {len(package['cases'])} cases (and {args.out.rstrip(os.sep)}.zip). Handoff text is in HANDOFF.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
