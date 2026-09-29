import re
import sys
import chess
import chess.pgn
from typing import Dict, Any, Optional, Set

from scripts.move_reference import ply_of

# Piece moves, pawn captures, promotions, castling. Bare pawn pushes ("e4") are
# indistinguishable from squares and are covered by the square whitelist.
SAN_TOKEN = re.compile(
    r'\b(?:[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8](?:=[QRBN])?|[a-h][18]=[QRBN]|O-O-O|O-O)[+#]?'
)

def extract_squares(text: str) -> Set[str]:
    """Extracts all square tokens (a1-h8) from the text."""
    squares = re.findall(r'\b[a-h][1-8]\b', text.lower())
    return set(squares)

def extract_squares_from_move(move_san: str) -> Set[str]:
    """Extracts squares from a SAN move string (e.g. Nf3 -> f3, O-O -> empty)."""
    if not move_san:
        return set()
    clean = re.sub(r'[KQRBNx+#=]', '', move_san)
    squares = re.findall(r'[a-h][1-8]', clean.lower())
    return set(squares)

def _payload_moves(flag: Dict[str, Any]) -> list:
    alternatives = [m for line in flag.get("alternatives", []) for m in line]
    return ([flag.get("move_san", ""), flag.get("best_move_san", "")] + list(flag.get("pv", []))
            + list(flag.get("refutation_pv", [])) + alternatives)

def get_allowed_moves(flag: Dict[str, Any]) -> Set[str]:
    """SAN moves (check/mate suffix stripped) the narration may cite."""
    return {m.rstrip("+#") for m in _payload_moves(flag) if m}

def get_allowed_squares(flag: Dict[str, Any]) -> Set[str]:
    """Computes the set of allowed squares from the flag payload."""
    allowed = set()
    for move in _payload_moves(flag):
        allowed.update(extract_squares_from_move(move))

    concessions = flag.get("concessions", {})
    if concessions:
        for k in ["new_weak_squares", "new_backward_pawns", "new_fixed_backward_pawns", "new_pawn_unsupported"]:
            for sq in concessions.get(k, []):
                allowed.update(re.findall(r'[a-h][1-8]', sq.lower()))

    for sqs in flag.get("position_facts", {}).values():
        allowed.update(sq.lower() for sq in sqs)

    return allowed

def merge_flags(flags: list, played_moves: list = ()) -> Dict[str, Any]:
    """One pseudo-flag whose payload is every stored flag's payload plus the moves
    actually played: the grounding set for free-form conversation."""
    moves = [m for f in flags for m in _payload_moves(f)] + list(played_moves)
    concessions: Dict[str, list] = {}
    for f in flags:
        for k, sqs in (f.get("concessions") or {}).items():
            concessions.setdefault(k, []).extend(sqs)
    return {"pv": moves, "concessions": concessions}

def check_narration_moves_legality(narration: str, game: chess.pgn.Game) -> Optional[str]:
    """Numbered SAN (e.g. '13...a5') must be legal at that ply of the real game.
    Returns the offending citation, or None."""
    san = r'(?:[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O-O|O-O)'
    # "2.Nc3 Nf6": a numbered move plus one optional unnumbered reply
    pattern = rf'\b(\d+)(?:\s*(\.\.\.|\.))?\s*({san})(?:\s+({san}))?\b'
    # Models often write the typographic ellipsis ("21…Nxf3"); read it as "..."
    matches = re.findall(pattern, narration.replace("\u2026", "..."))
    if not matches:
        return None

    mainline = list(game.mainline_moves())
    val_board = None
    prev_move_num = None
    prev_is_black = None

    for move_num_str, dots, san, reply in matches:
        move_num = int(move_num_str)
        is_black = (dots == "...")

        consecutive = False
        if val_board is not None:
            if prev_is_black:
                if not is_black and move_num == prev_move_num + 1:
                    consecutive = True
            else:
                if is_black and move_num == prev_move_num:
                    consecutive = True

        if not consecutive:
            val_board = game.board()
            target_ply = ply_of(game.board(), move_num, "black" if is_black else "white")
            if not 0 <= target_ply <= len(mainline):
                return f"{move_num}{dots or '.'}{san}"
            for m in mainline[:target_ply]:
                val_board.push(m)

        try:
            val_board.push(val_board.parse_san(san))
            prev_move_num = move_num
            prev_is_black = is_black
        except ValueError:
            return f"{move_num}{dots or '.'}{san}"

        # ponytail: an unparsable reply is treated as prose, not a violation;
        # piece moves in it are still caught by the payload membership check
        if reply:
            try:
                val_board.push(val_board.parse_san(reply))
                prev_move_num = move_num + (1 if is_black else 0)
                prev_is_black = not is_black
            except ValueError:
                pass

    return None

def narration_violation(narration: str, flag: Dict[str, Any], game: Optional[chess.pgn.Game] = None) -> Optional[str]:
    """
    Returns a short reason the narration is rejected, or None if it is valid:
    1. Does not start with the header (code emits it).
    2. Everything grounding_violation checks.
    """
    if narration.strip().startswith("Move "):
        return "it started with 'Move ' (the header is emitted by code)"
    return grounding_violation(narration, flag, game)

def grounding_violation(text: str, flag: Dict[str, Any], game: Optional[chess.pgn.Game] = None) -> Optional[str]:
    """
    1. No internal feature magnitudes like "delta of -X".
    2. Every SAN move cited must come from the payload (played, best, PV, refutation,
       alternatives).
    3. Every bare square must come from the payload.
    4. With the real game, every numbered move must be legal at that ply.
    """
    low = text.lower()
    if re.search(r'delta\s+of\s+-?\d+', low) or re.search(r'delta\s+magnitude', low):
        return "it quoted internal feature magnitudes (e.g. 'delta of -X')"

    allowed_moves = get_allowed_moves(flag)
    for san in SAN_TOKEN.findall(text):
        if san.rstrip("+#") not in allowed_moves:
            return f"move {san} is not in the engine payload"

    allowed_squares = get_allowed_squares(flag)
    for sq in sorted(extract_squares(text)):
        if sq not in allowed_squares:
            return f"square {sq} is not in the engine payload"

    if game is not None:
        bad = check_narration_moves_legality(text, game)
        if bad:
            return f"{bad} is not a legal move at that point of the game"

    return None

def validate_narration(narration: str, flag: Dict[str, Any], game: Optional[chess.pgn.Game] = None) -> bool:
    reason = narration_violation(narration, flag, game)
    if reason:
        print(f"VALIDATION REJECTED: {reason}. Narration: {narration[:50]}...", file=sys.stderr)
    return reason is None


# M13: claim-grounded narration. Terms with a precise Prophylax meaning may only be
# used when a claim of that kind exists (D9); ordinary vocabulary stays allowed.
CLAIM_TERMS = [
    (re.compile(r"\bbackward pawns?\b", re.I), "backward pawn", "backward_pawn_created"),
    (re.compile(r"\bweak squares?\b", re.I), "weak square", "weak_square_created"),
    (re.compile(r"\bholes?\b", re.I), "hole", "weak_square_created"),
    (re.compile(r"\boutposts?\b", re.I), "outpost", "weak_square_created"),
]
_SQUARE = re.compile(r"^[a-h][1-8]$")


def concept_violation(text: str, claims: list) -> Optional[str]:
    """A precise term used with no claim of its kind behind it, or None."""
    types = {c.type for c in claims}
    for pattern, term, ctype in CLAIM_TERMS:
        if ctype not in types and pattern.search(text):
            return f"it used the term '{term}', but no verified claim is about one"
    return None


def claims_ground(claims: list) -> Dict[str, Any]:
    """The grounding payload for claim-grounded narration: the moves and squares the
    claims (and their evidence) name, in the shape grounding_violation reads."""
    ground: Dict[str, Any] = {"move_san": claims[0].move_san if claims else "", "pv": [], "refutation_pv": []}
    squares = set()
    for c in claims:
        if c.subject:
            squares.add(c.subject)
        for e in c.evidence:
            values = e.value if isinstance(e.value, tuple) else (e.value,)
            if e.fact == "best_move":
                ground["best_move_san"] = e.value
            elif e.fact == "pv":
                ground["pv"] = list(e.value)
            elif e.fact == "refutation_pv":
                ground["refutation_pv"] = list(e.value)
            else:
                squares.update(v for v in values if isinstance(v, str) and _SQUARE.match(v))
    ground["position_facts"] = {"claim squares": sorted(squares)}
    return ground
