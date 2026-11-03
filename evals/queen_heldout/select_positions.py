"""Held-out suite for the blind QUEEN extraction evaluation. Positions come from seeded
Stockfish self-play and fixed rules, so nobody chooses them by how a grammar would read them.

Rules, applied to a shuffled pool with at most one position per game:
  endgame    no queens, or at most 4 non-pawn pieces
  tactical   not an endgame; best move at least 150cp better than the second; |eval| <= 600cp
  structure  not an endgame; gap < 60cp; at least 2 isolated/backward/doubled pawns
  quiet      not an endgame; gap < 40cp; at most 1 such pawn; no capture among the top two moves

Version 2 keeps the first half of each category from suite_v1.json (self-play; h01 and h02 already
have outputs) and fills the rest from real games in the player-history database, by the same rules.
No v2 position was chosen after seeing any QUEEN output. Only FENs leave this machine.

Usage: STOCKFISH_PATH=... ./venv/bin/python -m evals.queen_heldout.select_positions [--v1]"""
import argparse
import json
import os
import random
import sqlite3

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


def measure(engine, board):
    infos = engine.analyse(board, chess.engine.Limit(nodes=NODES), multipv=4)
    return infos, classify(board, infos)


def describe(board, infos, kind):
    first = score(infos[0], board.turn)
    gap = first - score(infos[1], board.turn) if len(infos) > 1 else None
    return f"{kind} rule at {NODES:,} nodes: best move {gap}cp ahead of the second, eval {first}cp, {structure_count(board)} isolated/backward/doubled pawns"


def coverage(board):
    """What the position offers to talk about. Recorded only; never used to select."""
    pawns = [(s, c) for c in chess.COLORS for s in board.pieces(chess.PAWN, c)]
    files = [f for f in range(8) if not any(chess.square_file(s) == f for s, _ in pawns)]
    def passed(s, c):
        f, r = chess.square_file(s), chess.square_rank(s)
        return not any(abs(chess.square_file(o) - f) <= 1 and (chess.square_rank(o) > r if c else chess.square_rank(o) < r)
                       for o, oc in pawns if oc != c)
    pinned = sum(board.is_pinned(p.color, s) for s, p in board.piece_map().items() if p.piece_type != chess.KING)
    return {"open_files": len(files), "passed_pawns": sum(passed(s, c) for s, c in pawns), "absolute_pins": pinned,
            "legal_captures": sum(board.is_capture(m) for m in board.legal_moves),
            "legal_checks": sum(board.gives_check(m) for m in board.legal_moves), "pieces": len(board.piece_map())}


def main_v2():
    with open(os.path.join(HERE, "suite_v1.json"), encoding="utf-8") as f:
        v1 = json.load(f)["cases"]
    with open(os.path.join(os.path.dirname(HERE), "queen_benchmark", "cases.json"), encoding="utf-8") as f:
        excluded = {c["fen"].rsplit(" ", 2)[0] for c in json.load(f)["cases"]}
    db = os.environ.get("PROPHYLAX_HISTORY_DB", os.path.join(os.path.dirname(os.path.dirname(HERE)), "data", "prophylax_history.sqlite3"))
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    games = [r[0] for r in conn.execute("SELECT game_pk FROM games WHERE exclusion_reason IS NULL ORDER BY game_pk")]
    rng = random.Random(SEED + 2)
    rng.shuffle(games)
    keep = {}
    for c in v1:
        keep.setdefault(c["category"], [])
        if len(keep[c["category"]]) < QUOTA[c["category"]] // 2:
            keep[c["category"]].append(c)
    used = {c["fen"].rsplit(" ", 2)[0] for cs in keep.values() for c in cs}
    real = {k: [] for k in QUOTA}
    engine = chess.engine.SimpleEngine.popen_uci(os.environ["STOCKFISH_PATH"])
    engine.configure({"Threads": 1, "Hash": 16})
    scanned = 0
    try:
        for pk in games:
            if all(len(real[k]) >= QUOTA[k] - QUOTA[k] // 2 for k in QUOTA):
                break
            scanned += 1
            rows = conn.execute("SELECT m.ply, m.fen_before FROM move_analyses m JOIN derivations d USING (derivation_id) "
                                "JOIN engine_runs r USING (run_id) WHERE r.game_pk=? AND m.ply>=14 ORDER BY m.ply", (pk,)).fetchall()
            rng.shuffle(rows)
            for ply, fen in rows[:12]:  # one position per game, from a bounded sample of its plies
                board = chess.Board(fen)
                if fen.rsplit(" ", 2)[0] in excluded | used:
                    continue
                infos, kind = measure(engine, board)
                if kind and len(real[kind]) < QUOTA[kind] - QUOTA[kind] // 2:
                    real[kind].append((pk, ply, fen))
                    used.add(fen.rsplit(" ", 2)[0])
                    break
        cases = []
        for kind in QUOTA:
            for c in keep[kind]:
                board = chess.Board(c["fen"])
                infos, _ = measure(engine, board)
                cases.append({"category": kind, "fen": c["fen"], "provenance": f"Stockfish self-play, seed {SEED} (suite_v1 {c['id']})",
                              "reason": describe(board, infos, kind), "coverage": coverage(board), "keep_id": c["id"]})
            for pk, ply, fen in real[kind]:
                board = chess.Board(fen)
                infos, _ = measure(engine, board)
                cases.append({"category": kind, "fen": fen, "provenance": f"player history, game {pk[:8]}, ply {ply}",
                              "reason": describe(board, infos, kind), "coverage": coverage(board)})
    finally:
        engine.quit()
    for n, c in enumerate(cases):
        c["id"] = c.pop("keep_id", None) or f"h{n + 1:02d}_{c['category']}"
    with open(os.path.join(HERE, "suite.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 2, "seed": SEED, "real_game_seed": SEED + 2, "nodes": NODES, "real_games_scanned": scanned,
                   "split": "first half of each category from suite_v1 (self-play), the rest from player history",
                   "cases": cases}, f, indent=1)
    print(scanned, {k: len(v) for k, v in real.items()}, len(cases))


def main_v1():
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
    with open(os.path.join(HERE, "suite_v1.json"), "w", encoding="utf-8") as f:
        json.dump({"seed": SEED, "nodes": NODES, "games": game, "rules": classify.__doc__ or "see module docstring",
                   "cases": cases}, f, indent=1)
    print(game, {k: len(v) for k, v in chosen.items()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1", action="store_true", help="regenerate the original all-self-play suite instead")
    main_v1() if parser.parse_args().v1 else main_v2()
