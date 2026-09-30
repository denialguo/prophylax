"""Recurring weaknesses (M16): deterministic, transparent statistics over MistakeEvents.

Only MistakeEvents count (engine-flagged moves by the player), never raw structural
events; those appear only as the denominator of the created -> flagged rate. No LLM,
no composite score: every number is a count, a distinct-game count or a median.

Defaults (operator decision C5): the last 20 eligible games; a category is recurring
at 3+ distinct games in that window; the previous 20 eligible games are the trend
comparison; bullet, abandoned and short games are out unless asked for."""
import json
import sqlite3
import statistics
from typing import Iterable, Optional

from config.settings import HISTORY_NODES
from domain.versions import ANALYZER_VERSION, CLAIMS_VERSION
from history.derive import CATEGORIES, UNCLASSIFIED

WINDOW = 20
RECURRING_MIN_GAMES = 3
DEFAULT_TC = ("blitz", "rapid", "classical")
PHASES = ("opening", "middlegame", "endgame")
# The concession each category is built from (claims rule: a backward pawn is not also
# counted as unsupported). king_safety_reduced has no per-move base rate stored.
CREATED_FROM = {"weak_square_created": "new_weak_squares", "backward_pawn_created": "new_backward_pawns",
                "pawn_support_lost": "new_pawn_unsupported"}
LABELS = {"weak_square_created": "Weak squares created", "backward_pawn_created": "Backward pawns created",
          "pawn_support_lost": "Pawns left without pawn support", "king_safety_reduced": "King's pawn cover weakened",
          UNCLASSIFIED: "Flagged moves with no structural pattern (tactical or other)"}


def eligible_games(conn: sqlite3.Connection, tc_classes: Iterable[str] = DEFAULT_TC,
                   since: Optional[str] = None, until: Optional[str] = None) -> list:
    """Profile games, newest first, each with its current 100k derivation. A game is in
    only if its exclusion_reason is NULL (bullet may be re-admitted by naming it in
    tc_classes; nothing else can be), its time class is selected, it falls in the date
    range, and it has been analysed at the history budget with the current versions."""
    tc_classes = tuple(tc_classes)
    rows = conn.execute(
        f"""SELECT g.game_pk, g.played_on, g.end_time, g.player_rating, g.tc_class, d.derivation_id
            FROM games g
            JOIN engine_runs r ON r.game_pk = g.game_pk AND r.nodes = ?
            JOIN derivations d ON d.run_id = r.run_id AND d.analyzer_version = ? AND d.claims_version = ?
            WHERE (g.exclusion_reason IS NULL OR (g.exclusion_reason = 'bullet' AND 'bullet' IN ({','.join('?' * len(tc_classes))})))
              AND g.tc_class IN ({','.join('?' * len(tc_classes))})
              AND (? IS NULL OR g.played_on >= ?) AND (? IS NULL OR g.played_on <= ?)
            ORDER BY g.played_on DESC, g.end_time DESC, g.game_pk""",
        (HISTORY_NODES, ANALYZER_VERSION, CLAIMS_VERSION, *tc_classes, *tc_classes, since, since, until, until)
    ).fetchall()
    return [dict(r) for r in rows]


def _events(conn, derivation_ids: list) -> list:
    if not derivation_ids:
        return []
    q = ",".join("?" * len(derivation_ids))
    return [dict(r) for r in conn.execute(
        f"""SELECT e.event_id, e.game_pk, e.phase, e.wdl_delta, e.derivation_id, c.category, c.subject
            FROM mistake_events e JOIN mistake_categories c USING (event_id)
            WHERE e.derivation_id IN ({q})""", derivation_ids)]


def _created_moves(conn, derivation_ids: list, category: str) -> Optional[int]:
    """Player moves in these games that created the category's structural feature,
    flagged or not (the base rate). None where no per-move base rate is stored."""
    key = CREATED_FROM.get(category)
    if key is None or not derivation_ids:
        return None if key is None else 0
    q = ",".join("?" * len(derivation_ids))
    n = 0
    for r in conn.execute(f"SELECT concessions FROM move_analyses WHERE is_player_move = 1 AND derivation_id IN ({q})",
                          derivation_ids):
        conc = json.loads(r["concessions"])
        squares = set(conc.get(key, []))
        if key == "new_pawn_unsupported":
            squares -= set(conc.get("new_backward_pawns", []))
        n += bool(squares)
    return n


def _category_stats(events: list, category: str) -> dict:
    rows = [e for e in events if e["category"] == category]
    by_event = {}
    for e in rows:
        by_event.setdefault(e["event_id"], e)  # one event may name several squares
    moves = list(by_event.values())
    losses = sorted(round(-e["wdl_delta"] * 100, 1) for e in moves)
    return {
        "games": len({e["game_pk"] for e in moves}),
        "moves": len(moves),
        "phases": {p: sum(e["phase"] == p for e in moves) for p in PHASES},
        "median_loss_pp": statistics.median(losses) if losses else None,
        "loss_range_pp": [losses[0], losses[-1]] if losses else None,
    }


def profile(conn: sqlite3.Connection, window: int = WINDOW, tc_classes: Iterable[str] = DEFAULT_TC,
            since: Optional[str] = None, until: Optional[str] = None) -> dict:
    games = eligible_games(conn, tc_classes, since, until)
    current, previous = games[:window], games[window:2 * window]
    cur_ids = [g["derivation_id"] for g in current]
    prev_ids = [g["derivation_id"] for g in previous]
    cur_events, prev_events = _events(conn, cur_ids), _events(conn, prev_ids)
    ratings = [g["player_rating"] for g in current if g["player_rating"] is not None]
    categories = []
    for cat in CATEGORIES + (UNCLASSIFIED,):
        stats = _category_stats(cur_events, cat)
        created = _created_moves(conn, cur_ids, cat) if cat != UNCLASSIFIED else None
        stats.update({
            "category": cat, "label": LABELS[cat],
            "created_moves": created,
            "flagged_share": (round(stats["moves"] / created, 3) if created else None),
            "previous_games": _category_stats(prev_events, cat)["games"] if previous else None,
            "recurring": cat != UNCLASSIFIED and stats["games"] >= RECURRING_MIN_GAMES,
        })
        categories.append(stats)
    structural = sorted((c for c in categories if c["category"] != UNCLASSIFIED),
                        key=lambda c: (-c["games"], -(c["median_loss_pp"] or 0), c["category"]))
    return {
        "basis": f"engine-flagged moves, Stockfish at {HISTORY_NODES:,} nodes per position "
                 f"(analyzer {ANALYZER_VERSION}, claims {CLAIMS_VERSION})",
        "window": {"size": window, "games": len(current), "previous_games": len(previous),
                   "from": current[-1]["played_on"] if current else None, "to": current[0]["played_on"] if current else None,
                   "rating_range": [min(ratings), max(ratings)] if ratings else None,
                   "time_classes": sorted({g["tc_class"] for g in current}),
                   "flagged_moves": len({e["event_id"] for e in cur_events})},
        "filters": {"tc_classes": list(tc_classes), "since": since, "until": until},
        "recurring": [c for c in structural if c["recurring"]],
        "structural": structural,
        "unclassified": next(c for c in categories if c["category"] == UNCLASSIFIED),
    }


def render(p: dict) -> str:
    """Plain-text answer to "what positional mistakes do I repeatedly make?"."""
    w = p["window"]
    if not w["games"]:
        return "No analysed eligible games match these filters yet."
    lines = [f"Your last {w['games']} eligible games ({w['from']} to {w['to']}, "
             f"{', '.join(w['time_classes'])}, rated {w['rating_range'][0]}-{w['rating_range'][1]})"
             if w["rating_range"] else f"Your last {w['games']} eligible games ({w['from']} to {w['to']})",
             f"Based on {p['basis']}.", ""]
    rec = p["recurring"]
    if not rec:
        lines.append(f"No positional pattern reached {RECURRING_MIN_GAMES} games in this window.")
    for c in rec:
        phases = ", ".join(f"{n} in the {ph}" for ph, n in c["phases"].items() if n)
        lines += [c["label"],
                  f"  {c['games']} of your last {w['games']} games ({c['moves']} flagged moves: {phases})",
                  f"  median win-probability loss: {c['median_loss_pp']:g} points "
                  f"(range {c['loss_range_pp'][0]:g}-{c['loss_range_pp'][1]:g})"]
        if c["created_moves"]:
            lines.append(f"  you did this on {c['created_moves']} moves in those games; "
                         f"{c['moves']} of them were engine-flagged")
        if c["previous_games"] is not None:
            lines.append(f"  previous {w['previous_games']} games: {c['previous_games']} games")
        lines.append("")
    u = p["unclassified"]
    lines.append(f"{u['label']}: {u['moves']} moves in {u['games']} games (not a positional pattern).")
    return "\n".join(lines)
