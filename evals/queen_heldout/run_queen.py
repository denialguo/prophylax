"""Requests one QUEEN response per held-out position from the public Space, with the settings
the development outputs used. Sends only the FEN; no credentials. Saves the final response
unaltered (raw/<id>/response.json) with the text and details QUEEN returned, and never overwrites
a completed case. Stops at the first quota error: it does not wait or retry on a schedule.

Usage: ./venv/bin/python -m evals.queen_heldout.run_queen"""
import datetime
import hashlib
import json
import os
import time
import urllib.request

HERE = os.path.dirname(__file__)
HOST = "https://hugging-apps-queen-hce-4-chess.hf.space"
SPACE_API = "https://huggingface.co/api/spaces/hugging-apps/queen-hce-4-chess"
MODEL_API = "https://huggingface.co/api/models/princeton-nlp/queen_hce-4"
MAX_TOKENS, TEMPERATURE, SEED = 2048, 0.6, 20260823


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def log(entry):
    with open(os.path.join(HERE, "generation_log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"utc": now(), **entry}) + "\n")


def sha_of(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r).get("sha")


def generate(fen):
    payload = json.dumps({"data": [fen, "", MAX_TOKENS, TEMPERATURE, SEED]}).encode()
    request = urllib.request.Request(f"{HOST}/gradio_api/call/analyze", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as r:
        event_id = json.load(r)["event_id"]
    kind = ""
    with urllib.request.urlopen(f"{HOST}/gradio_api/call/analyze/{event_id}", timeout=600) as stream:
        for raw in stream:
            line = raw.decode("utf-8")
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:") and kind in ("complete", "error"):
                return kind, line[5:].strip()
    return "no_result", ""


def main():
    with open(os.path.join(HERE, "suite.json"), encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    log({"event": "start", "space_sha": sha_of(SPACE_API), "model_sha": sha_of(MODEL_API)})
    for case in cases:
        out = os.path.join(HERE, "raw", case["id"])
        if os.path.exists(os.path.join(out, "response.json")):
            continue
        started = time.monotonic()
        try:
            kind, body = generate(case["fen"])
        except Exception as e:
            log({"event": "network_error", "case": case["id"], "error": repr(e)[:300]})
            print(case["id"], "network error", repr(e)[:200], flush=True)
            return 1
        seconds = round(time.monotonic() - started, 1)
        if kind != "complete":
            log({"event": kind, "case": case["id"], "body": body[:500], "seconds": seconds})
            print(case["id"], kind, body[:200], flush=True)
            return 1
        data = json.loads(body)
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "response.json"), "w", encoding="utf-8") as f:
            f.write(body)
        with open(os.path.join(out, "explanation.txt"), "w", encoding="utf-8", newline="") as f:
            f.write(data[2])
        with open(os.path.join(out, "details.json"), "w", encoding="utf-8") as f:
            json.dump(data[3], f, indent=1)
        log({"event": "complete", "case": case["id"], "seconds": seconds,
             "explanation_sha256": hashlib.sha256(data[2].encode("utf-8")).hexdigest(),
             "response_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()})
        print(case["id"], "complete", seconds, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
