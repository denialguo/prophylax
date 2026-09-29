"""Grounding validator on NON-fixture games (hard rule 2)."""
import io
import os
import chess.pgn

from evals.validate_narration import validate_narration, narration_violation

# A game that is not in tests/fixtures/
GAME = chess.pgn.read_game(io.StringIO("1. e4 e5 2. Nf3 Nc6 3. d4 exd4 *"))
FLAG = {
    "move_san": "Nf3", "move_number": 2, "side": "white", "phase": "opening",
    "best_move_san": "Nc3", "pv": ["Nc3", "Nf6", "f4"],
    "refutation_pv": ["Nc6", "d4"], "concessions": {},
}


def test_numbered_illegal_move_rejected():
    assert not validate_narration("After 2.Qxh7 White wins.", FLAG, GAME)


def test_unnumbered_hallucinated_move_rejected():
    assert not validate_narration("The engine shows Qxh7 wins outright.", FLAG, GAME)
    assert not validate_narration("The engine shows Qxh7 wins outright.", FLAG)


def test_unnumbered_payload_move_accepted():
    assert validate_narration("The engine prefers Nc3, keeping the f-pawn free.", FLAG, GAME)


def test_numbered_payload_line_accepted():
    assert validate_narration("The engine prefers 2.Nc3 Nf6 3.f4 instead.", FLAG, GAME)
    # the pre-rendered form the skills tell the narrator to quote verbatim
    assert validate_narration("The engine prefers 2.Nc3 2...Nf6 3.f4 instead.", FLAG, GAME)
    assert validate_narration("After 2...Nc6 3.d4 the centre opens.", FLAG, GAME)


def test_payload_move_at_wrong_ply_rejected():
    # Nc3 is in the payload but is not legal as Black's 2nd move
    assert not validate_narration("Black should answer 2...Nc3.", FLAG, GAME)


def test_violation_reason_is_reported():
    reason = narration_violation("The engine shows Qxh7 wins outright.", FLAG, GAME)
    assert reason is not None and "Qxh7" in reason
    assert narration_violation("The engine prefers Nc3.", FLAG, GAME) is None



def test_typographic_ellipsis_reads_as_black_move():
    # "21…Nxf3, 22.Kxf3" is one legal line; the "…" must not break the chain
    game = chess.pgn.read_game(open(os.path.join(os.path.dirname(__file__), "fixtures", "scandinavian_blitz.pgn")))
    from evals.validate_narration import check_narration_moves_legality
    assert check_narration_moves_legality("the refutation 21…Nxf3, 22.Kxf3 h5 follows", game) is None
    assert check_narration_moves_legality("the refutation 21…Qxf3 follows", game) == "21...Qxf3"
