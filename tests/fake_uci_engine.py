"""Minimal fake UCI engine for failure-mode tests.

FAKE_ENGINE_MODE: "hang" never answers `go`; "crash" exits on `go`;
"hang_after_first" answers the first `go` (start position) then hangs;
"answer" answers every `go` with a legal move. FAKE_ENGINE_LOG, if set, gets
every command received, one per line.
"""
import os
import sys

import chess

MODE = os.environ.get("FAKE_ENGINE_MODE", "hang")
LOG = os.environ.get("FAKE_ENGINE_LOG")
answered = False
board = chess.Board()

for line in sys.stdin:
    cmd = line.strip()
    if LOG:
        with open(LOG, "a") as f:
            f.write(cmd + "\n")
    if cmd.startswith("position"):
        head, _, moves = cmd.partition(" moves ")
        board = chess.Board() if head.split()[1] == "startpos" else chess.Board(head.split(None, 2)[2])
        for uci in moves.split():
            board.push_uci(uci)
    if cmd == "uci":
        print("id name Stockfish 18 fake")
        # defaults differ from the pinned values so the pins must be sent explicitly
        print("option name Threads type spin default 8 min 1 max 1024")
        print("option name Hash type spin default 64 min 1 max 33554432")
        print("option name MultiPV type spin default 1 min 1 max 500")
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
    elif cmd.startswith("go") and MODE == "answer":
        move = next(iter(board.legal_moves), None)
        if move:
            print(f"info depth 1 score cp 0 wdl 300 400 300 pv {move.uci()}")
        print(f"bestmove {move.uci() if move else '(none)'}", flush=True)
    elif cmd == "quit":
        break
