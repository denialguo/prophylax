"""Grounding validator on NON-fixture games (hard rule 2)."""
import io
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
