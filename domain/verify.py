"""Semantic chess-fact verifier: proves or disproves simple statements about a board or a
line of moves, whoever made them (an extracted model claim, a test fixture, a future
coaching claim). Thin wrappers over python-chess; no engine, no LLM, no prose parsing.
Nothing in the production path imports this yet.

Every check answers VERIFIED, FALSE or UNKNOWN. UNKNOWN means "not proven": an unregistered
predicate (strategic ideas such as traps or prophylaxis have none, on purpose), malformed
arguments, an invalid board. A consumer may only trust VERIFIED. Absence of evidence against
a statement is never a proof of it.

These are geometry and rule facts. "Attacks" ignores pins and whether the capture is good;
the evidence says when the attacker is pinned. Reasons a move is good are out of scope."""
import functools
import inspect
from typing import Any, Literal, Optional

import chess

from domain.models import Side, Square, _Model

Status = Literal["VERIFIED", "FALSE", "UNKNOWN"]
PieceName = Literal["pawn", "knight", "bishop", "rook", "queen", "king"]
PIECE_NAMES = chess.PIECE_NAMES[1:]
POINTS = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
PREDICATES: dict = {}


class Fact(_Model):
    """A statement to check: a registered predicate name and its arguments."""
    predicate: str
    args: dict[str, Any] = {}


class FactResult(_Model):
    predicate: str
    status: Status
    fen: str                       # the board the fact was judged on
    args: dict[str, Any] = {}
    reason: Optional[str] = None
    evidence: dict[str, Any] = {}  # JSON-ready; squares, pieces, blockers


class PieceOnSquare(_Model):
    square: Square
    color: Side
    piece: PieceName


class MaterialInventory(_Model):
    white: dict[str, int]
    black: dict[str, int]
    white_points: int
    black_points: int
    balance: int                   # White minus Black, in points


class MaterialDelta(_Model):
    white: dict[str, int]          # after minus before, per piece type
    black: dict[str, int]
    balance: int                   # change in White minus Black


class Ply(_Model):
    """One typed move of a variation. Only what is filled in is claimed. The SAN's own
    "x", "+" and "#" are claims too (python-chess's parser ignores them)."""
    san: str
    side: Optional[Side] = None
    move_number: Optional[int] = None
    piece: Optional[PieceName] = None
    captures: Optional[Literal["pawn", "knight", "bishop", "rook", "queen", "none"]] = None
    material_gain: Optional[int] = None         # the mover's points, this ply alone
    facts_after: tuple[Fact, ...] = ()          # judged on this branch's board after the ply


class PlyResult(_Model):
    ply: int                       # index in the variation
    claimed_san: str
    san: Optional[str] = None      # canonical, when the move could be played
    uci: Optional[str] = None
    status: Status
    fen_before: str
    fen_after: Optional[str] = None
    checks: tuple[FactResult, ...] = ()


class VariationResult(_Model):
    status: Status
    start_fen: str
    final_fen: Optional[str] = None
    first_false_ply: Optional[int] = None
    reason: Optional[str] = None
    material_delta: Optional[MaterialDelta] = None
    plies: tuple[PlyResult, ...] = ()


# ── arguments (a bad one makes the fact UNKNOWN, see _check) ─────────────────
class _BadArgument(ValueError):
    pass


def _square(name) -> int:
    if name not in chess.SQUARE_NAMES:
        raise _BadArgument(f"not a square: {name!r}")
    return chess.parse_square(name)


def _color(name) -> chess.Color:
    if name not in ("white", "black"):
        raise _BadArgument(f"not a color: {name!r}")
    return name == "white"


def _piece_type(name) -> Optional[int]:
    if name is None:
        return None
    if name not in PIECE_NAMES:
        raise _BadArgument(f"not a piece: {name!r}")
    return chess.PIECE_NAMES.index(name)


def _file(name) -> int:
    if name not in chess.FILE_NAMES:
        raise _BadArgument(f"not a file: {name!r}")
    return chess.FILE_NAMES.index(name)


def _int(value) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _BadArgument(f"not an integer: {value!r}")
    return value


# ── small helpers ────────────────────────────────────────────────────────────
def _on(board: chess.Board, square: int) -> Optional[PieceOnSquare]:
    p = board.piece_at(square)
    return p and PieceOnSquare(square=chess.square_name(square), color=chess.COLOR_NAMES[p.color],
                               piece=chess.piece_name(p.piece_type))


def _dump(items) -> list:
    return [i.model_dump() for i in items]


def _on_line(a: int, b: int) -> bool:
    if a == b:
        return False
    files, ranks = abs(chess.square_file(a) - chess.square_file(b)), abs(chess.square_rank(a) - chess.square_rank(b))
    return files == 0 or ranks == 0 or files == ranks


def _on_diagonal(a: int, b: int) -> bool:
    return a != b and abs(chess.square_file(a) - chess.square_file(b)) == abs(chess.square_rank(a) - chess.square_rank(b))


def _moves_along(piece_type: int, a: int, b: int) -> bool:
    """A sliding piece of this type travels along the line through a and b."""
    if not _on_line(a, b):
        return False
    return piece_type == chess.QUEEN or piece_type == (chess.BISHOP if _on_diagonal(a, b) else chess.ROOK)


def _between(board: chess.Board, a: int, b: int) -> tuple[PieceOnSquare, ...]:
    return tuple(_on(board, s) for s in sorted(chess.SquareSet(chess.between(a, b) & board.occupied)))


def _on_file(board: chess.Board, file_index: int) -> list:
    return [_on(board, s) for s in chess.SquareSet(chess.BB_FILES[file_index] & board.occupied)]


def _pawns_on_file(board: chess.Board, file_index: int, color: chess.Color) -> list:
    return sorted(chess.SquareSet(chess.BB_FILES[file_index] & board.pieces_mask(chess.PAWN, color)))


def _enemy_pawns_ahead(board: chess.Board, square: int, color: chess.Color) -> list:
    """Enemy pawns on the same or an adjacent file, strictly ahead of a `color` pawn."""
    f, r = chess.square_file(square), chess.square_rank(square)
    return sorted(chess.square_name(s) for s in board.pieces(chess.PAWN, not color)
                  if abs(chess.square_file(s) - f) <= 1 and (chess.square_rank(s) > r if color else chess.square_rank(s) < r))


def _true(**evidence):
    return "VERIFIED", None, evidence


def _false(reason: str, **evidence):
    return "FALSE", reason, evidence


def _yes_no(value: bool, reason: str, **evidence):
    return _true(**evidence) if value else _false(reason, **evidence)


def _predicate(fn):
    """Registers fn(board, ...) -> (status, reason, evidence); callers get a FactResult."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def run(board: chess.Board, *args, **kwargs) -> FactResult:
        named = dict(signature.bind(board, *args, **kwargs).arguments)
        named.pop("board")
        status, reason, evidence = fn(board, *args, **kwargs)
        return FactResult(predicate=fn.__name__, status=status, fen=board.fen(), args=named, reason=reason,
                          evidence=evidence)
    PREDICATES[fn.__name__] = run
    return run


# ── pieces ───────────────────────────────────────────────────────────────────
@_predicate
def piece_at(board, square, color=None, piece=None):
    sq, wanted_color, wanted = _square(square), None if color is None else _color(color), _piece_type(piece)
    found = board.piece_at(sq)
    if found is None:
        return _false(f"{square} is empty")
    occupant = _on(board, sq).model_dump()
    if wanted_color is not None and found.color != wanted_color:
        return _false(f"the piece on {square} is {occupant['color']}", occupant=occupant)
    if wanted is not None and found.piece_type != wanted:
        return _false(f"the piece on {square} is a {occupant['piece']}", occupant=occupant)
    return _true(occupant=occupant)


@_predicate
def square_empty(board, square):
    occupant = _on(board, _square(square))
    return _yes_no(occupant is None, f"{square} is occupied", occupant=occupant and occupant.model_dump())


@_predicate
def piece_count_is(board, color, piece, count):
    found = len(board.pieces(_piece_type(piece), _color(color)))
    return _yes_no(found == _int(count), f"{color} has {found}, not {count}", count=found)


# ── relations ────────────────────────────────────────────────────────────────
def _reach(board, source, target, piece):
    """(evidence, failure reason) for "the piece on source reaches target", shared by
    attacks and defends. Reaches means python-chess's attack set: blockers stop it."""
    src, tgt, wanted = _square(source), _square(target), _piece_type(piece)
    found = board.piece_at(src)
    if found is None:
        return {"source_piece": None}, f"{source} is empty"
    evidence = {"source_piece": _on(board, src).model_dump(), "target_piece": (_on(board, tgt) and _on(board, tgt).model_dump()),
                "source_pinned": board.is_pinned(found.color, src)}
    if wanted is not None and found.piece_type != wanted:
        return evidence, f"the piece on {source} is a {chess.piece_name(found.piece_type)}"
    if tgt not in board.attacks(src):
        slider = found.piece_type in (chess.BISHOP, chess.ROOK, chess.QUEEN) and _moves_along(found.piece_type, src, tgt)
        if slider:
            evidence["blockers"] = _dump(_between(board, src, tgt))
            return evidence, f"the line from {source} to {target} is blocked"
        return evidence, f"a {chess.piece_name(found.piece_type)} on {source} does not reach {target}"
    return evidence, None


@_predicate
def attacks(board, source, target, piece=None):
    """The piece on source bears on target, which holds an enemy piece or nothing."""
    evidence, failure = _reach(board, source, target, piece)
    if failure:
        return _false(failure, **evidence)
    found, other = board.piece_at(_square(source)), board.piece_at(_square(target))
    if other is not None and other.color == found.color:
        return _false(f"{target} holds a piece of the same colour (it defends it)", **evidence)
    return _true(**evidence)


@_predicate
def defends(board, source, target, piece=None):
    """The piece on source bears on target, which holds a friendly piece or nothing
    (guarding an empty square). X-rays through another piece do not count."""
    evidence, failure = _reach(board, source, target, piece)
    if failure:
        return _false(failure, **evidence)
    found, other = board.piece_at(_square(source)), board.piece_at(_square(target))
    if other is not None and other.color != found.color:
        return _false(f"{target} holds an enemy piece (it attacks it)", **evidence)
    return _true(**evidence)


def ray_blockers(board: chess.Board, source: str, target: str) -> Optional[tuple[PieceOnSquare, ...]]:
    """Pieces strictly between two squares, in square order; None when they share no line."""
    a, b = _square(source), _square(target)
    return _between(board, a, b) if _on_line(a, b) else None


@_predicate
def ray_clear(board, source, target):
    blockers = ray_blockers(board, source, target)
    if blockers is None:
        return _false(f"{source} and {target} share no rank, file or diagonal")
    return _yes_no(not blockers, f"the line from {source} to {target} is blocked", blockers=_dump(blockers))


@_predicate
def blocks(board, slider, blocker):
    """The piece on blocker is the first thing on a line the slider moves along."""
    a, b = _square(slider), _square(blocker)
    found = board.piece_at(a)
    if found is None or found.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        return _false(f"no sliding piece on {slider}")
    if board.piece_at(b) is None:
        return _false(f"{blocker} is empty")
    if not _moves_along(found.piece_type, a, b):
        return _false(f"a {chess.piece_name(found.piece_type)} on {slider} does not move along the line to {blocker}")
    between = _between(board, a, b)
    return _yes_no(not between, f"another piece stands between {slider} and {blocker}", blockers=_dump(between))


@_predicate
def pinned_against(board, pinner, pinned, target):
    """Line geometry of a pin: a slider, an enemy piece on its line, and behind that piece,
    with nothing else between, a more valuable piece of that piece's side."""
    a, m, z = _square(pinner), _square(pinned), _square(target)
    slider, middle, behind = board.piece_at(a), board.piece_at(m), board.piece_at(z)
    if slider is None or slider.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        return _false(f"no sliding piece on {pinner}")
    if middle is None or middle.color == slider.color:
        return _false(f"no enemy piece on {pinned}")
    if behind is None or behind.color != middle.color:
        return _false(f"no piece of the pinned side on {target}")
    if not (_moves_along(slider.piece_type, a, z) and chess.between(a, z) & chess.BB_SQUARES[m]):
        return _false(f"{pinned} and {target} are not in line along the way the piece on {pinner} moves")
    blockers = tuple(p for p in _between(board, a, z) if p.square != pinned)
    if blockers:
        return _false("another piece stands on the line", blockers=_dump(blockers))
    absolute = behind.piece_type == chess.KING
    if not absolute and POINTS[behind.piece_type] <= POINTS[middle.piece_type]:
        return _false(f"the piece on {target} is not worth more than the one on {pinned}")
    return _true(absolute=absolute, blockers=[])


# ── files and diagonals ──────────────────────────────────────────────────────
@_predicate
def is_open_file(board, file):
    """The chess term: no pawn of either colour on the file. Pieces may stand on it; they
    are reported, and is_file_clear is the stricter question."""
    f = _file(file)
    pawns = _pawns_on_file(board, f, chess.WHITE) + _pawns_on_file(board, f, chess.BLACK)
    return _yes_no(not pawns, f"the {file}-file has pawns", pawns=sorted(chess.square_name(s) for s in pawns),
                   pieces_on_file=_dump(_on_file(board, f)))


@_predicate
def is_semi_open_file(board, file, color):
    """No pawn of `color` on the file, at least one enemy pawn."""
    f, c = _file(file), _color(color)
    own, enemy = _pawns_on_file(board, f, c), _pawns_on_file(board, f, not c)
    return _yes_no(not own and bool(enemy), f"the {file}-file is not semi-open for {color}",
                   own_pawns=[chess.square_name(s) for s in own], enemy_pawns=[chess.square_name(s) for s in enemy])


@_predicate
def is_file_clear(board, file):
    occupants = _on_file(board, _file(file))
    return _yes_no(not occupants, f"the {file}-file has pieces on it", occupants=_dump(occupants))


@_predicate
def is_open_diagonal(board, source, target):
    a, b = _square(source), _square(target)
    if not _on_diagonal(a, b):
        return _false(f"{source} and {target} are not on a diagonal")
    between = _between(board, a, b)
    return _yes_no(not between, f"the diagonal from {source} to {target} is blocked", blockers=_dump(between))


# ── pawns ────────────────────────────────────────────────────────────────────
@_predicate
def is_passed_pawn(board, square):
    sq = _square(square)
    found = board.piece_at(sq)
    if found is None or found.piece_type != chess.PAWN:
        return _false(f"no pawn on {square}")
    ahead = _enemy_pawns_ahead(board, sq, found.color)
    return _yes_no(not ahead, f"enemy pawns stand ahead of {square}: {', '.join(ahead)}", enemy_pawns_ahead=ahead)


@_predicate
def has_passed_pawn(board, color, file):
    c = _color(color)
    pawns = _pawns_on_file(board, _file(file), c)
    passed = [chess.square_name(s) for s in pawns if not _enemy_pawns_ahead(board, s, c)]
    return _yes_no(bool(passed), f"{color} has no passed pawn on the {file}-file",
                   pawns=[chess.square_name(s) for s in pawns], passed=passed)


def promotion_distance(board: chess.Board, square: str) -> Optional[int]:
    """Fewest pawn moves to promote, by rank alone (a capture also advances one rank; a pawn
    on its home rank may double-step). Whether the way is free is a separate question.
    None when no pawn stands there."""
    sq = _square(square)
    found = board.piece_at(sq)
    if found is None or found.piece_type != chess.PAWN:
        return None
    rank = chess.square_rank(sq)
    return 7 - rank - (rank == 1) if found.color else rank - (rank == 6)


@_predicate
def promotion_distance_is(board, square, moves):
    wanted, distance = _int(moves), promotion_distance(board, square)
    if distance is None:
        return _false(f"no pawn on {square}")
    if distance != wanted:
        return _false(f"the pawn on {square} needs {distance} moves", moves=distance)
    sq = _square(square)
    forward = board.piece_at(sq).color
    path = [s for s in chess.SquareSet(chess.BB_FILES[chess.square_file(sq)] & board.occupied)
            if (chess.square_rank(s) > chess.square_rank(sq) if forward else chess.square_rank(s) < chess.square_rank(sq))]
    if path:  # the count holds only if a capture gets round the blocker; not proven here
        return "UNKNOWN", f"pieces stand on the path of the pawn on {square}", {
            "moves": distance, "path_blockers": _dump(_on(board, s) for s in path)}
    return _true(moves=distance, path_blockers=[])


# ── material ─────────────────────────────────────────────────────────────────
def material_inventory(board: chess.Board) -> MaterialInventory:
    def counts(color):
        return {chess.piece_name(t): len(board.pieces(t, color)) for t in chess.PIECE_TYPES}

    def points(color):
        return sum(POINTS[t] * len(board.pieces(t, color)) for t in chess.PIECE_TYPES)
    white, black = points(chess.WHITE), points(chess.BLACK)
    return MaterialInventory(white=counts(chess.WHITE), black=counts(chess.BLACK), white_points=white,
                             black_points=black, balance=white - black)


def material_delta(before: chess.Board, after: chess.Board) -> MaterialDelta:
    a, b = material_inventory(before), material_inventory(after)
    return MaterialDelta(white={n: b.white[n] - a.white[n] for n in PIECE_NAMES},
                         black={n: b.black[n] - a.black[n] for n in PIECE_NAMES}, balance=b.balance - a.balance)


@_predicate
def material_balance_is(board, points):
    inventory = material_inventory(board)
    return _yes_no(inventory.balance == _int(points), f"White minus Black is {inventory.balance} points",
                   inventory=inventory.model_dump())


# ── game state ───────────────────────────────────────────────────────────────
@_predicate
def legal_move_count_is(board, count):
    found = board.legal_moves.count()
    return _yes_no(found == _int(count), f"there are {found} legal moves", count=found)


@_predicate
def is_check(board):
    return _yes_no(board.is_check(), "the side to move is not in check", checkers=sorted(
        chess.square_name(s) for s in board.checkers()))


@_predicate
def is_checkmate(board):
    return _yes_no(board.is_checkmate(), "the position is not checkmate")


@_predicate
def is_stalemate(board):
    return _yes_no(board.is_stalemate(), "the position is not stalemate")


@_predicate
def is_insufficient_material(board):
    return _yes_no(board.is_insufficient_material(), "there is enough material to mate")


@_predicate
def is_terminal(board):
    """The game is over by rule. ponytail: from a bare FEN there is no move history, so a
    fivefold repetition cannot be seen; add a stack-aware check if it ever matters."""
    outcome = board.outcome()
    if outcome is None:
        return _false("the game is not over", legal_moves=board.legal_moves.count())
    return _true(termination=outcome.termination.name, result=outcome.result())


# ── entry points ─────────────────────────────────────────────────────────────
def _load(fen: str) -> Optional[chess.Board]:
    try:
        board = chess.Board(fen)
    except ValueError:
        return None
    return board if board.is_valid() else None


def _check(board: chess.Board, fact: Fact) -> FactResult:
    def unknown(reason):
        return FactResult(predicate=fact.predicate, status="UNKNOWN", fen=board.fen(), args=fact.args, reason=reason)
    run = PREDICATES.get(fact.predicate)
    if run is None:
        return unknown(f"no verifier for '{fact.predicate}'")
    try:
        inspect.signature(run).bind(board, **fact.args)
    except TypeError as e:
        return unknown(f"bad arguments: {e}")
    try:
        return run(board, **fact.args)
    except _BadArgument as e:
        return unknown(f"bad arguments: {e}")


def verify_fact(fen: str, fact: Fact) -> FactResult:
    board = _load(fen)
    if board is None:
        return FactResult(predicate=fact.predicate, status="UNKNOWN", fen=fen, args=fact.args, reason="invalid position")
    return _check(board, fact)


def _worst(statuses) -> Status:
    statuses = set(statuses)
    return "FALSE" if "FALSE" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "VERIFIED"


def _replay_ply(board: chess.Board, index: int, claim: Ply) -> tuple[PlyResult, Optional[chess.Board]]:
    """Judges one ply. Returns its result and the board to continue from, or None when the
    line can no longer be followed (illegal, game over, wrong side, unreadable move)."""
    before = board.fen()

    def check(predicate, status, reason=None, on=board, **evidence):
        return FactResult(predicate=predicate, status=status, fen=on.fen(), reason=reason, evidence=evidence)

    def stop(*checks):
        return PlyResult(ply=index, claimed_san=claim.san, status=_worst(c.status for c in checks), fen_before=before,
                         checks=checks), None

    if board.is_game_over():
        return stop(check("move_legal", "FALSE", f"the game is over ({board.outcome().termination.name})"))
    checks = []
    if claim.side is not None:
        turn = chess.COLOR_NAMES[board.turn]
        if claim.side != turn:
            return stop(check("side_to_move", "FALSE", f"{turn} is to move", turn=turn))
        checks.append(check("side_to_move", "VERIFIED", turn=turn))
    if claim.move_number is not None:
        status = "VERIFIED" if claim.move_number == board.fullmove_number else "FALSE"
        checks.append(check("move_number", status, None if status == "VERIFIED" else
                            f"the move number is {board.fullmove_number}", move_number=board.fullmove_number))
    try:
        move = board.parse_san(claim.san)
    except chess.IllegalMoveError as e:
        return stop(*checks, check("move_legal", "FALSE", str(e)))
    except ValueError as e:  # ambiguous or not a move at all: nothing about the line is proven
        return stop(*checks, check("move_legal", "UNKNOWN", str(e)))
    checks.insert(0, check("move_legal", "VERIFIED"))

    moved = chess.piece_name(board.piece_type_at(move.from_square))
    if claim.piece is not None:
        status = "VERIFIED" if claim.piece == moved else "FALSE"
        checks.append(check("piece_identity", status, None if status == "VERIFIED" else f"the moving piece is a {moved}",
                            piece=moved))
    captured = "none"
    if board.is_en_passant(move):
        captured = "pawn"
    elif board.is_capture(move):
        captured = chess.piece_name(board.piece_type_at(move.to_square))
    if claim.captures is not None or "x" in claim.san:
        holds = captured == claim.captures if claim.captures is not None else captured != "none"
        checks.append(check("capture_identity", "VERIFIED" if holds else "FALSE",
                            None if holds else f"the capture is: {captured}", captured=captured))
    san = board.san(move)
    after = board.copy()
    after.push(move)
    if claim.material_gain is not None:
        gain = material_delta(board, after).balance * (1 if board.turn else -1)
        holds = gain == claim.material_gain
        checks.append(check("material_gain", "VERIFIED" if holds else "FALSE",
                            None if holds else f"the mover gains {gain} points", gain=gain))
    if claim.san.endswith("#"):
        checks.append(check("mate_claim", "VERIFIED" if after.is_checkmate() else "FALSE",
                            None if after.is_checkmate() else "the move does not give mate", on=after))
    elif claim.san.endswith("+"):
        checks.append(check("check_claim", "VERIFIED" if after.is_check() else "FALSE",
                            None if after.is_check() else "the move does not give check", on=after))
    checks.extend(_check(after, fact) for fact in claim.facts_after)
    return PlyResult(ply=index, claimed_san=claim.san, san=san, uci=move.uci(), status=_worst(c.status for c in checks),
                     fen_before=before, fen_after=after.fen(), checks=tuple(checks)), after


def verify_variation(start_fen: str, plies: list) -> VariationResult:
    """Replays typed moves from start_fen, judging each ply's claims on the branch's own
    board. A false claim on a legal move is reported and the replay continues, so every
    failure is seen; a move that cannot be followed ends it. The line is never trusted as
    engine evidence: this says it is playable and its statements hold, not that it is good."""
    start = _load(start_fen)
    if start is None:
        return VariationResult(status="UNKNOWN", start_fen=start_fen, reason="invalid position")
    if not plies:
        return VariationResult(status="UNKNOWN", start_fen=start_fen, final_fen=start.fen(), reason="no moves to verify")
    board, results = start, []
    for index, claim in enumerate(plies):
        result, following = _replay_ply(board, index, claim)
        results.append(result)
        if following is None:
            break
        board = following
    first_false = next((r.ply for r in results if r.status == "FALSE"), None)
    return VariationResult(status=_worst(r.status for r in results), start_fen=start_fen, final_fen=board.fen(),
                           first_false_ply=first_false, material_delta=material_delta(start, board), plies=tuple(results))
