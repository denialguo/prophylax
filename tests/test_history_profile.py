"""M16: recurring weaknesses over a synthetic history database (no engine, no LLM)."""
import json

import pytest

from domain.versions import ANALYZER_VERSION, CLAIMS_VERSION
from history.db import connect
from history.profile import eligible_games, profile, render


@pytest.fixture
def db():
    conn = connect(":memory:")
    yield conn
    conn.close()


def add_game(db, n, events=(), reason=None, tc="blitz", nodes=100_000, versions=(ANALYZER_VERSION, CLAIMS_VERSION),
             created=0, rating=1500, analysed=True):
    """Game n is played on day n (higher n = newer). events: (category, phase, loss_pp[, subject]).
    created: extra unflagged player moves that created a backward pawn (the base rate)."""
    pk = f"g{n:03d}"
    db.execute("INSERT INTO games (game_pk, content_id, source_kind, source_file, source_index, played_on, end_time, "
               "player_color, player_rating, tc_class, start_fen, movetext, plies, exclusion_reason, imported_at) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (pk, pk, "player_history", "x", n, f"2025-{1 + n // 28:02d}-{1 + n % 28:02d}", "12:00:00", "white",
                rating, tc, "startpos", "", 60, reason, "now"))
    if not analysed:
        return pk
    run = db.execute("INSERT INTO engine_runs (game_pk, engine, nodes, threads, hash_mb, multipv, created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (pk, "Stockfish 18", nodes, 1, 16, 1, "now")).lastrowid
    did = db.execute("INSERT INTO derivations (run_id, analyzer_version, claims_version, created_at) VALUES (?,?,?,?)",
                     (run, *versions, "now")).lastrowid
    ply = 0
    for ev in events:
        cat, phase, loss = ev[:3]
        subject = ev[3] if len(ev) > 3 else ("c6" if cat != "unclassified" and cat != "king_safety_reduced" else "")
        conc = {"new_backward_pawns": [subject]} if cat == "backward_pawn_created" else \
               {"new_weak_squares": [subject]} if cat == "weak_square_created" else {}
        _move(db, did, ply, conc, flagged=True)
        eid = db.execute("INSERT INTO mistake_events (derivation_id, ply, game_pk, phase, channel, wdl_delta, "
                         "fen_before, played_san, best_move_san) VALUES (?,?,?,?,?,?,?,?,?)",
                         (did, ply, pk, phase, "wdl", -loss / 100, "fen", "x", "y")).lastrowid
        db.execute("INSERT INTO mistake_categories VALUES (?,?,?)", (eid, cat, subject))
        ply += 2
    for _ in range(created):
        _move(db, did, ply, {"new_backward_pawns": ["d6"]}, flagged=False)
        ply += 2
    db.commit()
    return pk


def _move(db, did, ply, conc, flagged):
    db.execute("INSERT INTO move_analyses (derivation_id, ply, move_number, side, san, uci, is_player_move, phase, "
               "fen_before, wdl_delta, best_move_san, flag_channel, concessions) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (did, ply, ply // 2 + 1, "white", "x", "e2e4", 1, "middlegame", "fen", -0.1 if flagged else 0.0, "y",
                "wdl" if flagged else None, json.dumps(conc)))


def cat(p, name):
    return next(c for c in p["structural"] + [p["unclassified"]] if c["category"] == name)


BP = "backward_pawn_created"


# ── eligibility ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kw", [
    {"reason": "abandoned"}, {"reason": "too_short"}, {"reason": "not_player"}, {"reason": "bullet", "tc": "bullet"},
    {"nodes": 1_000_000}, {"versions": ("a0", CLAIMS_VERSION)}, {"analysed": False},
])
def test_excluded_games_never_count(db, kw):
    add_game(db, 1, events=[(BP, "middlegame", 10)], **kw)
    assert eligible_games(db) == []
    assert cat(profile(db), BP)["games"] == 0


def test_bullet_only_when_named(db):
    add_game(db, 1, events=[(BP, "middlegame", 10)], reason="bullet", tc="bullet")
    add_game(db, 2, events=[(BP, "middlegame", 10)], reason="too_short", tc="bullet")  # short stays out
    assert cat(profile(db, tc_classes=("blitz", "bullet")), BP)["games"] == 1


def test_time_class_and_date_filters(db):
    add_game(db, 1, events=[(BP, "middlegame", 10)], tc="rapid")
    add_game(db, 2, events=[(BP, "middlegame", 10)], tc="blitz")
    add_game(db, 30, events=[(BP, "middlegame", 10)], tc="blitz")
    assert cat(profile(db, tc_classes=("rapid",)), BP)["games"] == 1
    assert cat(profile(db, since="2025-02-01"), BP)["games"] == 1  # only game 30
    assert cat(profile(db, until="2025-01-02"), BP)["games"] == 1  # only game 1


# ── counting ─────────────────────────────────────────────────────────────────
def test_one_chaotic_game_counts_once(db):
    add_game(db, 1, events=[(BP, "middlegame", 10)] * 5)
    c = cat(profile(db), BP)
    assert (c["games"], c["moves"], c["recurring"]) == (1, 5, False)


def test_event_naming_two_squares_is_one_move(db):
    pk = add_game(db, 1, events=[(BP, "middlegame", 10, "c6")])
    eid = db.execute("SELECT event_id FROM mistake_events").fetchone()[0]
    db.execute("INSERT INTO mistake_categories VALUES (?,?,?)", (eid, BP, "a6"))
    assert cat(profile(db), BP)["moves"] == 1


@pytest.mark.parametrize("n_games,recurring", [(2, False), (3, True)])
def test_recurring_threshold(db, n_games, recurring):
    for i in range(n_games):
        add_game(db, i, events=[(BP, "middlegame", 10)])
    p = profile(db)
    assert cat(p, BP)["recurring"] is recurring
    assert [c["category"] for c in p["recurring"]] == ([BP] if recurring else [])


@pytest.mark.parametrize("losses,median", [([4, 8, 21], 8), ([4, 8, 10, 21], 9)])
def test_median_loss(db, losses, median):
    for i, loss in enumerate(losses):
        add_game(db, i, events=[(BP, "middlegame", loss)])
    c = cat(profile(db), BP)
    assert c["median_loss_pp"] == median and c["loss_range_pp"] == [min(losses), max(losses)]


def test_quiet_concessions_are_counted_and_zero_loss_prints_as_zero(db):
    add_game(db, 1, events=[(BP, "middlegame", 0), (BP, "middlegame", 12)])
    db.execute("UPDATE mistake_events SET channel='quiet_inaccuracy', wdl_delta=-0.0 WHERE ply=0")
    for i in (2, 3):
        add_game(db, i, events=[(BP, "middlegame", 12)])
    p = profile(db)
    assert cat(p, BP)["quiet_moves"] == 1 and cat(p, BP)["loss_range_pp"] == [0.0, 12.0]
    text = render(p)
    assert "range 0 to 12" in text and "range -0" not in text and "1 of the 4 were quiet concessions" in text


def test_phase_split(db):
    add_game(db, 1, events=[(BP, "middlegame", 5), (BP, "middlegame", 5), (BP, "endgame", 5)])
    assert cat(profile(db), BP)["phases"] == {"opening": 0, "middlegame": 2, "endgame": 1}


# ── windows and trend ────────────────────────────────────────────────────────
def test_window_boundary_and_trend(db):
    for n in range(45):  # 45 eligible games: current = 25..44, previous = 5..24
        add_game(db, n, events=[(BP, "middlegame", 10)] if n in (4, 5, 24, 25, 30, 44) else ())
    p = profile(db)
    assert (p["window"]["games"], p["window"]["previous_games"]) == (20, 20)
    c = cat(p, BP)
    assert c["games"] == 3          # 25, 30, 44; game 24 is the 21st newest: previous window
    assert c["previous_games"] == 2  # 5 and 24; game 4 is outside both windows


def test_fewer_games_than_the_window(db):
    add_game(db, 1, events=[(BP, "middlegame", 10)])
    p = profile(db)
    assert p["window"]["games"] == 1 and cat(p, BP)["previous_games"] is None


# ── base rate and unclassified ───────────────────────────────────────────────
def test_created_to_flagged_rate(db):
    add_game(db, 1, events=[(BP, "middlegame", 10)], created=4)  # 5 backward-pawn moves, 1 flagged
    c = cat(profile(db), BP)
    assert (c["created_moves"], c["moves"], c["flagged_share"]) == (5, 1, 0.2)
    assert cat(profile(db), "king_safety_reduced")["created_moves"] is None  # no base rate stored


def test_rate_with_no_created_moves(db):
    add_game(db, 1)
    c = cat(profile(db), BP)
    assert (c["created_moves"], c["flagged_share"]) == (0, None)


def test_unclassified_is_never_a_recurring_weakness(db):
    for i in range(5):
        add_game(db, i, events=[("unclassified", "middlegame", 30)])
    p = profile(db)
    assert p["unclassified"]["games"] == 5 and p["recurring"] == []
    assert "not a positional pattern" in render(p)


def test_render_shows_the_evidence(db):
    for i in range(3):
        add_game(db, i, events=[(BP, "middlegame", 8)], rating=1500 + i)
    text = render(profile(db))
    assert "Backward pawns created" in text and "3 of your last 3 games" in text
    assert "median win-probability loss: 8 points" in text and "100,000 nodes" in text


def test_cli_profile(tmp_path, capsys):
    import history.__main__ as cli
    path = str(tmp_path / "h.sqlite3")
    conn = connect(path)
    for i in range(3):
        add_game(conn, i, events=[(BP, "middlegame", 8)])
    add_game(conn, 9, events=[(BP, "middlegame", 8)], reason="bullet", tc="bullet")
    conn.close()
    cli.main(["--db", path, "profile"])
    assert "3 of your last 3 games" in capsys.readouterr().out
    cli.main(["--db", path, "profile", "--include-bullet", "--json"])
    assert json.loads(capsys.readouterr().out)["window"]["games"] == 4
