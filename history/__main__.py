"""python -m history import|analyze|rederive|status|profile  (Phase C, M15/M16)

  import   --pgn FILE [--player NAME ...]   games -> history DB (idempotent)
  analyze  [--limit N]                      eligible games without a 100k run: Stockfish via the MCP server
  rederive                                  bring derivations to the current versions (no engine)
  status                                    what the database holds
  profile  [--window 20] [...]              recurring weaknesses (M16)

The database is PROPHYLAX_HISTORY_DB (default data/prophylax_history.sqlite3); the
player is PROPHYLAX_PLAYER_NAMES unless --player is given.
"""
import argparse
import asyncio
import json
import os
import sys
import time

from config.settings import (HISTORY_NODES, MAX_FLAGS, PINNED_STOCKFISH_VERSION, STOCKFISH_HASH_MB, STOCKFISH_THREADS,
                             get_history_db_path, get_player_names)
from domain.versions import ANALYZER_VERSION, CLAIMS_VERSION
from history import derive as hd
from history.db import connect
from history.importer import import_pgn


def cmd_import(conn, args):
    print(json.dumps(import_pgn(conn, args.pgn, args.player or get_player_names()), indent=1))


def _check_config(cfg: dict) -> None:
    """The server must have analysed at the history budget with the pinned engine."""
    want = {"nodes": HISTORY_NODES, "threads": STOCKFISH_THREADS, "hash_mb": STOCKFISH_HASH_MB, "multipv": 1}
    got = {k: cfg.get(k) for k in want}
    if got != want or not str(cfg.get("engine", "")).startswith(f"Stockfish {PINNED_STOCKFISH_VERSION}"):
        raise SystemExit(f"analysis_config {cfg} does not match the history policy {want}")


async def _analyze(conn, limit):
    from app.agent import call_mcp_tool_subprocess  # the only route to Stockfish
    todo = conn.execute(
        "SELECT game_pk, movetext FROM games g WHERE exclusion_reason IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM engine_runs r WHERE r.game_pk = g.game_pk AND r.nodes = ?) "
        "ORDER BY played_on, end_time", (HISTORY_NODES,)).fetchall()[:limit]
    start = time.time()
    for i, g in enumerate(todo, 1):
        payload = await call_mcp_tool_subprocess("analyze_pgn", {"pgn": g["movetext"], "max_flags": MAX_FLAGS,
                                                                 "include_searches": True})
        _check_config(payload["analysis_config"])
        run = hd.store_engine_run(conn, g["game_pk"], payload)
        hd.derive(conn, run)  # commits: the batch is resumable after any interruption
        print(f"{i}/{len(todo)} {g['game_pk'][:10]} {time.time() - start:.0f}s", file=sys.stderr, flush=True)
    return {"analyzed": len(todo), "wall_clock_s": round(time.time() - start, 1)}


def cmd_analyze(conn, args):
    os.environ["STOCKFISH_NODES"] = str(HISTORY_NODES)  # the server subprocess inherits it (C3)
    print(json.dumps(asyncio.run(_analyze(conn, args.limit)), indent=1))


def cmd_rederive(conn, args):
    print(json.dumps(hd.rederive_all(conn), indent=1))


def status(conn) -> dict:
    q = lambda sql, *a: [dict(r) for r in conn.execute(sql, a)]
    return {
        "versions": {"analyzer": ANALYZER_VERSION, "claims": CLAIMS_VERSION},
        "games": q("SELECT source_kind, COALESCE(exclusion_reason, 'eligible') AS status, COUNT(*) AS n "
                   "FROM games GROUP BY 1, 2 ORDER BY 1, 2"),
        "engine_runs": q("SELECT engine, nodes, threads, hash_mb, COUNT(*) AS n FROM engine_runs GROUP BY 1,2,3,4"),
        "derivations": q("SELECT analyzer_version, claims_version, COUNT(*) AS n, "
                         "analyzer_version = ? AND claims_version = ? AS current FROM derivations GROUP BY 1, 2",
                         ANALYZER_VERSION, CLAIMS_VERSION),
        "mistake_events": q("SELECT c.category, COUNT(DISTINCT e.event_id) AS events FROM mistake_events e "
                            "JOIN mistake_categories c USING (event_id) GROUP BY 1 ORDER BY 2 DESC"),
    }


def cmd_status(conn, args):
    print(json.dumps(status(conn), indent=1))


def cmd_profile(conn, args):
    from history.profile import DEFAULT_TC, WINDOW, profile, render
    tc = tuple(args.tc or DEFAULT_TC) + (("bullet",) if args.include_bullet else ())
    p = profile(conn, window=args.window or WINDOW, tc_classes=tc, since=args.since, until=args.until)
    print(json.dumps(p, indent=1) if args.json else render(p))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m history", description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None, help="database path (default PROPHYLAX_HISTORY_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import")
    p.add_argument("--pgn", required=True)
    p.add_argument("--player", action="append")
    p = sub.add_parser("analyze")
    p.add_argument("--limit", type=int, default=None)
    sub.add_parser("rederive")
    sub.add_parser("status")
    p = sub.add_parser("profile", help="what positional mistakes do I repeatedly make?")
    p.add_argument("--window", type=int, default=None, help="games per window (default 20)")
    p.add_argument("--tc", action="append", choices=["blitz", "rapid", "classical", "bullet"],
                   help="time classes (default blitz, rapid, classical)")
    p.add_argument("--include-bullet", action="store_true")
    p.add_argument("--since", help="YYYY-MM-DD")
    p.add_argument("--until", help="YYYY-MM-DD")
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    conn = connect(args.db or get_history_db_path())
    {"import": cmd_import, "analyze": cmd_analyze, "rederive": cmd_rederive, "status": cmd_status,
     "profile": cmd_profile}[args.cmd](conn, args)


if __name__ == "__main__":
    main()
