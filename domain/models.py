"""Canonical analysis models (M11). The MCP payload stays the wire format; these are
what the application reasons over. Layers are kept apart on purpose:

  raw observation     EngineEvaluation, EngineLine, PositionFeatures (engine / server)
  deterministic fact  Concessions, FeatureDelta (server-computed, python-chess)
  coaching claim      CoachingClaim + ClaimEvidence (M12, built by domain/claims.py)
  narration           a plain str, never stored on these models
"""
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StringConstraints

Side = Literal["white", "black"]
Phase = Literal["opening", "middlegame", "endgame"]
Square = Annotated[str, StringConstraints(pattern=r"^[a-h][1-8]$")]
# No lax coercion of numbers: "0.5" or True in a payload is an error, not a value
Num = StrictInt | StrictFloat


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WDL(_Model):
    wins: StrictInt
    draws: StrictInt
    losses: StrictInt


class EngineEvaluation(_Model):
    """Win probability for the mover. Before/after are only carried for flagged moves."""
    delta: Num = Field(ge=-1, le=1)
    before: Optional[Num] = Field(default=None, ge=0, le=1)
    after: Optional[Num] = Field(default=None, ge=0, le=1)


class EngineLine(_Model):
    """SAN moves starting at `start_ply` (the index of the first move in the game)."""
    moves: tuple[str, ...]
    start_ply: StrictInt


class FeatureDelta(_Model):
    name: str
    value: Num


class Concessions(_Model):
    """Squares the server reported for the mover, in the server's order."""
    new_weak_squares: tuple[Square, ...] = ()
    new_backward_pawns: tuple[Square, ...] = ()
    new_fixed_backward_pawns: tuple[Square, ...] = ()
    new_pawn_unsupported: tuple[Square, ...] = ()


class FlagDetail(_Model):
    channel: Literal["wdl", "quiet_inaccuracy"]
    rank: StrictInt  # position in the server's ranked flag list
    pv: EngineLine
    refutation: EngineLine
    feature_deltas: tuple[FeatureDelta, ...]  # server order (ranked by magnitude)
    concessions: Concessions


class MoveAnalysis(_Model):
    ply: StrictInt
    move_number: StrictInt = Field(ge=1)
    side: Side
    san: str
    phase: Phase
    fen_before: Optional[str]  # None only for a standalone flag with no game (synthetic evals)
    best_move: str
    evaluation: EngineEvaluation
    detail: Optional[FlagDetail] = None  # set only for flagged moves


class Summary(_Model):
    total_flags: StrictInt = Field(ge=0)
    phase_distribution: dict[Phase, StrictInt] = {}
    final_wdl: Optional[WDL] = None


class GameAnalysis(_Model):
    game_id: str
    start_fen: str
    moves: tuple[MoveAnalysis, ...]
    summary: Summary

    def flagged(self) -> list[MoveAnalysis]:
        """Flagged moves in the server's ranked order."""
        return sorted((m for m in self.moves if m.detail), key=lambda m: m.detail.rank)


class WeakSquare(_Model):
    square: Square
    complex: Literal["light", "dark"]


class SidePawnStructure(_Model):
    model_config = ConfigDict(frozen=True, extra="allow")  # score etc. pass through
    backward_pawns: tuple[Square, ...] = ()
    isolated_pawns: tuple[Square, ...] = ()
    doubled_pawns: tuple[Square, ...] = ()
    fixed_pawns: tuple[Square, ...] = ()


class PositionFeatures(_Model):
    weak_squares: dict[Side, tuple[WeakSquare, ...]]
    pawn_structure: dict[Side, SidePawnStructure]
    king_safety: dict[Side, Num] = {}
    piece_activity: dict[Side, StrictInt] = {}


class PositionAnalysis(_Model):
    fen: str
    lines: tuple[tuple[EngineLine, Optional[WDL]], ...]
    features: PositionFeatures


ClaimType = Literal[
    "wdl_loss", "engine_best_move", "engine_refutation", "weak_square_created",
    "backward_pawn_created", "pawn_support_lost", "king_safety_reduced",
    "quiet_structural_concession",
]


class ClaimEvidence(_Model):
    """One fact a claim rests on, with where it came from:
    engine           Stockfish numbers and lines, as returned by the MCP server
    server_features  the server's deterministic outputs (concessions, feature deltas, phase, channel)
    board            recomputed here from fen_before with python-chess; no search, no LLM"""
    provenance: Literal["engine", "server_features", "board"]
    fact: str
    value: bool | StrictInt | StrictFloat | str | tuple[str, ...]


class CoachingClaim(_Model):
    """One statement the narrator may make about a move."""
    claim_id: str
    type: ClaimType
    ply: StrictInt
    move_number: StrictInt
    side: Side
    move_san: str
    subject: Optional[Square] = None  # the square the claim is about, if any
    evidence: tuple[ClaimEvidence, ...]
    supports: tuple[str, ...] = ()  # claim_ids a composite claim is built from
