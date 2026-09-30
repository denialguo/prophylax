"""Engine runs -> derived history (M15). The engine layer (searches) is stored once per
game and configuration; everything else is re-derived from it through the live code
path: mcp_server.server.derive_game_payload, domain.convert, domain.claims and the
server's own feature functions. There is no history-specific chess logic here."""
import datetime
import io
import json
import sqlite3
import subprocess
from typing import Optional

import chess
import chess.pgn

from config.settings import MAX_FLAGS
from domain.claims import build_claims
from domain.convert import game_analysis_from_payload
from domain.versions import ANALYZER_VERSION, CLAIMS_VERSION
from mcp_server.features import get_quiet_concessions
from mcp_server.server import derive_game_payload, searches_from_json

# Categories are the structural claim types (M14 benchmark vocabulary). The composite
# quiet_structural_concession is not a category: its parts already are.
CATEGORIES = ("weak_square_created", "backward_pawn_created", "pawn_support_lost", "king_safety_reduced")
UNCLASSIFIED = "unclassified"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _code_ref() -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except Exception:
        return None


def store_engine_run(conn: sqlite3.Connection, game_pk: str, payload: dict) -> int:
    """Persist one analyze_pgn(include_searches=True) result's engine layer; returns the
    run id. The same configuration for the same game is stored once (immutable)."""
    cfg = payload["analysis_config"]
    key = (game_pk, cfg["engine"], cfg["nodes"], cfg["threads"], cfg["hash_mb"], cfg["multipv"])
    row = conn.execute("SELECT run_id FROM engine_runs WHERE game_pk=? AND engine=? AND nodes=? AND threads=? "
                       "AND hash_mb=? AND multipv=?", key).fetchone()
    if row:
        return row["run_id"]
    cur = conn.execute("INSERT INTO engine_runs (game_pk, engine, nodes, threads, hash_mb, multipv, created_at, "
                       "code_ref) VALUES (?,?,?,?,?,?,?,?)", key + (_now(), _code_ref()))
    run_id = cur.lastrowid
    conn.executemany("INSERT INTO engine_positions (run_id, position, wins, draws, losses, pv_uci) VALUES (?,?,?,?,?,?)",
                     [(run_id, i, *(wdl or (None, None, None)), " ".join(pv))
                      for i, (wdl, pv) in enumerate(payload["searches"])])
    return run_id


def _stored_searches(conn, run_id) -> list:
    rows = conn.execute("SELECT wins, draws, losses, pv_uci FROM engine_positions WHERE run_id=? ORDER BY position",
                        (run_id,)).fetchall()
    return [[[r["wins"], r["draws"], r["losses"]] if r["wins"] is not None else None,
             r["pv_uci"].split() if r["pv_uci"] else []] for r in rows]


def _position_state(wp: Optional[float]) -> Optional[str]:
    if wp is None:
        return None
    return "winning" if wp >= 0.8 else "losing" if wp <= 0.2 else "balanced"


def derive(conn: sqlite3.Connection, run_id: int) -> int:
    """Derive (or return the existing) current-version derivation of one engine run."""
    have = conn.execute("SELECT derivation_id FROM derivations WHERE run_id=? AND analyzer_version=? AND "
                        "claims_version=?", (run_id, ANALYZER_VERSION, CLAIMS_VERSION)).fetchone()
    if have:
        return have["derivation_id"]
    game_row = conn.execute("SELECT g.* FROM games g JOIN engine_runs r USING (game_pk) WHERE r.run_id=?",
                            (run_id,)).fetchone()
    game = chess.pgn.read_game(io.StringIO(game_row["movetext"]))
    moves = list(game.mainline_moves())
    searches = searches_from_json(game.board(), moves, _stored_searches(conn, run_id))
    payload = derive_game_payload(game, moves, searches, MAX_FLAGS)  # every flag, not the live 4
    analysis = game_analysis_from_payload(payload, game_row["movetext"])
    player = game_row["player_color"]

    cur = conn.execute("INSERT INTO derivations (run_id, analyzer_version, claims_version, created_at) "
                       "VALUES (?,?,?,?)", (run_id, ANALYZER_VERSION, CLAIMS_VERSION, _now()))
    did = cur.lastrowid
    board = game.board()
    for move, mv in zip(analysis.moves, moves):
        after = board.copy()
        after.push(mv)
        # the server's own concession function, for every ply (base rates for M16)
        conc = {k: sorted(v) for k, v in get_quiet_concessions(board, after, board.turn).items()}
        d = move.detail
        conn.execute(
            "INSERT INTO move_analyses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, move.ply, move.move_number, move.side, move.san, mv.uci(), int(move.side == player), move.phase,
             move.fen_before, move.evaluation.delta, move.best_move,
             d.channel if d else None, d.rank if d else None,
             move.evaluation.before, move.evaluation.after,
             json.dumps(list(d.pv.moves)) if d else None, json.dumps(list(d.refutation.moves)) if d else None,
             json.dumps({f.name: f.value for f in d.feature_deltas}) if d else None, json.dumps(conc)))
        if d:
            claims = build_claims(move, analysis.game_id)  # board evidence re-checked against the payload
            conn.executemany("INSERT INTO claims VALUES (?,?,?,?,?,?,?)",
                             [(did, c.claim_id, c.ply, c.type, c.subject,
                               json.dumps([[e.provenance, e.fact, e.value] for e in c.evidence]),
                               json.dumps(list(c.supports))) for c in claims])
            if move.side == player:
                ev = conn.execute(
                    "INSERT INTO mistake_events (derivation_id, ply, game_pk, phase, channel, wdl_delta, wp_before, "
                    "position_state, fen_before, played_san, best_move_san) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (did, move.ply, game_row["game_pk"], move.phase, d.channel, move.evaluation.delta,
                     move.evaluation.before, _position_state(move.evaluation.before), move.fen_before, move.san,
                     move.best_move)).lastrowid
                cats = sorted({(c.type, c.subject or "") for c in claims if c.type in CATEGORIES})
                conn.executemany("INSERT INTO mistake_categories VALUES (?,?,?)",
                                 [(ev, t, s) for t, s in cats] or [(ev, UNCLASSIFIED, "")])
        board.push(mv)
    # a newer derivation replaces older versions of the same run (engine rows are kept)
    conn.execute("DELETE FROM derivations WHERE run_id=? AND derivation_id<>?", (run_id, did))
    conn.commit()
    return did


def rederive_all(conn: sqlite3.Connection) -> dict:
    """Bring every engine run to the current versions. No engine involved."""
    runs = [r["run_id"] for r in conn.execute("SELECT run_id FROM engine_runs")]
    stale = [r for r in runs if not conn.execute(
        "SELECT 1 FROM derivations WHERE run_id=? AND analyzer_version=? AND claims_version=?",
        (r, ANALYZER_VERSION, CLAIMS_VERSION)).fetchone()]
    for r in stale:
        derive(conn, r)
    return {"runs": len(runs), "rederived": len(stale)}
