"""Held-out suite for the blind QUEEN extraction evaluation. Positions come from seeded
Stockfish self-play and fixed rules, so nobody chooses them by how a grammar would read them.

Rules, applied to a shuffled pool with at most one position per game:
  endgame    no queens, or at most 4 non-pawn pieces
  tactical   not an endgame; best move at least 150cp better than the second; |eval| <= 600cp
  structure  not an endgame; gap < 60cp; at least 2 isolated/backward/doubled pawns
  quiet      not an endgame; gap < 40cp; at most 1 such pawn; no capture among the top two moves

Usage: STOCKFISH_PATH=... ./venv/bin/python -m evals.queen_heldout.select_positions"""
import json
import os
import random

import chess
import chess.engine

from mcp_server.features import compute_pawn_structure

SEED = 20261103
QUOTA = {"tactical": 8, "quiet": 8, "structure": 4, "endgame": 4}
NODES = 50_000
HERE = os.path.dirname(__file__)


def score(info, color):
    return info["score"].pov(color).score(mate_score=100_000)


def structure_count(board):
    ps = compute_pawn_structure(board)
    return sum(len(ps[c][k]) for c in ("white", "black") for k in ("isolated_pawns", "backward_pawns", "doubled_pawns"))


def classify(board, infos):
    if board.is_game_over() or board.is_check() or len(infos) < 2:
        return None
    pieces = sum(len(board.pieces(t, c)) for t in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN) for c in chess.COLORS)
    if not board.pieces(chess.QUEEN, chess.WHITE) and not board.pieces(chess.QUEEN, chess.BLACK) or pieces <= 4:
        return "endgame" if abs(score(infos[0], board.turn)) <= 900 and pieces >= 1 else None
    first, second = score(infos[0], board.turn), score(infos[1], board.turn)
    gap, pawns = first - second, structure_count(board)
    if gap >= 150 and abs(first) <= 600:
        return "tactical"
    if gap < 60 and pawns >= 2:
        return "structure"
    captures = any(board.is_capture(i["pv"][0]) for i in infos[:2])
    if gap < 40 and pawns <= 1 and not captures:
        return "quiet"
    return None


def play_game(engine, rng):
    board, pool = chess.Board(), []
    for ply in range(rng.randint(70, 110)):
        if board.is_game_over():
            break
        infos = engine.analyse(board, chess.engine.Limit(nodes=NODES), multipv=4)
        if ply >= 14:
            kind = classify(board, infos)
            if kind:
                pool.append((kind, board.fen()))
        choices = infos if ply < 8 else infos[:2] if rng.random() < 0.2 else infos[:1]
        board.push(rng.choice(choices)["pv"][0])
    return pool


def main():
    with open(os.path.join(os.path.dirname(HERE), "queen_benchmark", "cases.json"), encoding="utf-8") as f:
        excluded = {c["fen"].rsplit(" ", 2)[0] for c in json.load(f)["cases"]}
    rng = random.Random(SEED)
    chosen, used = {k: [] for k in QUOTA}, set()
    engine = chess.engine.SimpleEngine.popen_uci(os.environ["STOCKFISH_PATH"])
    engine.configure({"Threads": 1, "Hash": 16})
    game = 0
    try:
        while any(len(chosen[k]) < n for k, n in QUOTA.items()) and game < 200:
            pool = play_game(engine, rng)
            rng.shuffle(pool)
            for kind, fen in pool:
                key = fen.rsplit(" ", 2)[0]
                if len(chosen[kind]) < QUOTA[kind] and key not in excluded and key not in used:
                    chosen[kind].append(fen)
                    used.add(key)
                    break  # one position per game
            game += 1
    finally:
        engine.quit()
    cases = [{"id": f"h{n + 1:02d}_{kind}", "category": kind, "fen": fen}
             for n, (kind, fen) in enumerate((k, f) for k in QUOTA for f in chosen[k])]
    with open(os.path.join(HERE, "suite.json"), "w", encoding="utf-8") as f:
        json.dump({"seed": SEED, "nodes": NODES, "games": game, "rules": classify.__doc__ or "see module docstring",
                   "cases": cases}, f, indent=1)
    print(game, {k: len(v) for k, v in chosen.items()})


if __name__ == "__main__":
    main()
