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
    # M14 Q2: the shelter score makes no king-safety claim in an endgame; other phases still do
    ("endgame shelter score", {"phase": "endgame", "feature_deltas": {"king_safety_delta": -2.5}}, ENGINE),
    ("opening shelter score", {"phase": "opening", "feature_deltas": {"king_safety_delta": -0.3}},
     ENGINE | {("king_safety_reduced", None)}),
    ("endgame structure still claimed", {"phase": "endgame", "concessions": {"new_weak_squares": ["c3"]}},
     ENGINE | {("weak_square_created", "c3")}),
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


# --- board evidence (with fen_before): values below were read off the boards by hand ---

import chess  # noqa: E402

from domain.claims import ClaimConsistencyError  # noqa: E402
from tests.test_narration import get_matching_game, load_eval_cases  # noqa: E402

EVAL = {c["name"]: c["input"]["flagged_move"] for c in load_eval_cases()}


def board_before(flag):
    game = get_matching_game(flag)
    board = game.board()
    for m in game.mainline_moves():
        if board.fullmove_number == flag["move_number"] and board.san(m) == flag["move_san"]:
            return board
        board.push(m)
    raise AssertionError("flag not in its fixture game")


def board_claims(flag):
    return build_claims(move_analysis_from_flag(flag, board_before(flag).fen()), game_id="g1")


def board_evidence(claims):
    return {(c.type, c.subject): {e.fact: e.value for e in c.evidence if e.provenance == "board"}
            for c in claims if any(e.provenance == "board" for e in c.evidence)}


BOARD_TABLE = [
    # 13.b4: b2 guarded a3; c3 could be supported only from b2; Black's d4 pawn attacks c3
    ("scandinavian_b4_blunder", {
        ("weak_square_created", "a3"): {"color_complex": "dark", "conceded_because": ("was_guarded_by_moved_pawn",),
                                        "enemy_pawn_attackers": ()},
        ("pawn_support_lost", "c3"): {"possible_supporters_before": ("b2",), "enemy_pawn_attackers": ("d4",)},
    }),
    # 10...b5: b7 guarded a6; c5 was never b7's square but White's d4 pawn attacks it;
    # c6 is backward with c5 in front, hit by d4 and covered by no Black pawn; c6's
    # unsupported fact is folded into the backward claim
    ("carlsbad_b5_quiet", {
        ("weak_square_created", "a6"): {"color_complex": "light", "conceded_because": ("was_guarded_by_moved_pawn",),
                                        "enemy_pawn_attackers": ()},
        ("weak_square_created", "c5"): {"color_complex": "dark", "conceded_because": ("enemy_pawn_attacks",),
                                        "enemy_pawn_attackers": ("d4",)},
        ("backward_pawn_created", "c6"): {"front_square": "c5", "front_enemy_pawn_attackers": ("d4",),
                                          "front_friendly_pawn_attackers": ()},
    }),
    # 21.h4: king on g2; shield f2 (1) + g3 (0.5) + h2 (1) = 2.5 -> 1.5, no file opens
    ("scandinavian_h4_blunder", {
        ("king_safety_reduced", None): {"king_square": "g2", "king_safety_before": 2.5, "king_safety_after": 1.5},
    }),
    # 2.g4: g2 guarded h3 (f3 holds White's own pawn, so it is no hole)
    ("fools_mate_g4_blunder", {
        ("weak_square_created", "h3"): {"color_complex": "light", "conceded_because": ("was_guarded_by_moved_pawn",),
                                        "enemy_pawn_attackers": ()},
    }),
]


@pytest.mark.parametrize("name,expected", BOARD_TABLE, ids=[t[0] for t in BOARD_TABLE])
def test_board_evidence(name, expected):
    claims = board_claims(EVAL[name])
    assert board_evidence(claims) == expected
    # board facts only add evidence; the claims themselves match the payload-only build
    assert kinds(claims) == kinds(build_claims(move_analysis_from_flag(EVAL[name]), game_id="g1"))


def test_board_sentences():
    text = {(c.type, c.subject): claim_sentence(c) for c in board_claims(EVAL["scandinavian_b4_blunder"])}
    assert text[("pawn_support_lost", "c3")] == (
        "After 13.b4, no White pawn can ever support White's c3 pawn. Black's pawn on d4 attacks it.")
    text = {(c.type, c.subject): claim_sentence(c) for c in board_claims(EVAL["carlsbad_b5_quiet"])}
    assert text[("weak_square_created", "c5")] == (
        "10...b5 created a weak square on c5: no Black pawn can ever guard it again. "
        "White's pawn on d4 already attacks it.")
    assert text[("backward_pawn_created", "c6")] == (
        "10...b5 left Black's c6 pawn backward: no Black pawn stands behind it on an adjacent file, "
        "and c5, the square in front of it, is covered by more White pawns than Black pawns.")


def test_board_that_contradicts_the_payload_raises():
    flag = dict(EVAL["scandinavian_b4_blunder"])
    with pytest.raises(ClaimConsistencyError):
        board_claims({**flag, "concessions": {"new_weak_squares": ["a3"]}})  # c3 fact dropped
    with pytest.raises(ClaimConsistencyError):
        board_claims({**flag, "concessions": {**flag["concessions"], "new_backward_pawns": ["c3"]}})
    with pytest.raises(ClaimConsistencyError):
        board_claims({**flag, "feature_deltas": {**flag["feature_deltas"], "king_safety_delta": -1.0}})


def _mirror_sq(name: str) -> str:
    return name[0] + str(9 - int(name[1]))


@pytest.mark.parametrize("name", [t[0] for t in BOARD_TABLE])
def test_mirrored_position_gives_mirrored_claims(name):
    """Colour symmetry: the mirrored position (colours swapped, board flipped) yields the
    same claim types on mirrored squares, with mirrored board evidence."""
    flag = EVAL[name]
    board = board_before(flag)
    move = board.parse_san(flag["move_san"])
    mirrored = board.mirror()
    m_move = chess.Move(chess.square_mirror(move.from_square), chess.square_mirror(move.to_square))
    m_flag = {**flag, "side": "black" if flag["side"] == "white" else "white",
              "move_san": mirrored.san(m_move), "best_move_san": "", "pv": [], "refutation_pv": [],
              "concessions": {k: [_mirror_sq(s) for s in v] for k, v in flag.get("concessions", {}).items()}}
    m_claims = build_claims(move_analysis_from_flag(m_flag, mirrored.fen()), game_id="g1")

    def mirror_value(fact, v):
        if fact == "color_complex":
            return {"light": "dark", "dark": "light"}[v]  # flipping ranks swaps square colours
        if fact == "king_square" or fact == "front_square":
            return _mirror_sq(v)
        if isinstance(v, tuple) and fact != "conceded_because":
            return tuple(sorted(_mirror_sq(s) for s in v))
        return v

    expected = {(t, _mirror_sq(s) if s else None): {f: mirror_value(f, v) for f, v in ev.items()}
                for (t, s), ev in board_evidence(board_claims(flag)).items()}
    assert board_evidence(m_claims) == expected


def test_plural_attackers_read_correctly():
    from scripts.format_narration import _pawns_attack
    assert _pawns_attack("Black", ("b4", "d4"), "already ") == "Black's pawns on b4 and d4 already attack it."
    assert _pawns_attack("White", ("d4",)) == "White's pawn on d4 attacks it."


def test_claim_code_never_reaches_the_engine():
    # Board evidence is python-chess only (D7): no search, no engine session
    import ast
    import inspect
    import domain.claims
    import mcp_server.features
    for module in (domain.claims, mcp_server.features):
        tree = ast.parse(inspect.getsource(module))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
                   {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert imported <= {"chess", "typing", "domain.models", "mcp_server.features"}, (module.__name__, imported)
