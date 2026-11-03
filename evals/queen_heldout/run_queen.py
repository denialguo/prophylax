"""Requests one QUEEN response per held-out position from the public Space, with the settings
in generation_settings.json. Resumable: completed cases are skipped. Sends only the FEN, plus
HF_TOKEN from the environment when set (for ZeroGPU quota; never logged or stored).

Saved exactly as received: raw/<id>/response.json (the final event), explanation.txt (QUEEN's
text) and details.json. Every attempt goes to generation_log.jsonl and failures also to
attempts/<id>/<utc>.json. Stops at the first error of any kind; it never waits or retries.
A completed response is only replaced with --overwrite-completed, which keeps the old one.

Usage: set -a; . app/.env; set +a; ./venv/bin/python -m evals.queen_heldout.run_queen"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import time
import urllib.request

HERE = os.path.dirname(__file__)
EXIT_OK, EXIT_ERROR, EXIT_QUOTA, EXIT_REVISION = 0, 1, 3, 4


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def auth():
    token = os.environ.get("HF_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def is_quota_error(body: str) -> bool:
    low = body.lower()
    return "quota" in low or "runs limit" in low


def fetch_sha(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r).get("sha")


def space_generate(fen, settings):
    """(event kind, final data line). The stream's intermediate frames are not kept."""
    host = settings["host"]
    payload = json.dumps({"data": [fen, "", settings["max_tokens"], settings["temperature"], settings["seed"]]}).encode()
    request = urllib.request.Request(f"{host}/gradio_api/call/{settings['api_name']}", data=payload,
                                     headers={"Content-Type": "application/json", **auth()})
    with urllib.request.urlopen(request, timeout=60) as r:
        event_id = json.load(r)["event_id"]
    kind = ""
    with urllib.request.urlopen(urllib.request.Request(f"{host}/gradio_api/call/{settings['api_name']}/{event_id}", headers=auth()),
                                timeout=600) as stream:
        for raw in stream:
            line = raw.decode("utf-8")
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:") and kind in ("complete", "error"):
                return kind, line[5:].strip()
    return "no_result", ""


def run(cases, settings, base=HERE, generate=space_generate, revisions=None, overwrite_completed=False, allow_revision_change=False):
    log_path = os.path.join(base, "generation_log.jsonl")

    def log(entry):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"utc": now(), **entry}) + "\n")

    def keep_failure(case_id, entry):
        folder = os.path.join(base, "attempts", case_id)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, stamp() + ".json"), "w", encoding="utf-8") as f:
            json.dump({"utc": now(), **entry}, f, indent=1)

    pending = [c for c in cases if overwrite_completed or not os.path.exists(os.path.join(base, "raw", c["id"], "response.json"))]
    if not pending:
        print("all cases complete", flush=True)
        return EXIT_OK
    space_sha, model_sha = revisions if revisions else (fetch_sha(settings["space_api"]), fetch_sha(settings["model_api"]))
    log({"event": "start", "space_sha": space_sha, "model_sha": model_sha, "settings_version": settings["version"]})
    if (space_sha, model_sha) != (settings["expected_space_sha"], settings["expected_model_sha"]) and not allow_revision_change:
        log({"event": "revision_changed", "space_sha": space_sha, "model_sha": model_sha})
        print(f"Stopping: the Space or model changed ({space_sha}, {model_sha}), so outputs may not be comparable. "
              "Rerun with --allow-revision-change to accept and record that.", flush=True)
        return EXIT_REVISION
    for case in pending:
        out = os.path.join(base, "raw", case["id"])
        started = time.monotonic()
        try:
            kind, body = generate(case["fen"], settings)
        except Exception as e:
            entry = {"event": "network_error", "case": case["id"], "error": repr(e)[:2000]}
            log(entry)
            keep_failure(case["id"], entry)
            print(case["id"], "network error", repr(e)[:200], flush=True)
            return EXIT_ERROR
        seconds = round(time.monotonic() - started, 1)
        if kind != "complete":
            entry = {"event": kind, "case": case["id"], "body": body, "seconds": seconds}
            log({**entry, "body": body[:500]})
            keep_failure(case["id"], entry)
            print(case["id"], kind, body[:200], flush=True)
            if is_quota_error(body):
                print("Stopping: ZeroGPU quota. Rerun the same command once it resets; completed cases are skipped.", flush=True)
                return EXIT_QUOTA
            return EXIT_ERROR
        data = json.loads(body)
        if data[3].get("fen") not in (None, case["fen"]):  # the Space answered about another position
            entry = {"event": "fen_mismatch", "case": case["id"], "got": data[3].get("fen")}
            log(entry)
            keep_failure(case["id"], {**entry, "body": body})
            return EXIT_ERROR
        if os.path.exists(out):
            shutil.move(out, os.path.join(base, "attempts", case["id"], "superseded-" + stamp()))
        os.makedirs(out)
        with open(os.path.join(out, "response.json"), "w", encoding="utf-8") as f:
            f.write(body)
        with open(os.path.join(out, "explanation.txt"), "w", encoding="utf-8", newline="") as f:
            f.write(data[2])
        with open(os.path.join(out, "details.json"), "w", encoding="utf-8") as f:
            json.dump(data[3], f, indent=1)
        log({"event": "complete", "case": case["id"], "seconds": seconds, "explanation_sha256": sha256(data[2]), "response_sha256": sha256(body)})
        print(case["id"], "complete", seconds, flush=True)
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--overwrite-completed", action="store_true", help="regenerate completed cases; the old response is kept under attempts/")
    parser.add_argument("--allow-revision-change", action="store_true", help="continue though the Space or model revision differs")
    args = parser.parse_args(argv)
    with open(os.path.join(HERE, "suite.json"), encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    with open(os.path.join(HERE, "generation_settings.json"), encoding="utf-8") as f:
        settings = json.load(f)
    return run(cases, settings, overwrite_completed=args.overwrite_completed, allow_revision_change=args.allow_revision_change)


if __name__ == "__main__":
    raise SystemExit(main())
