"""Where the held-out experiment stands, and the next step. Reads counts and hashes only: it
never prints QUEEN's text, a prediction or a label, so it is safe to run during a blind stage.

Usage: ./venv/bin/python -m evals.queen_heldout.status"""
import json
import os

HERE = os.path.dirname(__file__)


def count_lines(path):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def stage(base=HERE) -> dict:
    with open(os.path.join(base, "suite.json"), encoding="utf-8") as f:
        total = len(json.load(f)["cases"])
    raw_dir = os.path.join(base, "raw")
    done = sum(os.path.exists(os.path.join(raw_dir, c, "explanation.txt")) for c in os.listdir(raw_dir)) if os.path.isdir(raw_dir) else 0
    frozen = os.path.exists(os.path.join(base, "predictions", "MANIFEST.json"))
    labels = count_lines(os.path.join(base, "labels.jsonl"))
    with open(os.path.join(base, "labels_meta.json"), encoding="utf-8") as f:
        status = json.load(f)["status"]
    scored = os.path.exists(os.path.join(base, "scores.json"))
    reported = os.path.exists(os.path.join(base, "REPORT.md"))
    if done < total:
        step = "Generate outputs: python -m evals.queen_heldout.run_queen (rerun until complete)"
    elif not frozen:
        step = "Freeze predictions: python -m evals.queen_heldout.freeze_predictions, then commit"
    elif not labels:
        step = "Build the package (build_labeling_package) and hand it to the blind labeller"
    elif status != "frozen":
        step = "Resolve open label questions, then import_blind_labels --freeze"
    elif not scored:
        step = "Score: python -m evals.queen_heldout.score"
    elif not reported:
        step = "Report: python -m evals.queen_heldout.generate_report"
    else:
        step = "Complete. Review REPORT.md"
    return {"outputs": done, "total": total, "predictions_frozen": frozen, "labels": labels, "labels_status": status,
            "scored": scored, "reported": reported, "next": step}


def main():
    s = stage()
    print(f"Outputs:     {s['outputs']} / {s['total']}")
    print(f"Predictions: {'frozen' if s['predictions_frozen'] else 'not frozen'}")
    print(f"Labels:      {s['labels']} ({s['labels_status']})")
    print(f"Scored:      {'yes' if s['scored'] else 'no'}")
    print(f"Report:      {'yes' if s['reported'] else 'no'}")
    print(f"Next:        {s['next']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
