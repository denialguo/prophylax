"""Minimal fake UCI engine for failure-mode tests.

FAKE_ENGINE_MODE: "hang" never answers `go`; "crash" exits on `go`;
"hang_after_first" answers the first `go` (start position) then hangs.
"""
import os
import sys

MODE = os.environ.get("FAKE_ENGINE_MODE", "hang")
answered = False

for line in sys.stdin:
    cmd = line.strip()
    if cmd == "uci":
        print("id name Stockfish 18 fake")
        print("option name Threads type spin default 1 min 1 max 1024")
        print("option name Hash type spin default 16 min 1 max 33554432")
        print("option name UCI_ShowWDL type check default false")
        print("uciok", flush=True)
    elif cmd == "isready":
        print("readyok", flush=True)
    elif cmd.startswith("go") and MODE == "crash":
        sys.exit(1)
    elif cmd.startswith("go") and MODE == "hang_after_first" and not answered:
        answered = True
        print("info depth 1 score cp 20 wdl 400 400 200 pv e2e4")
        print("bestmove e2e4", flush=True)
    elif cmd == "quit":
        break
