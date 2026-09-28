"""M6: a pawn move concedes the squares it stopped guarding directly, plus any
farther new hole an enemy pawn already supports (an outpost)."""
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
    # 13.b4: a3/c3 lose their guard; a4 was never guarded directly and no Black pawn supports it
    (*SCAND_B4, {"a3", "c3"}),
    # same position mirrored: Black plays ...b5
    (SCAND_B4[0].mirror(), chess.Move(chess.square_mirror(SCAND_B4[1].from_square),
                                      chess.square_mirror(SCAND_B4[1].to_square)), {"a6", "c6"}),
    # 10...b5: a6/c6 directly, plus c5, which White's d4 pawn already supports
    (*board_before("carlsbad_quiet.pgn", 10, chess.BLACK), {"a6", "c6", "c5"}),
    # 19.g3: kingside holes f3/h3
    (*board_before("scandinavian_blitz.pgn", 19, chess.WHITE), {"f3", "h3"}),
    # edge file, no c-pawn: a2-a4 leaves b3 (direct); b4 is unsupported by Black, so not reported
    (chess.Board("4k3/pppppppp/8/8/8/8/P2PPPPP/4K3 w - - 0 1"), chess.Move.from_uci("a2a4"), {"b3"}),
    # neighbour still covers: b2-b4 from the start leaves a3; d2 still guards c3
    (chess.Board(), chess.Move.from_uci("b2b4"), {"a3"}),
    # piece moves never concede pawn squares
    (chess.Board(), chess.Move.from_uci("g1f3"), set()),
])
def test_conceded_squares(board, move, expected):
    assert conceded(board, move) == expected
