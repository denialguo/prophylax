"""Helpers for labelling. Reads only this folder.

  python label_tools.py span <case> "exact quote"   span_start and span_end for a quote
  python label_tools.py board <case> [uci ...]      the board after those moves from the root"""
import json
import os
import sys

import chess

HERE = os.path.dirname(os.path.abspath(__file__))


def case_dir(name):
    return os.path.join(HERE, "cases", name)


def span(name, quote):
    with open(os.path.join(case_dir(name), "response.txt"), encoding="utf-8", newline="") as f:
        text = f.read()
    starts, at = [], text.find(quote)
    while at >= 0:
        starts.append(at)
        at = text.find(quote, at + 1)
    if not starts:
        return "not found: the quote must match the text exactly"
    lines = [json.dumps({"span_start": s, "span_end": s + len(quote), "source_text": quote}, ensure_ascii=False) for s in starts]
    if len(starts) > 1:
        lines.insert(0, f"{len(starts)} matches: pick the one you mean")
    return "\n".join(lines)


def board(name, moves):
    with open(os.path.join(case_dir(name), "case.json"), encoding="utf-8") as f:
        b = chess.Board(json.load(f)["fen"])
    for n, uci in enumerate(moves):
        try:
            b.push(b.parse_uci(uci))
        except ValueError as e:
            return f"move {n} ({uci}) is illegal here: {e}\n{b}\n{b.fen()}"
        if n < len(moves) - 1 and b.is_game_over():
            return f"the game is over after move {n} ({uci}): {b.outcome().termination.name}\n{b}\n{b.fen()}"
    status = b.outcome().termination.name if b.is_game_over() else ("in check" if b.is_check() else "")
    return f"{b}\n{b.fen()}\n{'White' if b.turn else 'Black'} to move {status}".rstrip()


def main(argv):
    if len(argv) >= 3 and argv[0] == "span":
        print(span(argv[1], argv[2]))
    elif len(argv) >= 2 and argv[0] == "board":
        print(board(argv[1], argv[2:]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
