import re
import sys
import chess
import chess.pgn
from typing import Dict, Any, Optional, Set

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
    return [flag.get("move_san", ""), flag.get("best_move_san", "")] + list(flag.get("pv", [])) + list(flag.get("refutation_pv", []))

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

    return allowed

def check_narration_moves_legality(narration: str, game: chess.pgn.Game) -> Optional[str]:
    """Numbered SAN (e.g. '13...a5') must be legal at that ply of the real game.
    Returns the offending citation, or None."""
    san = r'(?:[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O-O|O-O)'
    # "2.Nc3 Nf6": a numbered move plus one optional unnumbered reply
    pattern = rf'\b(\d+)(?:\s*(\.\.\.|\.))?\s*({san})(?:\s+({san}))?\b'
    matches = re.findall(pattern, narration)
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
            target_ply = (move_num - 1) * 2 + (1 if is_black else 0)
            if target_ply > len(mainline):
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
    1. No internal feature magnitudes like "delta of -X".
    2. Does not start with the header (code emits it).
    3. Every SAN move cited must come from the payload (played, best, PV, refutation).
    4. Every bare square must come from the payload.
    5. With the real game, every numbered move must be legal at that ply.
    """
    low = narration.lower()
    if re.search(r'delta\s+of\s+-?\d+', low) or re.search(r'delta\s+magnitude', low):
        return "it quoted internal feature magnitudes (e.g. 'delta of -X')"

    if narration.strip().startswith("Move "):
        return "it started with 'Move ' (the header is emitted by code)"

    allowed_moves = get_allowed_moves(flag)
    for san in SAN_TOKEN.findall(narration):
        if san.rstrip("+#") not in allowed_moves:
            return f"move {san} is not in the engine payload"

    allowed_squares = get_allowed_squares(flag)
    for sq in sorted(extract_squares(narration)):
        if sq not in allowed_squares:
            return f"square {sq} is not in the engine payload"

    if game is not None:
        bad = check_narration_moves_legality(narration, game)
        if bad:
            return f"{bad} is not a legal move at that point of the game"

    return None

def validate_narration(narration: str, flag: Dict[str, Any], game: Optional[chess.pgn.Game] = None) -> bool:
    reason = narration_violation(narration, flag, game)
    if reason:
        print(f"VALIDATION REJECTED: {reason}. Narration: {narration[:50]}...", file=sys.stderr)
    return reason is None
