"""The boundary between the MCP payload (loose dicts) and the domain models (M11).
Anything inconsistent fails loudly with PayloadError; nothing is guessed or repaired."""
import hashlib
import io
from typing import Any

import chess
import chess.pgn
from pydantic import ValidationError

from domain.models import (Concessions, EngineEvaluation, EngineLine, FeatureDelta, FlagDetail,
                           GameAnalysis, MoveAnalysis, PositionAnalysis, PositionFeatures, Summary, WDL)
from scripts.move_reference import ply_of

MOVE_EVAL_KEYS = {"move_san", "move_number", "side", "phase", "wdl_delta", "best_move_san"}
FLAG_KEYS = MOVE_EVAL_KEYS | {"pv", "refutation_pv", "feature_deltas", "concessions", "channel"}
FLAG_OPTIONAL = {"wdl_before_prob", "wdl_after_prob"}


def content_id(start_fen: str, ucis: str) -> str:
    """GameAnalysis.game_id: the game's content (start position + moves), so claim ids are
    stable across re-analysis. Not unique per played game: identical move sequences collide."""
    return hashlib.sha1(f"{start_fen}|{ucis}".encode()).hexdigest()[:12]


class PayloadError(ValueError):
    """The server payload doesn't match the game or the contract."""


def _keys(obj: Any, required: set, optional: set, what: str) -> None:
    if not isinstance(obj, dict):
        raise PayloadError(f"{what} is not an object")
    missing, extra = required - obj.keys(), obj.keys() - required - optional
    if missing or extra:
        raise PayloadError(f"{what}: missing {sorted(missing)}, unexpected {sorted(extra)}")


def _replay(movetext: str) -> tuple[chess.Board, list[tuple[chess.Board, chess.Move]]]:
    game = chess.pgn.read_game(io.StringIO(movetext))
    if game is None or game.errors:
        raise PayloadError("movetext does not parse as a legal game")
    board = game.board()
    start, plies = board.copy(), []
    for move in game.mainline_moves():
        plies.append((board.copy(), move))
        board.push(move)
    return start, plies


def game_analysis_from_payload(payload: dict, movetext: str) -> GameAnalysis:
    """analyze_pgn result + the movetext it was computed from -> GameAnalysis."""
    try:
        return _game_analysis(payload, movetext)
    except ValidationError as e:
        raise PayloadError(str(e)) from e


def _game_analysis(payload: dict, movetext: str) -> GameAnalysis:
    _keys(payload, {"flags", "move_evals", "summary"}, {"analysis_config", "searches"}, "analyze_pgn result")
    start, plies = _replay(movetext)
    evals = payload["move_evals"]
    if len(evals) != len(plies):
        raise PayloadError(f"{len(evals)} move_evals for a game of {len(plies)} plies")

    moves = []
    for ply, (entry, (board, move)) in enumerate(zip(evals, plies)):
        _keys(entry, MOVE_EVAL_KEYS, set(), f"move_evals[{ply}]")
        side = "white" if board.turn == chess.WHITE else "black"
        actual = (board.fullmove_number, side, board.san(move))
        if (entry["move_number"], entry["side"], entry["move_san"]) != actual:
            raise PayloadError(f"move_evals[{ply}] names {entry['move_number']} {entry['side']} "
                               f"{entry['move_san']}, the game has {actual}")
        moves.append(dict(ply=ply, move_number=actual[0], side=side, san=actual[2],
                          phase=entry["phase"], fen_before=board.fen(),
                          best_move=entry["best_move_san"],
                          evaluation=EngineEvaluation(delta=entry["wdl_delta"])))

    for rank, flag in enumerate(payload["flags"]):
        _keys(flag, FLAG_KEYS, FLAG_OPTIONAL, f"flags[{rank}]")
        ply = ply_of(start, flag["move_number"], flag["side"])
        if not 0 <= ply < len(moves):
            raise PayloadError(f"flags[{rank}] ({flag['move_number']} {flag['side']}) is not in the game")
        m = moves[ply]
        if "detail" in m:
            raise PayloadError(f"flags[{rank}] duplicates the flag at ply {ply}")
        for key, field in (("move_san", "san"), ("phase", "phase"), ("best_move_san", "best_move")):
            if flag[key] != m[field]:
                raise PayloadError(f"flags[{rank}].{key} is {flag[key]!r}, move_evals says {m[field]!r}")
        if flag["wdl_delta"] != m["evaluation"].delta:
            raise PayloadError(f"flags[{rank}].wdl_delta disagrees with move_evals")
        m["evaluation"] = EngineEvaluation(delta=flag["wdl_delta"], before=flag.get("wdl_before_prob"),
                                           after=flag.get("wdl_after_prob"))
        if not isinstance(flag["feature_deltas"], dict) or not isinstance(flag["concessions"], dict):
            raise PayloadError(f"flags[{rank}]: feature_deltas and concessions must be objects")
        m["detail"] = FlagDetail(
            channel=flag["channel"], rank=rank,
            pv=EngineLine(moves=flag["pv"], start_ply=ply),
            refutation=EngineLine(moves=flag["refutation_pv"], start_ply=ply + 1),
            feature_deltas=tuple(FeatureDelta(name=k, value=v) for k, v in flag["feature_deltas"].items()),
            concessions=Concessions(**flag["concessions"]),
        )

    ucis = " ".join(move.uci() for _, move in plies)
    return GameAnalysis(
        game_id=content_id(start.fen(), ucis),
        start_fen=start.fen(),
        moves=tuple(MoveAnalysis(**m) for m in moves),
        summary=Summary(**payload["summary"]),
    )


def to_flag_dict(move: MoveAnalysis) -> dict:
    """The server's flag dict for a flagged move, exactly as analyze_pgn emitted it
    (the prompt and validator code still read this form)."""
    d = move.detail
    if d is None:
        raise ValueError(f"ply {move.ply} is not flagged")
    out = {"move_san": move.san, "move_number": move.move_number, "side": move.side, "phase": move.phase}
    if move.evaluation.before is not None:
        out["wdl_before_prob"] = move.evaluation.before
    if move.evaluation.after is not None:
        out["wdl_after_prob"] = move.evaluation.after
    out.update({
        "wdl_delta": move.evaluation.delta,
        "best_move_san": move.best_move,
        "pv": list(d.pv.moves),
        "refutation_pv": list(d.refutation.moves),
        "feature_deltas": {f.name: f.value for f in d.feature_deltas},
        "concessions": {k: list(v) for k, v in d.concessions.model_dump().items() if v},
        "channel": d.channel,
    })
    return out


def position_analysis_from_payload(payload: dict, fen: str) -> PositionAnalysis:
    """analyze_position result -> PositionAnalysis. Line start_ply is relative to `fen`."""
    try:
        _keys(payload, {"multipv_lines", "features"}, set(), "analyze_position result")
        lines = []
        for i, line in enumerate(payload["multipv_lines"]):
            _keys(line, {"pv", "wdl"}, set(), f"multipv_lines[{i}]")
            lines.append((EngineLine(moves=line["pv"], start_ply=0),
                          WDL(**line["wdl"]) if line["wdl"] is not None else None))
        return PositionAnalysis(fen=chess.Board(fen).fen(), lines=tuple(lines),
                                features=PositionFeatures(**payload["features"]))
    except ValidationError as e:
        raise PayloadError(str(e)) from e


def move_analysis_from_flag(flag: dict, fen_before: str | None = None) -> MoveAnalysis:
    """A single flag with no surrounding game (the narration eval cases). Plies are
    numbered as from the standard start. With `fen_before`, the move must be legal there
    and the side to move must match; without it there is no board evidence."""
    _keys(flag, FLAG_KEYS - {"concessions"}, FLAG_OPTIONAL | {"concessions", "synthetic"}, "flag")
    side = flag["side"]
    if fen_before is not None:
        board = chess.Board(fen_before)
        if (board.turn == chess.WHITE) != (side == "white") or board.fullmove_number != flag["move_number"]:
            raise PayloadError(f"flag is {flag['move_number']} {side}, the board is {board.fullmove_number} "
                               f"{'white' if board.turn else 'black'} to move")
        try:
            if board.san(board.parse_san(flag["move_san"])) != flag["move_san"]:
                raise ValueError
        except ValueError:
            raise PayloadError(f"{flag['move_san']} is not a legal move in {fen_before}") from None
    ply = (flag["move_number"] - 1) * 2 + (side == "black")
    try:
        return MoveAnalysis(
            ply=ply, move_number=flag["move_number"], side=side, san=flag["move_san"], phase=flag["phase"],
            fen_before=fen_before, best_move=flag["best_move_san"],
            evaluation=EngineEvaluation(delta=flag["wdl_delta"], before=flag.get("wdl_before_prob"),
                                        after=flag.get("wdl_after_prob")),
            detail=FlagDetail(
                channel=flag["channel"], rank=0,
                pv=EngineLine(moves=flag["pv"], start_ply=ply),
                refutation=EngineLine(moves=flag["refutation_pv"], start_ply=ply + 1),
                feature_deltas=tuple(FeatureDelta(name=k, value=v) for k, v in flag["feature_deltas"].items()),
                concessions=Concessions(**flag.get("concessions", {})),
            ))
    except (ValidationError, AttributeError, TypeError) as e:
        raise PayloadError(str(e)) from e
