"""M6: a pawn move concedes the squares it stopped guarding directly, plus any
farther new hole an enemy pawn already supports (an outpost). A square holding
the mover's own pawn is never a hole."""
import os

import chess
import chess.pgn
import pytest

from mcp_server.features import get_quiet_concessions

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def board_before(pgn_file, move_number, color):
    game = chess.pgn.read_game(open(os.path.join(FIX, pgn_file)))
    board = game.board()
    for m in game.mainline_moves():
        if board.fullmove_number == move_number and board.turn == color:
            return board, m
        board.push(m)
    raise AssertionError("move not found")


def conceded(board, move):
    after = board.copy()
    after.push(move)
    return set(get_quiet_concessions(board, after, board.turn).get("new_weak_squares", []))


SCAND_B4 = board_before("scandinavian_blitz.pgn", 13, chess.WHITE)


@pytest.mark.parametrize("board,move,expected", [
    # 13.b4: a3 loses its guard (c3 holds White's own pawn); a4 was never guarded
    # directly and no Black pawn supports it
    (*SCAND_B4, {"a3"}),
    # same position mirrored: Black plays ...b5
    (SCAND_B4[0].mirror(), chess.Move(chess.square_mirror(SCAND_B4[1].from_square),
                                      chess.square_mirror(SCAND_B4[1].to_square)), {"a6"}),
    # 10...b5: a6 directly (c6 holds Black's own pawn), plus c5, which White's d4
    # pawn already supports
    (*board_before("carlsbad_quiet.pgn", 10, chess.BLACK), {"a6", "c5"}),
    # 19.g3: kingside holes f3/h3
    (*board_before("scandinavian_blitz.pgn", 19, chess.WHITE), {"f3", "h3"}),
    # edge file, no b- or c-pawn: a2-a4 leaves b3 (direct); b4 is unsupported by Black, so not reported
    (chess.Board("4k3/pppppppp/8/8/8/8/P2PPPPP/4K3 w - - 0 1"), chess.Move.from_uci("a2a4"), {"b3"}),
    # neighbour still covers: b2-b4 from the start leaves a3; d2 still guards c3
    (chess.Board(), chess.Move.from_uci("b2b4"), {"a3"}),
    # piece moves never concede pawn squares
    (chess.Board(), chess.Move.from_uci("g1f3"), set()),
])
def test_conceded_squares(board, move, expected):
    assert conceded(board, move) == expected


def unsupported(board, move):
    after = board.copy()
    after.push(move)
    return set(get_quiet_concessions(board, after, board.turn).get("new_pawn_unsupported", []))


@pytest.mark.parametrize("board,move,expected", [
    # 13.b4: the c3 pawn can never again be supported by a pawn (b2 was its last supporter)
    (*SCAND_B4, {"c3"}),
    (SCAND_B4[0].mirror(), chess.Move(chess.square_mirror(SCAND_B4[1].from_square),
                                      chess.square_mirror(SCAND_B4[1].to_square)), {"c6"}),
    # 10...b5: c6 loses b7 as a supporter (it is also backward; both facts are kept)
    (*board_before("carlsbad_quiet.pgn", 10, chess.BLACK), {"c6"}),
    # 1.b4: the b-pawn keeps a2 behind it; home-rank pawns were already unsupportable
    (chess.Board(), chess.Move.from_uci("b2b4"), set()),
    (chess.Board(), chess.Move.from_uci("g1f3"), set()),
])
def test_new_pawn_unsupported(board, move, expected):
    assert unsupported(board, move) == expected


def test_unsupported_pawn_reaches_prompt_unless_backward():
    from scripts.format_narration import format_flag_for_llm
    base = {"move_san": "b4", "move_number": 13, "side": "white", "phase": "middlegame",
            "wdl_delta": -0.1, "best_move_san": "", "pv": [], "refutation_pv": [], "feature_deltas": {}}
    p = format_flag_for_llm({**base, "concessions": {"new_pawn_unsupported": ["c3"]}}, 1, 1800)
    assert "Pawns That Lost All Possible Pawn Support: c3" in p
    p = format_flag_for_llm({**base, "concessions": {"new_backward_pawns": ["c3"], "new_pawn_unsupported": ["c3"]}}, 1, 1800)
    assert "Lost All Possible Pawn Support" not in p
