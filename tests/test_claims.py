"""M12: deterministic coaching claims. Table-driven; no engine, no LLM."""
import re

import pytest

from domain.claims import build_claims
from domain.convert import move_analysis_from_flag
from scripts.format_narration import claim_sentence

BASE = {"move_san": "b4", "move_number": 13, "side": "white", "phase": "middlegame",
        "wdl_delta": -0.3, "best_move_san": "cxd4", "pv": ["cxd4", "exd4"],
        "refutation_pv": ["a5", "bxa5"], "feature_deltas": {}, "concessions": {}, "channel": "wdl"}
ENGINE = {("wdl_loss", None), ("engine_best_move", None), ("engine_refutation", None)}


def claims_for(**overrides):
    return build_claims(move_analysis_from_flag({**BASE, **overrides}), game_id="g1")


def kinds(claims):
    return {(c.type, c.subject) for c in claims}


# (name, overrides, expected (type, subject) set)
TABLE = [
    ("engine claims", {}, ENGINE),
    ("no loss when the move gains", {"wdl_delta": 0.05}, ENGINE - {("wdl_loss", None)}),
    ("no loss at zero", {"wdl_delta": 0.0}, ENGINE - {("wdl_loss", None)}),
    ("played the engine move", {"best_move_san": "b4"}, ENGINE - {("engine_best_move", None)}),
    ("no engine move", {"best_move_san": ""}, ENGINE - {("engine_best_move", None)}),
    ("no refutation", {"refutation_pv": []}, ENGINE - {("engine_refutation", None)}),
    ("weak squares", {"concessions": {"new_weak_squares": ["c5", "a6"]}},
     ENGINE | {("weak_square_created", "a6"), ("weak_square_created", "c5")}),
    ("backward pawn", {"concessions": {"new_backward_pawns": ["c6"]}},
     ENGINE | {("backward_pawn_created", "c6")}),
    ("unsupported pawn", {"concessions": {"new_pawn_unsupported": ["c3"]}},
     ENGINE | {("pawn_support_lost", "c3")}),
    # fact 5: a backward pawn already implies no support; only the backward claim
    ("backward and unsupported", {"concessions": {"new_backward_pawns": ["c6"], "new_pawn_unsupported": ["c6", "a4"]}},
     ENGINE | {("backward_pawn_created", "c6"), ("pawn_support_lost", "a4")}),
    ("king safety reduced", {"feature_deltas": {"king_safety_delta": -1.0}}, ENGINE | {("king_safety_reduced", None)}),
    ("negligible king safety change", {"feature_deltas": {"king_safety_delta": -0.005}}, ENGINE),
    ("king safety improved", {"feature_deltas": {"king_safety_delta": 0.5}}, ENGINE),
    # D8: scores are not statements
    ("scores make no claims", {"feature_deltas": {"pawn_structure_delta": -0.8, "piece_activity_delta": -3,
                                                  "weak_squares": -2}}, ENGINE),
    ("quiet concession", {"channel": "quiet_inaccuracy", "wdl_delta": -0.04,
                          "concessions": {"new_weak_squares": ["c5"], "new_backward_pawns": ["c6"]}},
     ENGINE | {("weak_square_created", "c5"), ("backward_pawn_created", "c6"), ("quiet_structural_concession", None)}),
]


@pytest.mark.parametrize("name,overrides,expected", TABLE, ids=[t[0] for t in TABLE])
@pytest.mark.parametrize("side", ["white", "black"])
def test_claim_table(name, overrides, expected, side):
    # Symmetric: the same payload for either side gives the same claims
    assert kinds(claims_for(side=side, **overrides)) == expected


def test_claims_are_ordered_and_identified():
    claims = claims_for(channel="quiet_inaccuracy", feature_deltas={"king_safety_delta": -1.0},
                        concessions={"new_weak_squares": ["c5", "a6"], "new_backward_pawns": ["c6"],
                                     "new_pawn_unsupported": ["a4"]})
    assert [(c.type, c.subject) for c in claims] == [
        ("wdl_loss", None), ("engine_best_move", None), ("engine_refutation", None),
        ("weak_square_created", "a6"), ("weak_square_created", "c5"), ("backward_pawn_created", "c6"),
        ("pawn_support_lost", "a4"), ("king_safety_reduced", None), ("quiet_structural_concession", None)]
    assert [c.claim_id for c in claims] == [f"g1-ply24-c{i}" for i in range(1, 10)]
    quiet = claims[-1]
    assert set(quiet.supports) == {c.claim_id for c in claims if c.type in ("weak_square_created", "backward_pawn_created")}


def test_payload_only_claims_carry_no_board_evidence():
    claims = claims_for(concessions={"new_weak_squares": ["a3"], "new_pawn_unsupported": ["c3"]})
    assert {e.provenance for c in claims for e in c.evidence} == {"engine", "server_features"}
    by_type = {c.type: c for c in claims}
    assert [(e.fact, e.value) for e in by_type["wdl_loss"].evidence] == [
        ("win_prob_delta", -0.3), ("channel", "wdl"), ("phase", "middlegame")]
    assert [(e.fact, e.value) for e in by_type["engine_best_move"].evidence] == [
        ("best_move", "cxd4"), ("pv", ("cxd4", "exd4"))]
    assert [(e.fact, e.value) for e in by_type["pawn_support_lost"].evidence] == [("new_pawn_unsupported", "c3")]


# --- templates: one deterministic sentence per claim type ---

SENTENCES = [
    ("wdl_loss", {}, "13.b4 lowered White's winning chances in the engine's evaluation."),
    ("wdl_loss", {"wdl_before_prob": 0.74, "wdl_after_prob": 0.511},
     "13.b4 dropped White's win probability from 74% to 51% in the engine's evaluation."),
    ("engine_best_move", {}, "The engine preferred 13.cxd4, with the line 13.cxd4 13...exd4."),
    ("engine_refutation", {}, "The engine's refutation of 13.b4 is 13...a5 14.bxa5."),
    ("weak_square_created", {"concessions": {"new_weak_squares": ["a3"]}},
     "13.b4 created a weak square on a3: no White pawn can ever guard it again."),
    ("backward_pawn_created", {"concessions": {"new_backward_pawns": ["c3"]}},
     "13.b4 left White's c3 pawn backward: no White pawn stands behind it on an adjacent file, "
     "and the square in front of it is covered by more Black pawns than White pawns."),
    ("pawn_support_lost", {"concessions": {"new_pawn_unsupported": ["c3"]}},
     "After 13.b4, no White pawn can ever support White's c3 pawn."),
    ("king_safety_reduced", {"feature_deltas": {"king_safety_delta": -1.0}},
     "13.b4 thinned the pawn cover around White's king."),
    ("quiet_structural_concession", {"channel": "quiet_inaccuracy",
                                     "concessions": {"new_weak_squares": ["c5"], "new_backward_pawns": ["c6"]}},
     "The engine barely registers 13.b4, but it is a lasting structural concession: "
     "it creates both a weak square and a backward pawn."),
]


@pytest.mark.parametrize("ctype,overrides,sentence", SENTENCES)
def test_claim_sentence(ctype, overrides, sentence):
    claim = next(c for c in claims_for(**overrides) if c.type == ctype)
    assert claim_sentence(claim) == sentence


def test_black_move_sentences_number_correctly():
    claims = claims_for(side="black", move_san="b5", move_number=10, best_move_san="a6",
                        pv=["a6", "a4"], refutation_pv=["a4", "b4"])
    text = {c.type: claim_sentence(c) for c in claims}
    assert text["engine_best_move"] == "The engine preferred 10...a6, with the line 10...a6 11.a4."
    assert text["engine_refutation"] == "The engine's refutation of 10...b5 is 11.a4 11...b4."


def test_sentences_cite_only_claim_squares():
    claims = claims_for(channel="quiet_inaccuracy", feature_deltas={"king_safety_delta": -1.0},
                        concessions={"new_weak_squares": ["c5"], "new_backward_pawns": ["c6"],
                                     "new_pawn_unsupported": ["a4"]})
    for c in claims:
        allowed = set(re.findall(r"[a-h][1-8]", " ".join(
            [c.move_san, c.subject or ""] + [str(e.value) for e in c.evidence])))
        assert set(re.findall(r"\b[a-h][1-8]\b", claim_sentence(c))) <= allowed, c.type


# --- new_pawn_unsupported stays neutral: a claim type doesn't make it a flag trigger ---

from tests.test_failure_modes import call, fake_engine  # noqa: E402,F401  (fixture)


def test_unsupported_pawn_alone_never_flags(fake_engine):
    """12.c3 in the Scandinavian fixture concedes only new_pawn_unsupported (d3). With an
    engine that scores every position the same (no WDL drop), nothing may flag it."""
    import json
    import chess
    import chess.pgn
    from mcp_server.features import get_quiet_concessions
    fake_engine.setenv("FAKE_ENGINE_MODE", "answer")
    game = chess.pgn.read_game(open("tests/fixtures/scandinavian_blitz.pgn"))
    board = game.board()
    moves = list(game.mainline_moves())[:23]  # through 12.c3
    for m in moves[:-1]:
        board.push(m)
    after = board.copy(); after.push(moves[-1])
    assert get_quiet_concessions(board, after, chess.WHITE) == {"new_pawn_unsupported": ["d3"]}

    pgn = chess.pgn.Game.from_board(after).accept(chess.pgn.StringExporter(headers=False))
    res = call("analyze_pgn", {"pgn": pgn, "max_flags": 50})
    out = json.loads(res["result"]["content"][0]["text"])
    assert (12, "white", "c3") not in {(f["move_number"], f["side"], f["move_san"]) for f in out["flags"]}
