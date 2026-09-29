"""M8: deterministic move-reference parser (the LLM router is only a fallback)."""
import pytest

from scripts.move_reference import parse_move_reference


@pytest.mark.parametrize("text,expected", [
    ("15.Bd3", (15, "white", "Bd3")),
    ("what about 15. Bd3?", (15, "white", "Bd3")),
    ("11...Bxc4", (11, "black", "Bxc4")),
    ("why was 11... Bxc4 bad", (11, "black", "Bxc4")),
    ("11…b5", (11, "black", "b5")),
    ("explain 13.b4", (13, "white", "b4")),
    ("20...Qh4#", (20, "black", "Qh4#")),
    ("9.O-O", (9, "white", "O-O")),
    ("was 30...exd4 a mistake", (30, "black", "exd4")),
    ("move 15 white", (15, "white", None)),
    ("ask about move 13 white", (13, "white", None)),
    ("What about move 2 black?", (2, "black", None)),
    ("Move 7, Black", (7, "black", None)),
    ("move 22 as white", (22, "white", None)),
    ("the 15th move for black", (15, "black", None)),
    ("white's 15th move", (15, "white", None)),
    ("Black's move 12", (12, "black", None)),
    ("black move 3", (3, "black", None)),
    ("tell me about White 18", (18, "white", None)),
    ("what did I do wrong?", None),
    ("What about move 15?", None),        # side unknown -> LLM fallback
    ("my knight move", None),
    ("I was 1800 last year", None),
])
def test_parse_move_reference(text, expected):
    assert parse_move_reference(text) == expected
