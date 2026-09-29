"""M14: the claim benchmark. Hand-labelled positions (evals/claim_benchmark/*.json)
measured against the deterministic layer, plus required-claim coverage of the M13
narrations. Offline: no engine, no LLM.

Three separate questions (kept apart on purpose):
1. deterministic  did the feature + claim code produce the labelled claims?
2. coverage       did a narration express the claims a case marks `required`?
3. grounding      was what it said supported? (the validator and judge; see M13)

Usage: ./venv/bin/python -m evals.claim_metrics [--compare artifacts/narration_compare.json]
"""
import argparse
import glob
import json
import os
import re

import chess
import chess.pgn

from domain.claims import ClaimConsistencyError, build_claims
from domain.convert import move_analysis_from_flag
from mcp_server.features import get_feature_deltas, get_quiet_concessions

BENCH_DIR = os.path.join(os.path.dirname(__file__), "claim_benchmark")
ROOT = os.path.dirname(os.path.dirname(__file__))
# The benchmark covers board-derived claim types only; engine claims are pinned by golden bands
TYPES = ("weak_square_created", "backward_pawn_created", "pawn_support_lost", "king_safety_reduced")
# Stricter than the gate's bare "king"; "pawn cover" is the claim sentence's own wording,
# so both arms' vocabulary counts
KING_WORDS = ("safety", "shield", "expos", "pawn cover")


def _msq(sq):
    return sq[0] + str(9 - int(sq[1]))


def _mirror_case(case):
    """The colour mirror of a board case, derived from its labels (not the builder):
    squares flipped, colours swapped, piece letters swapped."""
    other = {"white": "black", "black": "white"}
    claim = lambda c: {**c, "subject": _msq(c["subject"]) if c.get("subject") else c.get("subject")}

    def fact(f):
        v = f["value"]
        v = (sorted(_msq(x) for x in v) if isinstance(v, list) else v.swapcase() if isinstance(v, str) else v)
        return {**f, "square": _msq(f["square"]), "value": v, **({"color": other[f["color"]]} if "color" in f else {})}

    return {**case, "id": case["id"] + "_mirror", "mirror_of": case["id"], "eval_case": None,
            "source": {**case["source"], "mirror": True},
            "expected_claims": [claim(c) for c in case["expected_claims"]],
            "forbidden_claims": [claim(c) for c in case["forbidden_claims"]],
            "not_labelled": [claim(c) for c in case.get("not_labelled", [])],
            "evidence": {"board": [fact(f) for f in case["evidence"].get("board", [])]},
            "label_rationale": f"Colour mirror of {case['id']}."}


def load_cases(directory=BENCH_DIR) -> list[dict]:
    cases = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(path) as fh:
            cases += json.load(fh)
    return cases + [_mirror_case(c) for c in cases if c.get("mirror")]


def _eval_case(name):
    from tests.test_narration import load_eval_cases
    return next(c for c in load_eval_cases() if c["name"] == name)


def _replay(game, move_number, side, san):
    board = game.board()
    for m in game.mainline_moves():
        if board.fullmove_number == move_number and (board.turn == chess.WHITE) == (side == "white"):
            break
        board.push(m)
    move = board.parse_san(san)
    return board, move


def position(case):
    """(board before, move) for a case, or (None, None) for a payload-only eval case."""
    src = case["source"]
    if "fen" in src:
        board = chess.Board(src["fen"])
        move = board.parse_san(src["san"])
    elif "pgn_fixture" in src:
        with open(os.path.join(ROOT, "tests", "fixtures", src["pgn_fixture"] + ".pgn")) as fh:
            game = chess.pgn.read_game(fh)
    elif "pgn_file" in src:
        with open(os.path.join(ROOT, src["pgn_file"])) as fh:
            for _ in range(src["game_index"] + 1):
                game = chess.pgn.read_game(fh)
    else:
        return None, None
    if "fen" not in src:
        board, move = _replay(game, src["move_number"], src["side"], src["san"])
    if src.get("mirror"):
        board = board.mirror()
        move = chess.Move(chess.square_mirror(move.from_square), chess.square_mirror(move.to_square),
                          move.promotion)
    return board, move


def deterministic_claims(case) -> set:
    """(type, subject) the deterministic layer emits. A board case runs the server's own
    feature code, then build_claims with board evidence (which re-checks the payload);
    an eval-case source uses that case's payload."""
    board, move = position(case)
    if board is None:
        flag, fen = _eval_case(case["source"]["eval_case"])["input"]["flagged_move"], None
    else:
        after = board.copy()
        after.push(move)
        flag = {"move_san": board.san(move), "move_number": board.fullmove_number,
                "side": "white" if board.turn == chess.WHITE else "black", "phase": "middlegame",
                "wdl_delta": 0.0, "best_move_san": "", "pv": [], "refutation_pv": [], "channel": "wdl",
                "feature_deltas": get_feature_deltas(board, after, board.turn),
                "concessions": get_quiet_concessions(board, after, board.turn)}
        fen = board.fen()
    claims = build_claims(move_analysis_from_flag(flag, fen), game_id=case["id"])
    return {(c.type, c.subject) for c in claims if c.type in TYPES}


def _pawns(board, color):
    return board.pieces(chess.PAWN, chess.WHITE if color == "white" else chess.BLACK)


def _behind_adjacent(board, sq, color):
    f, r = chess.square_file(sq), chess.square_rank(sq)
    return sorted(chess.square_name(p) for p in _pawns(board, color)
                  if abs(chess.square_file(p) - f) == 1
                  and (chess.square_rank(p) < r if color == "white" else chess.square_rank(p) > r))


def evidence_mismatches(case) -> list[str]:
    """Tier 1: every stated fact is re-read from the board (or payload). A label must not
    rest on a wrong fact, whatever its status."""
    out = []
    board, move = position(case)
    boards = {}
    if board is not None:
        after = board.copy()
        after.push(move)
        boards = {"before": board, "after": after}
    for fact in case["evidence"].get("board", []):
        b, sq = boards[fact["board"]], chess.parse_square(fact["square"])
        kind = fact["fact"]
        if kind == "piece":
            p = b.piece_at(sq)
            got = p.symbol() if p else None
        elif kind == "pawn_attackers":
            got = sorted(chess.square_name(s) for s in b.attackers(
                chess.WHITE if fact["color"] == "white" else chess.BLACK, sq) & _pawns(b, fact["color"]))
        elif kind == "pawns_behind_adjacent":
            got = _behind_adjacent(b, sq, fact["color"])
        else:
            out.append(f"unknown fact {kind}")
            continue
        if got != fact["value"]:
            out.append(f"{fact['board']} {kind} {fact['square']}: label {fact['value']}, board {got}")
    for fact in case["evidence"].get("payload", []):
        flag = _eval_case(case["source"]["eval_case"])["input"]["flagged_move"]
        got = flag.get(fact["field"], {}).get(fact["name"])
        if got != fact["value"]:
            out.append(f"payload {fact['field']}.{fact['name']}: label {fact['value']}, payload {got}")
    return out


def _key(c):
    return (c["type"], c.get("subject"))


def compare(case) -> dict:
    """Label vs builder for one case: missing expected, emitted forbidden, extra claims."""
    try:
        got = deterministic_claims(case)
    except ClaimConsistencyError as e:
        return {"unsupported": str(e), "got": set(), "missing": set(), "forbidden_emitted": set()}
    expected = {_key(c) for c in case["expected_claims"]}
    forbidden = {_key(c) for c in case["forbidden_claims"]}
    return {"unsupported": None, "got": got, "missing": expected - got, "forbidden_emitted": forbidden & got}


def deterministic_metrics(cases) -> dict:
    """Per-type precision / recall over approved cases. An emitted claim that is neither
    expected nor forbidden is an unlabelled extra: it counts against precision."""
    rows = {t: {"tp": 0, "fp": 0, "fn": 0, "forbidden_emitted": 0} for t in TYPES}
    unsupported = 0
    for case in cases:
        r = compare(case)
        unsupported += r["unsupported"] is not None
        expected = {_key(c) for c in case["expected_claims"]}
        skip = {_key(c) for c in case.get("not_labelled", [])}
        for t, s in r["got"] - skip:
            rows[t]["tp" if (t, s) in expected else "fp"] += 1
        for t, s in r["missing"]:
            rows[t]["fn"] += 1
        for t, s in r["forbidden_emitted"]:
            rows[t]["forbidden_emitted"] += 1
    for row in rows.values():
        row["precision"] = row["tp"] / (row["tp"] + row["fp"]) if row["tp"] + row["fp"] else None
        row["recall"] = row["tp"] / (row["tp"] + row["fn"]) if row["tp"] + row["fn"] else None
    return {"cases": len(cases), "unsupported_claims": unsupported, "per_type": rows}


def expressed(claim: dict, narration: str) -> bool:
    """Did the narration state this claim? Square claims: the square is named.
    king_safety_reduced: king-safety vocabulary appears."""
    low = narration.lower()
    if claim["type"] == "king_safety_reduced":
        return any(w in low for w in KING_WORDS)
    return re.search(rf"\b{claim['subject']}\b", low) is not None


def coverage(cases, compare_artifact) -> dict:
    """Required-claim coverage of the M13 A/B narrations, per arm: of the (trial,
    required claim) pairs for cases linked to an eval case, how many were expressed."""
    with open(compare_artifact) as fh:
        trials = json.load(fh)["trials"]
    out = {}
    for arm in ("flag", "claims"):
        hit, total, misses = 0, 0, []
        for case in cases:
            required = [c for c in case["expected_claims"] if c.get("required")]
            if not case.get("eval_case") or not required:
                continue
            for t in trials:
                if t["case"] != case["eval_case"] or t["arm"] != arm:
                    continue
                for c in required:
                    total += 1
                    if expressed(c, t["final_narration"]):
                        hit += 1
                    else:
                        misses.append(f"{t['case']} run {t['run']}: {c['type']} {c.get('subject') or ''}".strip())
        out[arm] = {"expressed": hit, "required": total, "misses": misses}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--compare", default="artifacts/narration_compare.json")
    args = ap.parse_args(argv)
    cases = load_cases()
    by_status = {s: [c for c in cases if c["status"] == s] for s in ("approved", "draft", "ambiguous")}
    report = {"counts": {s: len(v) for s, v in by_status.items()},
              "deterministic (approved)": deterministic_metrics(by_status["approved"]),
              "deterministic (all labelled, for review only)": deterministic_metrics(
                  by_status["approved"] + by_status["draft"])}
    if os.path.exists(args.compare):
        report["required_claim_coverage (M13 narrations, all labelled)"] = coverage(
            by_status["approved"] + by_status["draft"], args.compare)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
