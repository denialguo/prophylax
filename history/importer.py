"""PGN history import (M15). Headers are parsed strictly into typed columns and never
reach a model; only sanitized movetext is stored. Import is idempotent: a game already
in the database (same game_pk) is skipped. Profile eligibility is decided here, from
the stored facts, and recorded explicitly (C10)."""
import datetime
import hashlib
import io
import re
import sqlite3
from typing import Iterable, Optional

import chess
import chess.pgn

from domain.convert import content_id
from hooks.sanitize_pgn import sanitized_movetext

MIN_PLIES = 20  # C5

_DATE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")
_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})")
_ELO = re.compile(r"\d{1,4}")
_TC = re.compile(r"(\d+)(?:\+(\d+))?")
_DAILY = re.compile(r"\d+/\d+")
_RESULTS = {"1-0", "0-1", "1/2-1/2"}


def time_control(tc: Optional[str]) -> tuple:
    """(base_s, increment_s, class). Class by estimated duration base + 40 x increment:
    under 180s bullet, under 480s blitz, under 1500s rapid, else classical."""
    if tc and _DAILY.fullmatch(tc):
        return None, None, "daily"
    m = _TC.fullmatch(tc or "")
    if not m:
        return None, None, None
    base, inc = int(m.group(1)), int(m.group(2) or 0)
    est = base + 40 * inc
    cls = "bullet" if est < 180 else "blitz" if est < 480 else "rapid" if est < 1500 else "classical"
    return base, inc, cls


def termination(text: Optional[str]) -> Optional[str]:
    """normal | time | abandoned | other, from Chess.com / Lichess Termination text."""
    if not text:
        return None
    low = text.lower()
    if "abandon" in low:
        return "abandoned"
    if "on time" in low or "time forfeit" in low or "timeout" in low:
        return "time"
    if re.search(r"resignation|checkmate|repetition|agreement|stalemate|insufficient|50-move|normal", low):
        return "normal"
    return "other"


def exclusion_reason(g: dict) -> Optional[str]:
    """The first reason a game is kept out of the profile, or None (eligible). Order
    matters: a short bullet game is 'too_short', so --include-bullet can't pull it in."""
    if g["player_color"] is None:
        return "not_player"
    if g.get("has_null_move"):
        return "null_move"  # the engine can't search past one; never repaired
    if g["start_fen"] != chess.STARTING_FEN:
        return "nonstandard_start"
    if g["termination"] == "abandoned":
        return "abandoned"
    if g["plies"] < MIN_PLIES:
        return "too_short"
    if g["tc_class"] is None:
        return "unknown_time_control"
    if g["tc_class"] in ("bullet", "daily"):
        return g["tc_class"]
    return None


def _elo(h, key):
    v = h.get(key, "")
    return int(v) if _ELO.fullmatch(v) else None


def game_row(game: chess.pgn.Game, source_file: str, source_index: int, player_names: Iterable[str],
             source_kind: str = "player_history") -> dict:
    h = game.headers
    names = {n.casefold() for n in player_names}
    white, black = h.get("White", ""), h.get("Black", "")
    color = ("white" if white.casefold() in names else "black" if black.casefold() in names else None)
    if white.casefold() in names and black.casefold() in names:
        color = None  # both sides "are" the player: not a profile game
    pgn = str(game)
    movetext = sanitized_movetext(pgn)
    start = game.board()
    moves = list(game.mainline_moves())
    ucis = " ".join(m.uci() for m in moves)
    d = _DATE.fullmatch(h.get("Date", ""))
    t = _TIME.match(h.get("EndTime", ""))
    base, inc, cls = time_control(h.get("TimeControl"))
    res = h.get("Result")
    if res in _RESULTS and color:
        result = "draw" if res == "1/2-1/2" else "win" if res == ("1-0" if color == "white" else "0-1") else "loss"
    else:
        result = None
    identity = "|".join([h.get(k, "") for k in ("Site", "Date", "EndTime", "White", "Black", "Result", "TimeControl")]
                        + [start.fen(), ucis])
    row = {
        "game_pk": hashlib.sha256(identity.encode()).hexdigest(),
        "content_id": content_id(start.fen(), ucis),
        "source_kind": source_kind, "source_file": source_file, "source_index": source_index,
        "played_on": "-".join(d.groups()) if d else None,
        "end_time": ":".join(t.groups()) if t else None,
        "player_color": color,
        "player_rating": _elo(h, "WhiteElo" if color == "white" else "BlackElo") if color else None,
        "opponent_rating": _elo(h, "BlackElo" if color == "white" else "WhiteElo") if color else None,
        "time_control": h.get("TimeControl") if base is not None or cls == "daily" else None,
        "tc_base_s": base, "tc_increment_s": inc, "tc_class": cls,
        "result": result, "termination": termination(h.get("Termination")),
        "start_fen": start.fen(), "movetext": movetext, "plies": len(moves),
        "imported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    row["exclusion_reason"] = exclusion_reason({**row, "has_null_move": not all(moves)})
    return row


def import_pgn(conn: sqlite3.Connection, path: str, player_names: Iterable[str]) -> dict:
    """Import every game in a PGN file. Returns counts: source games, imported, already
    present, unreadable."""
    player_names = list(player_names)
    if not player_names:
        raise ValueError("no profiled player: set PROPHYLAX_PLAYER_NAMES or pass --player")
    counts = {"source_games": 0, "imported": 0, "already_present": 0, "unreadable": 0}
    with open(path) as fh:
        text = fh.read()
    stream, index = io.StringIO(text), 0
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        counts["source_games"] += 1
        if game.errors or not any(True for _ in game.mainline_moves()):
            counts["unreadable"] += 1
        else:
            row = game_row(game, path, index, player_names)
            cur = conn.execute(f"INSERT OR IGNORE INTO games ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                               tuple(row.values()))
            if cur.rowcount:
                counts["imported"] += 1
            else:  # facts are immutable; the eligibility policy may have changed
                conn.execute("UPDATE games SET exclusion_reason = ? WHERE game_pk = ?",
                             (row["exclusion_reason"], row["game_pk"]))
                counts["already_present"] += 1
        index += 1
    conn.commit()
    return counts
