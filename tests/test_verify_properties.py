"""Property tests of domain/verify.py against python-chess and independent reimplementations,
on seeded random games. They pin existing behaviour of the frozen verifier and change nothing.
No engine, no LLM, no network."""
import json
import random

import chess
import pytest

from domain.verify import (PREDICATES, Fact, Ply, attacks, blocks, defends, has_passed_pawn, is_check, is_checkmate,
                           is_file_clear, is_insufficient_material, is_open_diagonal, is_open_file, is_passed_pawn,
                           is_semi_open_file, is_stalemate, is_terminal, legal_move_count_is, material_balance_is,
                           material_delta, material_inventory, piece_count_is, pinned_against, promotion_distance,
                           ray_blockers, ray_clear, verify_fact, verify_variation)

SQUARES = chess.SQUARE_NAMES
SEED = 20261103


def random_positions(games=40, seed=SEED):
    """Positions from seeded random legal games, at every stage of a game."""
    rng = random.Random(seed)
    out = []
    for _ in range(games):
        board = chess.Board()
        for ply in range(rng.randint(10, 160)):
            if board.is_game_over():
                break
            board.push(rng.choice(list(board.legal_moves)))
            if ply % 9 == 0:
                out.append(chess.Board(board.fen()))
        out.append(chess.Board(board.fen()))
    return out


POSITIONS = random_positions()


def random_lines(n=30, length=40, seed=SEED + 1):
    rng = random.Random(seed)
    lines = []
    for _ in range(n):
        board, moves = chess.Board(), []
        for _ in range(rng.randint(1, length)):
            if board.is_game_over():
                break
            move = rng.choice(list(board.legal_moves))
            moves.append(move)
            board.push(move)
        lines.append(moves)
    return lines


def square_sample(rng, k=10):
    return rng.sample(range(64), k)


def mirror_name(square):
    return chess.square_name(chess.square_mirror(square))


# ── relations against python-chess ───────────────────────────────────────────
def test_attacks_and_defends_agree_with_python_chess():
    rng = random.Random(SEED)
    for board in POSITIONS:
        for s, piece in board.piece_map().items():
            for t in square_sample(rng, 8):
                other = board.piece_at(t)
                reach = t in board.attacks(s)
                a, d = SQUARES[s], SQUARES[t]
                assert (attacks(board, a, d).status == "VERIFIED") == (reach and (other is None or other.color != piece.color))
                assert (defends(board, a, d).status == "VERIFIED") == (reach and (other is None or other.color == piece.color))


def test_a_relation_from_an_empty_square_is_false():
    rng = random.Random(SEED)
    for board in POSITIONS[:60]:
        empty = [s for s in range(64) if board.piece_at(s) is None]
        for s in rng.sample(empty, min(5, len(empty))):
            t = rng.choice(chess.SQUARES)
            assert attacks(board, SQUARES[s], SQUARES[t]).status == "FALSE"
            assert defends(board, SQUARES[s], SQUARES[t]).status == "FALSE"


def test_attack_and_defence_exclude_each_other_on_an_occupied_target():
    for board in POSITIONS[:60]:
        for s in board.piece_map():
            for t in board.attacks(s):
                if board.piece_at(t) is not None:
                    results = {attacks(board, SQUARES[s], SQUARES[t]).status, defends(board, SQUARES[s], SQUARES[t]).status}
                    assert results == {"VERIFIED", "FALSE"}


def test_rays_match_the_occupancy_between_two_squares():
    rng = random.Random(SEED)
    for board in POSITIONS[:80]:
        for _ in range(40):
            a, b = rng.sample(range(64), 2)
            aligned = bool(chess.BB_RAYS[a][b])
            blockers = ray_blockers(board, SQUARES[a], SQUARES[b])
            assert (blockers is not None) == aligned
            if aligned:
                expected = {SQUARES[s] for s in chess.SquareSet(chess.between(a, b)) if board.piece_at(s)}
                assert {p.square for p in blockers} == expected
                assert (ray_clear(board, SQUARES[a], SQUARES[b]).status == "VERIFIED") == (not expected)
            else:
                assert ray_clear(board, SQUARES[a], SQUARES[b]).status == "FALSE"


def test_a_slider_reaches_along_its_lines_exactly_when_the_ray_is_clear():
    for board in POSITIONS[:60]:
        for s, piece in board.piece_map().items():
            if piece.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
                continue
            for t in range(64):
                if t == s or not chess.BB_RAYS[s][t]:
                    continue
                diagonal = abs(chess.square_file(s) - chess.square_file(t)) == abs(chess.square_rank(s) - chess.square_rank(t))
                travels = piece.piece_type == chess.QUEEN or piece.piece_type == (chess.BISHOP if diagonal else chess.ROOK)
                if travels:
                    assert (t in board.attacks(s)) == (not chess.SquareSet(chess.between(s, t) & board.occupied))


def test_blocks_means_first_piece_on_a_line_the_slider_uses():
    for board in POSITIONS[:60]:
        for s, piece in board.piece_map().items():
            for t in board.piece_map():
                if s == t:
                    continue
                result = blocks(board, SQUARES[s], SQUARES[t]).status
                if piece.piece_type in (chess.BISHOP, chess.ROOK, chess.QUEEN):
                    assert (result == "VERIFIED") == (t in board.attacks(s))  # the first occupied square along a line it travels
                else:
                    assert result == "FALSE"


def test_absolute_pins_agree_with_python_chess_both_ways():
    found = 0
    for board in POSITIONS:
        for color in chess.COLORS:
            king = board.king(color)
            for s, piece in board.piece_map().items():
                if piece.color != color or piece.piece_type == chess.KING:
                    continue
                pinned = board.is_pinned(color, s)
                sliders = [p for p in chess.SquareSet(board.occupied_co[not color]) if board.piece_type_at(p) in (chess.BISHOP, chess.ROOK, chess.QUEEN)]
                verified = [p for p in sliders if pinned_against(board, SQUARES[p], SQUARES[s], SQUARES[king]).status == "VERIFIED"]
                assert bool(verified) == pinned
                found += pinned
    assert found > 20  # the sample really contains pins


# ── pawns ────────────────────────────────────────────────────────────────────
def brute_passed(board, square, color):
    f, r = chess.square_file(square), chess.square_rank(square)
    direction = 1 if color else -1
    rank = r + direction
    while 0 <= rank <= 7:
        for file in (f - 1, f, f + 1):
            if 0 <= file <= 7:
                piece = board.piece_at(chess.square(file, rank))
                if piece and piece.piece_type == chess.PAWN and piece.color != color:
                    return False
        rank += direction
    return True


def test_passed_pawns_agree_with_a_walk_up_the_board():
    for board in POSITIONS:
        for color in chess.COLORS:
            for s in board.pieces(chess.PAWN, color):
                assert (is_passed_pawn(board, SQUARES[s]).status == "VERIFIED") == brute_passed(board, s, color)
            for f, name in enumerate(chess.FILE_NAMES):
                expected = any(brute_passed(board, s, color) for s in board.pieces(chess.PAWN, color) if chess.square_file(s) == f)
                assert (has_passed_pawn(board, chess.COLOR_NAMES[color], name).status == "VERIFIED") == expected


@pytest.mark.parametrize("color", chess.COLORS)
@pytest.mark.parametrize("file", range(8))
def test_promotion_distance_equals_the_shortest_real_path(color, file):
    """On an otherwise empty board, played out with python-chess's own moves."""
    for rank in range(1, 7):
        square = chess.square(file, rank)
        board = chess.Board(None)
        board.set_piece_at(square, chess.Piece(chess.PAWN, color))
        board.set_piece_at(chess.H1 if file < 4 else chess.A1, chess.Piece(chess.KING, chess.WHITE))
        board.set_piece_at(chess.H8 if file < 4 else chess.A8, chess.Piece(chess.KING, chess.BLACK))
        board.turn = color
        distance, frontier = 0, [square]
        found = False
        while not found:
            distance += 1
            following = []
            for at in frontier:
                b = board.copy()
                b.remove_piece_at(square)
                b.set_piece_at(at, chess.Piece(chess.PAWN, color))
                for move in b.generate_legal_moves(from_mask=chess.BB_SQUARES[at]):
                    if move.promotion:
                        found = True
                    following.append(move.to_square)
            frontier = following
        assert promotion_distance(board, SQUARES[square]) == distance


def test_files_agree_with_counting_pawns():
    for board in POSITIONS:
        for f, name in enumerate(chess.FILE_NAMES):
            pawns = {c: [s for s in board.pieces(chess.PAWN, c) if chess.square_file(s) == f] for c in chess.COLORS}
            occupied = chess.SquareSet(chess.BB_FILES[f] & board.occupied)
            assert (is_open_file(board, name).status == "VERIFIED") == (not pawns[True] and not pawns[False])
            assert (is_file_clear(board, name).status == "VERIFIED") == (not occupied)
            for c in chess.COLORS:
                expected = not pawns[c] and bool(pawns[not c])
                assert (is_semi_open_file(board, name, chess.COLOR_NAMES[c]).status == "VERIFIED") == expected


def test_a_clear_diagonal_is_a_clear_ray_on_a_diagonal():
    rng = random.Random(SEED)
    for board in POSITIONS[:80]:
        for _ in range(30):
            a, b = rng.sample(range(64), 2)
            diagonal = abs(chess.square_file(a) - chess.square_file(b)) == abs(chess.square_rank(a) - chess.square_rank(b))
            expected = diagonal and ray_clear(board, SQUARES[a], SQUARES[b]).status == "VERIFIED"
            assert (is_open_diagonal(board, SQUARES[a], SQUARES[b]).status == "VERIFIED") == expected


# ── symmetry ─────────────────────────────────────────────────────────────────
def test_colour_mirror_invariance():
    rng = random.Random(SEED)
    for board in POSITIONS[:120]:
        mirror = board.mirror()
        for _ in range(25):
            a, b = rng.sample(range(64), 2)
            m, n = mirror_name(a), mirror_name(b)
            for fn in (attacks, defends, ray_clear, blocks, is_open_diagonal):
                assert fn(board, SQUARES[a], SQUARES[b]).status == fn(mirror, m, n).status, (fn.__name__, board.fen(), a, b)
        for s in chess.SQUARES:
            assert is_passed_pawn(board, SQUARES[s]).status == is_passed_pawn(mirror, mirror_name(s)).status
            assert promotion_distance(board, SQUARES[s]) == promotion_distance(mirror, mirror_name(s))
        for name in chess.FILE_NAMES:
            assert is_open_file(board, name).status == is_open_file(mirror, name).status
            assert is_semi_open_file(board, name, "white").status == is_semi_open_file(mirror, name, "black").status
        assert material_inventory(board).balance == -material_inventory(mirror).balance
        assert material_inventory(board).white == material_inventory(mirror).black
        for fn in (is_check, is_checkmate, is_stalemate, is_insufficient_material, is_terminal):
            assert fn(board).status == fn(mirror).status
        assert board.legal_moves.count() == mirror.legal_moves.count()


def test_left_right_mirror_invariance_of_geometry():
    rng = random.Random(SEED)
    for board in POSITIONS[:120]:
        plain = board.copy()
        plain.castling_rights, plain.ep_square = 0, None  # neither survives a left-right flip
        flipped = plain.transform(chess.flip_horizontal)
        flip = lambda s: SQUARES[s ^ 7]
        for _ in range(25):
            a, b = rng.sample(range(64), 2)
            for fn in (attacks, defends, ray_clear, blocks, is_open_diagonal):
                assert fn(plain, SQUARES[a], SQUARES[b]).status == fn(flipped, flip(a), flip(b)).status, (fn.__name__, plain.fen(), a, b)
        for s in chess.SQUARES:
            assert is_passed_pawn(plain, SQUARES[s]).status == is_passed_pawn(flipped, flip(s)).status
        for f, name in enumerate(chess.FILE_NAMES):
            assert is_open_file(plain, name).status == is_open_file(flipped, chess.FILE_NAMES[7 - f]).status


# ── material ─────────────────────────────────────────────────────────────────
def test_inventory_counts_every_piece_on_the_board():
    values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
    for board in POSITIONS:
        inv = material_inventory(board)
        assert sum(inv.white.values()) + sum(inv.black.values()) == len(board.piece_map())
        white = sum(values[p.piece_type] for p in board.piece_map().values() if p.color)
        black = sum(values[p.piece_type] for p in board.piece_map().values() if not p.color)
        assert (inv.white_points, inv.black_points, inv.balance) == (white, black, white - black)
        assert material_balance_is(board, white - black).status == "VERIFIED"
        assert material_balance_is(board, white - black + 1).status == "FALSE"
        for name, count in inv.white.items():
            assert piece_count_is(board, "white", name, count).status == "VERIFIED"
            assert piece_count_is(board, "white", name, count + 1).status == "FALSE"


def test_material_changes_only_by_what_each_move_captures_or_promotes():
    values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
    seen = {"capture": 0, "promotion": 0, "en_passant": 0}
    for moves in random_lines(n=60, length=150):
        board = chess.Board()
        for move in moves:
            before, sign = board.copy(), 1 if board.turn else -1
            gain = 0
            if board.is_en_passant(move):
                gain, seen["en_passant"] = 1, seen["en_passant"] + 1
            elif board.is_capture(move):
                gain = values[board.piece_type_at(move.to_square)]
                seen["capture"] += 1
            if move.promotion:
                gain += values[move.promotion] - 1
                seen["promotion"] += 1
            board.push(move)
            assert material_delta(before, board).balance == sign * gain
            totals = sum(abs(v) for v in material_delta(before, board).white.values()) + sum(abs(v) for v in material_delta(before, board).black.values())
            assert totals == (1 if before.is_capture(move) else 0) + (2 if move.promotion else 0)
    assert all(seen.values()), seen


# ── variations ───────────────────────────────────────────────────────────────
def test_every_legal_line_verifies_and_ends_on_the_same_board():
    for moves in random_lines():
        board, plies = chess.Board(), []
        for move in moves:
            plies.append(Ply(san=board.san(move)))
            board.push(move)
        result = verify_variation(chess.STARTING_FEN, plies)
        assert result.status == "VERIFIED", result.reason
        assert result.final_fen == board.fen() and [p.uci for p in result.plies] == [m.uci() for m in moves]


def test_every_true_annotation_on_a_legal_line_verifies():
    values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
    for moves in random_lines(n=20, length=120):
        board, plies = chess.Board(), []
        for move in moves:
            piece = chess.piece_name(board.piece_type_at(move.from_square))
            captured = "none"
            gain = 0
            if board.is_en_passant(move):
                captured, gain = "pawn", 1
            elif board.is_capture(move):
                captured = chess.piece_name(board.piece_type_at(move.to_square))
                gain = values[board.piece_type_at(move.to_square)]
            if move.promotion:
                gain += values[move.promotion] - 1
            plies.append(Ply(san=board.san(move), side=chess.COLOR_NAMES[board.turn], move_number=board.fullmove_number,
                             piece=piece, captures=captured, material_gain=gain))
            board.push(move)
        assert verify_variation(chess.STARTING_FEN, plies).status == "VERIFIED"


def test_a_wrong_annotation_is_false_at_its_ply():
    rng = random.Random(SEED)
    for moves in random_lines(n=30, length=30):
        if not moves:
            continue
        board = chess.Board()
        index = rng.randrange(len(moves))
        plies = []
        for i, move in enumerate(moves):
            turn = chess.COLOR_NAMES[board.turn]
            if i == index:
                plies.append(Ply(san=board.san(move), material_gain=99))
            else:
                plies.append(Ply(san=board.san(move), side=turn))
            board.push(move)
        result = verify_variation(chess.STARTING_FEN, plies)
        assert result.status == "FALSE" and result.first_false_ply == index


def test_an_illegal_ply_stops_the_replay_at_that_ply():
    rng = random.Random(SEED)
    checked = 0
    for moves in random_lines(n=40, length=30):
        if len(moves) < 3:
            continue
        cut = rng.randrange(1, len(moves))
        board, plies = chess.Board(), []
        for move in moves[:cut]:
            plies.append(Ply(san=board.san(move)))
            board.push(move)
        if board.is_game_over():
            continue
        illegal = next((f"{chess.SQUARE_NAMES[a]}{chess.SQUARE_NAMES[b]}" for a in chess.SQUARES for b in chess.SQUARES
                        if a != b and board.piece_type_at(a) == chess.PAWN and board.color_at(a) == board.turn
                        and chess.Move(a, b) not in board.legal_moves and not board.piece_at(b) and chess.square_rank(b) not in (0, 7)), None)
        if illegal is None:
            continue
        result = verify_variation(chess.STARTING_FEN, plies + [Ply(san=illegal), Ply(san="e4")])
        assert result.status == "FALSE" and result.first_false_ply == cut and len(result.plies) == cut + 1
        assert result.final_fen == board.fen()
        checked += 1
    assert checked > 10


def test_castling_en_passant_promotion_and_capture_promotion_replay():
    castle = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
    assert verify_variation(castle, [Ply(san="O-O", piece="king", captures="none"), Ply(san="O-O-O", piece="king")]).status == "VERIFIED"
    no_rights = "r3k2r/8/8/8/8/8/8/R3K2R w - - 0 1"
    assert verify_variation(no_rights, [Ply(san="O-O")]).status == "FALSE"
    through_check = "4kr2/8/8/8/8/8/8/R3K2R w KQ - 0 1"
    assert verify_variation(through_check, [Ply(san="O-O")]).status == "FALSE"
    ep = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2"
    assert verify_variation(ep, [Ply(san="exd6", captures="pawn", material_gain=1)]).status == "VERIFIED"
    assert verify_variation("4k3/8/8/3pP3/8/8/8/4K3 w - - 0 2", [Ply(san="exd6")]).status == "FALSE"
    promo = "1n2k3/P7/8/8/8/8/8/4K3 w - - 0 1"
    assert verify_variation(promo, [Ply(san="axb8=N", captures="knight", material_gain=5)]).status == "VERIFIED"
    assert verify_variation(promo, [Ply(san="a8=R", captures="none", material_gain=4)]).status == "VERIFIED"
    assert verify_variation(promo, [Ply(san="a8")]).status == "FALSE"


def test_check_and_mate_annotations_follow_the_board():
    seen = {"check": 0, "mate": 0}
    for moves in random_lines(n=80, length=200):
        board = chess.Board()
        for move in moves:
            san = board.san(move)
            bare = san.rstrip("+#")
            start = board.fen()
            gives_check = board.gives_check(move)
            after = board.copy()
            after.push(move)
            mate = after.is_checkmate()
            assert (verify_variation(start, [Ply(san=bare + "+")]).status == "VERIFIED") == gives_check
            assert (verify_variation(start, [Ply(san=bare + "#")]).status == "VERIFIED") == mate
            seen["check"] += gives_check
            seen["mate"] += mate
            board.push(move)
    assert seen["check"] > 0


# ── terminal state ───────────────────────────────────────────────────────────
def finished_games(games=60, seed=SEED + 2):
    rng = random.Random(seed)
    out = []
    for _ in range(games):
        board = chess.Board()
        while not board.is_game_over():
            board.push(rng.choice(list(board.legal_moves)))
        out.append(chess.Board(board.fen()))
    return out


def test_terminal_predicates_agree_with_python_chess():
    finished = 0
    for board in POSITIONS + finished_games():
        outcome = board.outcome()
        assert (is_terminal(board).status == "VERIFIED") == (outcome is not None)
        assert (is_checkmate(board).status == "VERIFIED") == board.is_checkmate()
        assert (is_stalemate(board).status == "VERIFIED") == board.is_stalemate()
        assert (is_insufficient_material(board).status == "VERIFIED") == board.is_insufficient_material()
        assert (is_check(board).status == "VERIFIED") == board.is_check()
        assert legal_move_count_is(board, board.legal_moves.count()).status == "VERIFIED"
        assert legal_move_count_is(board, board.legal_moves.count() + 1).status == "FALSE"
        finished += outcome is not None
    assert finished > 3


def test_no_move_follows_a_finished_game():
    for moves in random_lines(n=200, length=400):
        board = chess.Board()
        plies = []
        for move in moves:
            plies.append(Ply(san=board.san(move)))
            board.push(move)
        if not board.is_game_over():
            continue
        fen = board.fen()
        replies = [Ply(san=chess.Board(fen).san(m)) for m in list(chess.Board(fen).legal_moves)[:1]] or [Ply(san="Ke2")]
        result = verify_variation(chess.STARTING_FEN, plies + replies)
        assert result.status == "FALSE" and result.first_false_ply == len(plies)


# ── shape of every answer ────────────────────────────────────────────────────
def test_every_predicate_answers_with_a_status_and_serialisable_evidence():
    samples = {"square": "e4", "source": "e2", "target": "e4", "slider": "f1", "blocker": "e2", "pinner": "d1", "pinned": "d2",
               "file": "e", "color": "white", "piece": "pawn", "count": 1, "moves": 2, "points": 0}
    import inspect
    for board in POSITIONS[:40]:
        for name, run in PREDICATES.items():
            args = {p: samples[p] for p in inspect.signature(run).parameters if p != "board" and p in samples}
            r = run(board, **args)
            assert r.status in ("VERIFIED", "FALSE", "UNKNOWN") and r.predicate == name
            json.dumps(r.evidence)
            assert r.reason or r.status == "VERIFIED"


def test_the_dispatcher_and_the_direct_call_agree():
    for board in POSITIONS[:40]:
        assert verify_fact(board.fen(), Fact(predicate="is_check")).status == is_check(board).status
