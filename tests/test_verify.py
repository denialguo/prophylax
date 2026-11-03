"""Semantic chess-fact verifier: the false claims seen in the QUEEN benchmark, as board
positions, with a positive control for every predicate. No engine, no LLM, no QUEEN."""
import ast
import inspect

import chess
import pytest

import domain.verify as verify
from domain.verify import (Fact, Ply, attacks, blocks, defends, has_passed_pawn, is_check, is_checkmate,
                           is_file_clear, is_insufficient_material, is_open_diagonal, is_open_file,
                           is_passed_pawn, is_semi_open_file, is_stalemate, is_terminal, legal_move_count_is,
                           material_balance_is, material_delta, material_inventory, piece_at, piece_count_is,
                           pinned_against, promotion_distance, promotion_distance_is, ray_blockers, ray_clear,
                           square_empty, verify_fact, verify_variation)
from evals.validate_narration import grounding_violation

b = chess.Board
START = chess.STARTING_FEN
BISHOP_C5 = "4k3/8/8/2B3n1/8/8/8/4K3 w - - 0 1"
KNIGHT_F7 = "k4r2/5Np1/8/8/8/8/8/4K3 w - - 0 1"
BLOCKED_DIAGONAL = "4k3/8/8/2b5/8/4P3/5P2/4K3 b - - 0 1"
CLEAR_DIAGONAL = "4k3/8/8/2b5/8/8/5P2/4K3 b - - 0 1"
INSUFFICIENT_AFTER = "4k3/8/3q4/8/4N3/8/8/4K3 w - - 0 1"
SCHOLAR = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"


# ── QUEEN benchmark failures: attack relations ───────────────────────────────
def test_bishop_c5_does_not_attack_g5():
    r = attacks(b(BISHOP_C5), "c5", "g5")
    assert r.status == "FALSE" and r.evidence["source_piece"] == {"square": "c5", "color": "white", "piece": "bishop"}


@pytest.mark.parametrize("target", ["f8", "g7"])
def test_knight_f7_does_not_attack_f8_or_g7(target):
    assert attacks(b(KNIGHT_F7), "f7", target).status == "FALSE"


def test_attacks_positive_controls():
    assert attacks(b(KNIGHT_F7), "f7", "d6").status == "VERIFIED"       # an empty square
    assert attacks(b("k4r2/8/8/6n1/8/8/5N2/4K3 w - - 0 1"), "f2", "g4").status == "VERIFIED"
    assert attacks(b("4k3/8/8/2B5/8/8/8/4K2n w - - 0 1"), "c5", "e7").status == "VERIFIED"
    assert attacks(b(CLEAR_DIAGONAL), "c5", "f2").status == "VERIFIED"  # an enemy pawn
    assert attacks(b("4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1"), "e4", "d5").status == "VERIFIED"


def test_a_pawn_push_is_not_an_attack():
    assert attacks(b("4k3/8/8/4p3/4P3/8/8/4K3 w - - 0 1"), "e4", "e5").status == "FALSE"


def test_attacks_rejects_a_wrong_piece_identity_and_an_empty_source():
    board = b(BISHOP_C5)
    assert attacks(board, "c5", "e7", piece="knight").status == "FALSE"
    assert attacks(board, "c5", "e7", piece="bishop").status == "VERIFIED"
    assert attacks(board, "d4", "e5").status == "FALSE"


def test_a_piece_does_not_attack_its_own_side_it_defends_it():
    board = b("4k3/8/8/8/8/4P3/5P2/4K3 w - - 0 1")
    assert attacks(board, "f2", "e3").status == "FALSE"
    assert defends(board, "f2", "e3").status == "VERIFIED"


def test_defends_negative_controls():
    assert defends(b(BLOCKED_DIAGONAL), "c5", "f2").status == "FALSE"   # enemy piece, and blocked
    assert defends(b("4k3/8/8/8/8/4P3/8/4K2R w - - 0 1"), "h1", "e3").status == "FALSE"  # no line
    behind = defends(b("4k3/8/8/4R3/4P3/8/4R3/4K3 w - - 0 1"), "e2", "e5")  # x-ray through e4 is not a defence
    assert behind.status == "FALSE" and behind.evidence["blockers"][0]["square"] == "e4"


def test_guarding_an_empty_square_counts_as_defending_it():
    board = b("4k3/8/8/8/8/8/5P2/4K3 w - - 0 1")
    assert defends(board, "f2", "e3").status == "VERIFIED"
    assert attacks(board, "f2", "e3").status == "VERIFIED"


def test_attack_by_a_pinned_piece_is_geometry_and_says_so():
    r = attacks(b("4r1k1/8/8/8/8/8/4N3/4K3 w - - 0 1"), "e2", "d4")
    assert r.status == "VERIFIED" and r.evidence["source_pinned"] is True


# ── QUEEN benchmark failures: rays, blockers, pins ───────────────────────────
def test_bishop_c5_to_f2_is_blocked_by_e3():
    board = b(BLOCKED_DIAGONAL)
    r = ray_clear(board, "c5", "f2")
    assert r.status == "FALSE" and r.evidence["blockers"] == [{"square": "e3", "color": "white", "piece": "pawn"}]
    assert [p.square for p in ray_blockers(board, "c5", "f2")] == ["e3"]
    assert attacks(board, "c5", "f2").status == "FALSE"


def test_ray_positive_controls():
    assert ray_clear(b(CLEAR_DIAGONAL), "c5", "f2").status == "VERIFIED"
    assert ray_blockers(b(CLEAR_DIAGONAL), "c5", "f2") == ()
    assert ray_clear(b(START), "e4", "e5").status == "VERIFIED"  # adjacent: nothing between


def test_squares_with_no_common_line_have_no_ray():
    board = b(BISHOP_C5)
    assert ray_clear(board, "c5", "d7").status == "FALSE"
    assert ray_blockers(board, "c5", "d7") is None


def test_a_pawn_on_f3_does_not_block_a_bishop_on_f1():
    board = b("4k3/8/8/8/8/5P2/8/4KB2 w - - 0 1")
    assert blocks(board, "f1", "f3").status == "FALSE"     # bishops do not move along the file
    assert blocks(b("4k3/8/8/8/8/6P1/8/4KB2 w - - 0 1"), "f1", "g2").status == "FALSE"  # g2 is empty


def test_blocks_positive_and_other_negative_controls():
    assert blocks(b("4k3/8/8/8/8/8/6P1/4KB2 w - - 0 1"), "f1", "g2").status == "VERIFIED"
    assert blocks(b("4k3/8/8/8/8/5P2/6P1/4KB2 w - - 0 1"), "f1", "h3").status == "FALSE"  # empty target
    assert blocks(b("4k3/8/8/8/5N2/5P2/8/4KR2 w - - 0 1"), "f1", "f4").status == "FALSE"  # f3 stands in between
    assert blocks(b("4k3/8/8/8/8/5P2/8/4KN2 w - - 0 1"), "f1", "f3").status == "FALSE"    # a knight is not a slider


def test_knight_is_not_pinned_to_the_queen_through_a_bishop():
    r = pinned_against(b("3qk3/3b4/3n4/8/8/8/8/3RK3 w - - 0 1"), "d1", "d6", "d8")
    assert r.status == "FALSE" and r.evidence["blockers"][0]["square"] == "d7"


def test_pin_positive_controls_relative_and_absolute():
    relative = pinned_against(b("3qk3/8/3n4/8/8/8/8/3RK3 w - - 0 1"), "d1", "d6", "d8")
    assert relative.status == "VERIFIED" and relative.evidence["absolute"] is False
    absolute = pinned_against(b("4k3/8/4n3/8/8/8/8/3KR3 w - - 0 1"), "e1", "e6", "e8")
    assert absolute.status == "VERIFIED" and absolute.evidence["absolute"] is True


@pytest.mark.parametrize("fen,pinner,pinned,target", [
    ("3qk3/8/3n4/8/8/8/8/3NK3 w - - 0 1", "d1", "d6", "d8"),      # a knight does not pin
    ("3qk3/8/3n4/8/8/8/8/3BK3 w - - 0 1", "d1", "d6", "d8"),      # a bishop does not move along a file
    ("3qk3/8/3N4/8/8/8/8/3RK3 w - - 0 1", "d1", "d6", "d8"),      # the "pinned" piece is White's own
    ("3Qk3/8/3n4/8/8/8/8/3RK3 b - - 0 1", "d1", "d6", "d8"),      # nothing of Black's to lose behind it
    ("3qk3/8/3n4/8/8/3P4/8/3RK3 w - - 0 1", "d1", "d6", "d8"),    # blocked before the pinned piece
    ("2q1k3/8/3n4/8/8/8/8/3RK3 w - - 0 1", "d1", "d6", "c8"),     # target not on the line
    ("3qk3/8/3n4/8/8/8/8/3RK3 w - - 0 1", "d1", "d6", "d7"),      # empty target
    ("3nk3/8/3q4/8/8/8/8/3RK3 w - - 0 1", "d1", "d6", "d8"),      # a queen is not pinned to a knight
])
def test_pin_negative_controls(fen, pinner, pinned, target):
    assert pinned_against(b(fen), pinner, pinned, target).status == "FALSE"


# ── QUEEN benchmark failures: pawns ──────────────────────────────────────────
def test_c6_pawn_is_two_moves_from_promotion_not_one():
    board = b("4k3/8/2P5/8/8/8/8/4K3 w - - 0 1")
    assert promotion_distance(board, "c6") == 2
    assert promotion_distance_is(board, "c6", 1).status == "FALSE"
    assert promotion_distance_is(board, "c6", 2).status == "VERIFIED"


def test_promotion_distance_follows_pawn_direction_and_the_double_step():
    board = b("4k3/2p5/8/8/8/8/2P5/4K3 w - - 0 1")
    assert promotion_distance(board, "c2") == 5
    assert promotion_distance(board, "c7") == 5
    black = b("4k3/8/8/8/8/2p5/8/4K3 b - - 0 1")
    assert promotion_distance(black, "c3") == 2
    assert promotion_distance(board, "e1") is None     # not a pawn
    assert promotion_distance_is(board, "e1", 1).status == "FALSE"


def test_a_blocked_path_leaves_promotion_unproven():
    r = promotion_distance_is(b("2r1k3/2P5/8/8/8/8/8/4K3 w - - 0 1"), "c7", 1)
    assert r.status == "UNKNOWN" and r.evidence["path_blockers"][0]["square"] == "c8"


def test_b6_is_not_passed_with_an_enemy_pawn_on_a7():
    assert is_passed_pawn(b("4k3/p7/1P6/8/8/8/8/4K3 w - - 0 1"), "b6").status == "FALSE"
    assert is_passed_pawn(b("4k3/p7/1P6/8/8/8/8/4K3 w - - 0 1"), "b6").evidence["enemy_pawns_ahead"] == ["a7"]


@pytest.mark.parametrize("fen,square,expected", [
    ("4k3/8/1P6/8/8/8/8/4K3 w - - 0 1", "b6", "VERIFIED"),       # nothing ahead
    ("4k3/8/pP6/8/8/8/8/4K3 w - - 0 1", "b6", "VERIFIED"),       # beside it, not ahead
    ("4k3/8/1P6/p7/8/8/8/4K3 w - - 0 1", "b6", "VERIFIED"),      # already behind it
    ("4k3/1p6/1P6/8/8/8/8/4K3 w - - 0 1", "b6", "FALSE"),        # same file, ahead
    ("4k3/2p5/1P6/8/8/8/8/4K3 w - - 0 1", "b6", "FALSE"),        # other adjacent file
    ("4k3/8/8/8/1p6/8/8/4K3 b - - 0 1", "b4", "VERIFIED"),       # Black: ahead is downward
    ("4k3/8/8/8/1p6/P7/8/4K3 b - - 0 1", "b4", "FALSE"),
    ("4k3/8/8/8/1n6/8/8/4K3 b - - 0 1", "b4", "FALSE"),          # not a pawn
    ("4k3/8/8/8/8/8/8/4K3 w - - 0 1", "b4", "FALSE"),            # empty
])
def test_passed_pawn_table(fen, square, expected):
    assert is_passed_pawn(b(fen), square).status == expected


def test_has_passed_pawn_by_file():
    board = b("4k3/8/1P6/8/8/8/P7/4K3 w - - 0 1")
    assert has_passed_pawn(board, "white", "a").status == "VERIFIED"
    assert has_passed_pawn(board, "white", "c").status == "FALSE"
    assert has_passed_pawn(board, "black", "a").status == "FALSE"


# ── QUEEN benchmark failures: files and diagonals ────────────────────────────
def test_open_file_rejects_a_file_with_a_pawn_of_either_colour():
    assert is_open_file(b(START), "e").status == "FALSE"
    assert is_open_file(b("4k3/4p3/8/8/8/8/8/4K3 w - - 0 1"), "e").status == "FALSE"
    assert is_open_file(b("4k3/8/8/8/8/8/P7/4K3 w - - 0 1"), "a").status == "FALSE"


def test_open_file_means_no_pawns_and_reports_the_pieces_still_on_it():
    r = is_open_file(b("4k3/8/8/8/8/2n5/8/2R1K3 w - - 0 1"), "c")
    assert r.status == "VERIFIED"
    assert [p["square"] for p in r.evidence["pieces_on_file"]] == ["c1", "c3"]


def test_clear_file_rejects_any_occupant_at_all():
    board = b("4k3/8/8/8/8/2n5/8/2R1K3 w - - 0 1")
    assert is_file_clear(board, "c").status == "FALSE"
    assert is_file_clear(board, "d").status == "VERIFIED"


def test_semi_open_file():
    board = b("4k3/4p3/8/8/8/8/8/4K3 w - - 0 1")
    assert is_semi_open_file(board, "e", "white").status == "VERIFIED"
    assert is_semi_open_file(board, "e", "black").status == "FALSE"  # Black's own pawn is on it
    assert is_semi_open_file(board, "d", "white").status == "FALSE"  # fully open


def test_open_diagonal():
    assert is_open_diagonal(b(CLEAR_DIAGONAL), "c5", "f2").status == "VERIFIED"
    assert is_open_diagonal(b(BLOCKED_DIAGONAL), "c5", "f2").status == "FALSE"
    assert is_open_diagonal(b(CLEAR_DIAGONAL), "c5", "c1").status == "FALSE"  # a file, not a diagonal


# ── piece placement and material ─────────────────────────────────────────────
def test_piece_at():
    board = b(BISHOP_C5)
    assert piece_at(board, "c5", "white", "bishop").status == "VERIFIED"
    assert piece_at(board, "c5").status == "VERIFIED"
    assert piece_at(board, "c5", "black", "bishop").status == "FALSE"
    assert piece_at(board, "c5", "white", "knight").status == "FALSE"
    assert piece_at(board, "d5").status == "FALSE"
    assert square_empty(board, "d5").status == "VERIFIED"
    assert square_empty(board, "c5").status == "FALSE"


def test_material_inventory_and_delta_with_a_promotion():
    before = b("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    after = before.copy()
    after.push_san("a8=Q")
    inv = material_inventory(after)
    assert inv.white == {"pawn": 0, "knight": 0, "bishop": 0, "rook": 0, "queen": 1, "king": 1}
    assert (inv.white_points, inv.black_points, inv.balance) == (9, 0, 9)
    delta = material_delta(before, after)
    assert delta.white == {"pawn": -1, "knight": 0, "bishop": 0, "rook": 0, "queen": 1, "king": 0}
    assert delta.balance == 8


def test_material_predicates():
    board = b(INSUFFICIENT_AFTER)
    assert material_balance_is(board, -6).status == "VERIFIED"
    assert material_balance_is(board, 6).status == "FALSE"
    assert piece_count_is(board, "black", "queen", 1).status == "VERIFIED"
    assert piece_count_is(board, "white", "queen", 1).status == "FALSE"


# ── terminal state ───────────────────────────────────────────────────────────
def test_check_mate_stalemate_predicates():
    mate = b("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    assert is_check(mate).status == "VERIFIED" and is_checkmate(mate).status == "VERIFIED"
    assert is_terminal(mate).status == "VERIFIED" and is_stalemate(mate).status == "FALSE"
    stale = b("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    assert is_stalemate(stale).status == "VERIFIED" and is_check(stale).status == "FALSE"
    assert is_checkmate(stale).status == "FALSE" and is_terminal(stale).status == "VERIFIED"
    assert legal_move_count_is(stale, 0).status == "VERIFIED"
    start = b(START)
    assert is_check(start).status == "FALSE" and is_terminal(start).status == "FALSE"
    assert legal_move_count_is(start, 20).status == "VERIFIED" and legal_move_count_is(start, 19).status == "FALSE"


def test_king_and_knight_against_king_is_insufficient_and_terminal():
    board = b("4k3/8/3N4/8/8/8/8/4K3 b - - 0 1")
    assert is_insufficient_material(board).status == "VERIFIED"
    r = is_terminal(board)
    assert r.status == "VERIFIED" and r.evidence["termination"] == "INSUFFICIENT_MATERIAL"
    assert is_insufficient_material(b(INSUFFICIENT_AFTER)).status == "FALSE"


# ── variations ───────────────────────────────────────────────────────────────
def test_a_true_variation_replays_with_every_claim_verified():
    r = verify_variation(SCHOLAR, [Ply(san="Qxf7#", side="white", move_number=4, piece="queen", captures="pawn",
                                       material_gain=1,
                                       facts_after=(Fact(predicate="is_checkmate"), Fact(predicate="is_terminal"),
                                                    Fact(predicate="piece_at", args={"square": "f7", "color": "white",
                                                                                     "piece": "queen"})))])
    assert r.status == "VERIFIED" and r.first_false_ply is None
    assert r.plies[0].san == "Qxf7#" and r.plies[0].uci == "h5f7"
    assert r.material_delta.balance == 1
    assert b(r.final_fen).is_checkmate()


def test_an_illegal_ply_is_rejected_and_replay_stops_there():
    r = verify_variation(START, [Ply(san="e4"), Ply(san="e5"), Ply(san="Qh6"), Ply(san="Nf6")])
    assert r.status == "FALSE" and r.first_false_ply == 2 and len(r.plies) == 3
    assert r.plies[2].checks[0].predicate == "move_legal" and r.plies[2].checks[0].status == "FALSE"
    assert r.final_fen == r.plies[1].fen_after


def test_capture_identity_comes_from_the_branch_board():
    # after 1.e4 d5 the d5 unit is a pawn; a line that says the pawn takes a knight is false
    r = verify_variation(START, [Ply(san="e4"), Ply(san="d5"), Ply(san="exd5", captures="knight")])
    assert r.status == "FALSE" and r.first_false_ply == 2
    bad = next(c for c in r.plies[2].checks if c.predicate == "capture_identity")
    assert bad.evidence["captured"] == "pawn"
    ok = verify_variation(START, [Ply(san="e4"), Ply(san="d5"), Ply(san="exd5", captures="pawn", material_gain=1)])
    assert ok.status == "VERIFIED"


@pytest.mark.parametrize("san,predicate", [("Nxf3", "capture_identity"), ("Nf3+", "check_claim"), ("Nf3#", "mate_claim")])
def test_a_san_string_cannot_assert_what_the_board_denies(san, predicate):
    # python-chess's parser ignores x, + and #; the verifier treats them as claims
    r = verify_variation(START, [Ply(san=san)])
    assert r.status == "FALSE"
    assert next(c for c in r.plies[0].checks if c.predicate == predicate).status == "FALSE"


def test_a_claimed_quiet_move_that_captures_is_false():
    r = verify_variation("4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1", [Ply(san="exd5", captures="none")])
    assert r.status == "FALSE"


def test_side_to_move_and_move_number_claims():
    assert verify_variation(START, [Ply(san="e4", side="black")]).status == "FALSE"
    wrong_side = verify_variation(START, [Ply(san="e4", side="black"), Ply(san="e5")])
    assert len(wrong_side.plies) == 1  # the line belongs to another position: replay stops
    assert verify_variation(START, [Ply(san="e4", move_number=3)]).status == "FALSE"
    assert verify_variation(START, [Ply(san="e4", side="white", move_number=1)]).status == "VERIFIED"


def test_a_wrong_piece_identity_is_false():
    r = verify_variation(START, [Ply(san="Nf3", piece="bishop")])
    assert r.status == "FALSE"
    assert verify_variation(START, [Ply(san="Nf3", piece="knight")]).status == "VERIFIED"


def test_a_false_claim_on_a_legal_move_keeps_replaying_and_reports_every_failure():
    r = verify_variation(START, [Ply(san="e4", captures="pawn"), Ply(san="e5"), Ply(san="Nf3", piece="bishop")])
    assert r.status == "FALSE" and r.first_false_ply == 0 and len(r.plies) == 3
    assert [p.status for p in r.plies] == ["FALSE", "VERIFIED", "FALSE"]


def test_material_gain_is_checked():
    assert verify_variation(SCHOLAR, [Ply(san="Qxf7#", material_gain=9)]).status == "FALSE"


def test_en_passant_capture_identity_and_occupancy():
    r = verify_variation("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2",
                         [Ply(san="exd6", captures="pawn", material_gain=1,
                              facts_after=(Fact(predicate="square_empty", args={"square": "d5"}),
                                           Fact(predicate="piece_at", args={"square": "d6", "piece": "pawn"})))])
    assert r.status == "VERIFIED"


def test_promotion_replaces_the_piece_in_the_branch():
    r = verify_variation("8/P6k/8/8/8/8/8/K7 w - - 0 1",
                         [Ply(san="a8=Q", material_gain=8,
                              facts_after=(Fact(predicate="piece_count_is",
                                                args={"color": "white", "piece": "pawn", "count": 0}),
                                           Fact(predicate="piece_at", args={"square": "a8", "piece": "queen"})))])
    assert r.status == "VERIFIED"


def test_an_invented_passed_a_pawn_is_false_in_the_branch():
    r = verify_variation("4k3/8/8/8/3p4/4P3/8/4K3 w - - 0 1",
                         [Ply(san="exd4", facts_after=(Fact(predicate="has_passed_pawn",
                                                            args={"color": "white", "file": "a"}),))])
    assert r.status == "FALSE"
    assert r.plies[0].checks[-1].fen == r.plies[0].fen_after  # checked on the branch's board


def test_nxd6_wins_the_queen_ends_the_game_and_nothing_can_follow():
    won = verify_variation(INSUFFICIENT_AFTER, [Ply(san="Nxd6+", captures="queen", material_gain=9,
                                                    facts_after=(Fact(predicate="is_insufficient_material"),
                                                                 Fact(predicate="is_terminal")))])
    assert won.status == "VERIFIED"
    longer = verify_variation(INSUFFICIENT_AFTER, [Ply(san="Nxd6+"), Ply(san="Kd7")])
    assert longer.status == "FALSE" and longer.first_false_ply == 1
    assert "game is over" in longer.plies[1].checks[0].reason


def test_moves_after_checkmate_are_rejected():
    r = verify_variation(SCHOLAR, [Ply(san="Qxf7#"), Ply(san="Ke7")])
    assert r.status == "FALSE" and r.first_false_ply == 1


def test_ambiguous_or_unparsable_moves_are_unknown_and_stop_the_replay():
    ambiguous = verify_variation("4k3/8/8/8/8/8/8/N1N1K3 w - - 0 1", [Ply(san="Nb3"), Ply(san="Kd7")])
    assert ambiguous.status == "UNKNOWN" and len(ambiguous.plies) == 1 and ambiguous.first_false_ply is None
    assert verify_variation(START, [Ply(san="knight takes")]).status == "UNKNOWN"


def test_a_false_ply_outranks_a_later_unknown():
    r = verify_variation(START, [Ply(san="e4", captures="pawn"), Ply(san="???")])
    assert r.status == "FALSE"


def test_a_fact_about_the_branch_that_cannot_be_evaluated_is_unknown_not_verified():
    r = verify_variation(START, [Ply(san="e4", facts_after=(Fact(predicate="improves_coordination"),))])
    assert r.status == "UNKNOWN" and r.plies[0].status == "UNKNOWN"


def test_nothing_is_proven_by_an_empty_variation_or_a_bad_start():
    assert verify_variation(START, []).status == "UNKNOWN"
    assert verify_variation("not a fen", [Ply(san="e4")]).status == "UNKNOWN"
    assert verify_variation("8/8/8/8/8/8/8/8 w - - 0 1", [Ply(san="e4")]).status == "UNKNOWN"


# ── UNKNOWN: never a proof by absence ────────────────────────────────────────
@pytest.mark.parametrize("predicate", [
    "traps_piece", "only_saving_move", "prophylaxis", "improves_coordination", "piece_reroute",
    "dominates", "has_initiative", "seizes_control",
])
def test_strategic_claims_are_unknown(predicate):
    r = verify_fact(START, Fact(predicate=predicate, args={"square": "g5"}))
    assert r.status == "UNKNOWN" and "no verifier" in r.reason
    assert predicate not in verify.PREDICATES


@pytest.mark.parametrize("fact", [
    Fact(predicate="attacks", args={"source": "z9", "target": "e4"}),
    Fact(predicate="attacks", args={"source": "e2"}),
    Fact(predicate="attacks", args={"source": "e2", "target": "e4", "extra": 1}),
    Fact(predicate="attacks", args={"source": 12, "target": "e4"}),
    Fact(predicate="piece_at", args={"square": "e2", "piece": "dragon"}),
    Fact(predicate="piece_at", args={"square": "e2", "color": "green"}),
    Fact(predicate="is_open_file", args={"file": "k"}),
    Fact(predicate="promotion_distance_is", args={"square": "e2", "moves": "two"}),
])
def test_a_malformed_fact_is_unknown(fact):
    assert verify_fact(START, fact).status == "UNKNOWN"


def test_a_fact_on_a_bad_board_is_unknown():
    fact = Fact(predicate="is_check")
    assert verify_fact("garbage", fact).status == "UNKNOWN"
    assert verify_fact("8/8/8/8/8/8/8/8 w - - 0 1", fact).status == "UNKNOWN"


def test_the_dispatcher_reaches_the_predicates():
    r = verify_fact(BISHOP_C5, Fact(predicate="attacks", args={"source": "c5", "target": "g5", "piece": "bishop"}))
    assert r.status == "FALSE" and r.predicate == "attacks" and r.fen == b(BISHOP_C5).fen()
    assert r.args == {"source": "c5", "target": "g5", "piece": "bishop"}
    assert verify_fact(BISHOP_C5, Fact(predicate="attacks", args={"source": "c5", "target": "e7"})).status == "VERIFIED"


def test_every_predicate_answers_in_both_directions():
    assert set(verify.PREDICATES) == {
        "piece_at", "square_empty", "piece_count_is", "attacks", "defends", "ray_clear", "blocks", "pinned_against",
        "is_open_file", "is_semi_open_file", "is_file_clear", "is_open_diagonal", "is_passed_pawn", "has_passed_pawn",
        "promotion_distance_is", "material_balance_is", "legal_move_count_is", "is_check", "is_checkmate",
        "is_stalemate", "is_insufficient_material", "is_terminal"}


# ── separation from narration ────────────────────────────────────────────────
def test_grounded_squares_do_not_make_a_relation_true():
    """The gap this module exists for: the narration validator checks tokens, so both
    squares being in the payload lets a false relation through."""
    text = "The bishop on c5 attacks g5."
    assert grounding_violation(text, {"position_facts": {"squares": ["c5", "g5"]}}) is None
    assert attacks(b(BISHOP_C5), "c5", "g5").status == "FALSE"


def test_results_are_deterministic_and_auditable():
    plies = [Ply(san="e4"), Ply(san="d5"), Ply(san="exd5", captures="pawn")]
    first, second = verify_variation(START, plies), verify_variation(START, plies)
    assert first.model_dump_json() == second.model_dump_json()
    r = first.plies[2]
    assert r.ply == 2 and r.fen_before != r.fen_after and all(c.fen for c in r.checks)


def test_the_verifier_stands_alone():
    tree = ast.parse(inspect.getsource(verify))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
               {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"chess", "functools", "inspect", "typing", "pydantic", "domain.models"}, imported
