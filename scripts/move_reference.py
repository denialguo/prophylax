# scripts/move_reference.py - deterministic parsing of "which move?" in a user message
import re
import chess
from typing import Optional, Tuple

SAN = r'(?:[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h](?:x[a-h])?[1-8](?:=[QRBN])?|O-O-O|O-O)[+#]?'
_NUM = r'(\d{1,3})(?:st|nd|rd|th)?'
_SIDE = r'(white|black)'

# "15.Bd3", "15. Bd3", "11...Bxc4", "11… b5" (SAN is case-sensitive)
_NUMBERED_SAN = re.compile(rf'\b(\d{{1,3}})\s*(\.\.\.|…|\.)\s*({SAN})')
# "move 15 white", "move 15 as black", "move 15, black", "15th move for white"
_NUM_THEN_SIDE = re.compile(rf'\b(?:move\s+{_NUM}|{_NUM}\s+move)\W+(?:(?:as|for|by|of)\s+)?{_SIDE}\b', re.I)
# "white's 15th move", "black move 12", "black's move 12", "white 15"
_SIDE_THEN_NUM = re.compile(rf"\b{_SIDE}(?:'s)?\s+(?:move\s+)?{_NUM}\b", re.I)

def parse_move_reference(text: str) -> Optional[Tuple[int, str, Optional[str]]]:
    """(move_number, 'white'|'black', typed SAN or None), or None when the message
    doesn't name one move unambiguously (the LLM router is the fallback then)."""
    m = _NUMBERED_SAN.search(text)
    if m:
        return int(m.group(1)), "white" if m.group(2) == "." else "black", m.group(3)
    m = _NUM_THEN_SIDE.search(text)
    if m:
        return int(m.group(1) or m.group(2)), m.group(3).lower(), None
    m = _SIDE_THEN_NUM.search(text)
    if m:
        return int(m.group(2)), m.group(1).lower(), None
    return None


def ply_of(start: "chess.Board", move_number: int, side: str) -> int:
    """Index into the mainline of the move numbered `move_number` for `side`, in a
    game starting at `start` (a FEN may start at any move, with either side to move).
    Negative or past-the-end values mean the move isn't in the game."""
    return ((move_number - start.fullmove_number) * 2
            + (side == "black") - (start.turn == chess.BLACK))
