"""Coaching claims (M12): the statements the narrator may make about a move, each built
from one existing signal. No new chess rules live here: every claim maps to a field the
server already returns (see PHASE_B_PLAN.md, M12 table). Pure; no engine, no LLM."""
import chess

from domain.models import ClaimEvidence, CoachingClaim, MoveAnalysis
# Pure python-chess feature code (no engine): the same functions the server uses
from mcp_server.features import compute_king_safety, get_quiet_concessions

TYPE_ORDER = ("wdl_loss", "engine_best_move", "engine_refutation", "weak_square_created",
              "backward_pawn_created", "pawn_support_lost", "king_safety_reduced",
              "quiet_structural_concession")
NEGLIGIBLE = 0.01  # same cut as format_flag_for_llm's rank_and_prune_features


def _ev(provenance: str, fact: str, value) -> ClaimEvidence:
    return ClaimEvidence(provenance=provenance, fact=fact, value=value)


class ClaimConsistencyError(ValueError):
    """Recomputing from the board contradicts the payload; no claim is emitted."""


def _squares(bb) -> tuple[str, ...]:
    return tuple(sorted(chess.square_name(s) for s in chess.SquareSet(bb)))


def _supporters(board: chess.Board, sq: int, color: chess.Color) -> tuple[str, ...]:
    """Friendly pawns behind `sq` on an adjacent file: the pawns that could still
    advance to support a pawn there (pawn_support_impossible's rule, inverted)."""
    f, r = chess.square_file(sq), chess.square_rank(sq)
    return tuple(sorted(
        chess.square_name(p) for p in board.pieces(chess.PAWN, color)
        if abs(chess.square_file(p) - f) == 1
        and (chess.square_rank(p) < r if color == chess.WHITE else chess.square_rank(p) > r)))


def _board_facts(move: MoveAnalysis) -> dict:
    """(type, subject) -> board evidence, recomputed from fen_before. Raises
    ClaimConsistencyError when the board disagrees with the server's facts."""
    before = chess.Board(move.fen_before)
    played = before.parse_san(move.san)
    after = before.copy()
    after.push(played)
    color, enemy = before.turn, not before.turn
    d = move.detail

    recomputed = get_quiet_concessions(before, after, color)
    for key, sqs in d.concessions.model_dump().items():
        if set(sqs) != set(recomputed.get(key, [])):
            raise ClaimConsistencyError(f"{key}: payload {sorted(sqs)}, board {sorted(recomputed.get(key, []))}")
    side = "white" if color == chess.WHITE else "black"
    ks_before, ks_after = compute_king_safety(before)[side], compute_king_safety(after)[side]
    deltas = {f.name: f.value for f in d.feature_deltas}
    if "king_safety_delta" in deltas and abs((ks_after - ks_before) - deltas["king_safety_delta"]) > 1e-9:
        raise ClaimConsistencyError(f"king_safety_delta: payload {deltas['king_safety_delta']}, "
                                    f"board {ks_after - ks_before}")

    enemy_pawns = after.pieces(chess.PAWN, enemy)
    own_pawns = after.pieces(chess.PAWN, color)
    guarded = (chess.BB_PAWN_ATTACKS[color][played.from_square]
               if before.piece_type_at(played.from_square) == chess.PAWN else 0)
    facts = {}
    for name in d.concessions.new_weak_squares:
        sq = chess.parse_square(name)
        attackers = _squares(after.attackers(enemy, sq) & enemy_pawns)
        because = tuple(r for r, ok in (("was_guarded_by_moved_pawn", bool(chess.BB_SQUARES[sq] & guarded)),
                                        ("enemy_pawn_attacks", bool(attackers))) if ok)
        facts[("weak_square_created", name)] = [
            _ev("board", "color_complex", "light" if (chess.square_file(sq) + chess.square_rank(sq)) % 2 else "dark"),
            _ev("board", "conceded_because", because),
            _ev("board", "enemy_pawn_attackers", attackers)]
    for name in d.concessions.new_backward_pawns:
        sq = chess.parse_square(name)
        front = sq + (8 if color == chess.WHITE else -8)
        facts[("backward_pawn_created", name)] = [
            _ev("board", "front_square", chess.square_name(front)),
            _ev("board", "front_enemy_pawn_attackers", _squares(after.attackers(enemy, front) & enemy_pawns)),
            _ev("board", "front_friendly_pawn_attackers", _squares(after.attackers(color, front) & own_pawns))]
    for name in d.concessions.new_pawn_unsupported:
        sq = chess.parse_square(name)
        was = played.from_square if sq == played.to_square else sq  # the moved pawn: where it stood
        facts[("pawn_support_lost", name)] = [
            _ev("board", "possible_supporters_before", _supporters(before, was, color)),
            _ev("board", "enemy_pawn_attackers", _squares(after.attackers(enemy, sq) & enemy_pawns))]
    facts[("king_safety_reduced", None)] = [
        _ev("board", "king_square", chess.square_name(after.king(color))),
        _ev("board", "king_safety_before", ks_before), _ev("board", "king_safety_after", ks_after)]
    return facts


def build_claims(move: MoveAnalysis, game_id: str) -> list[CoachingClaim]:
    """Claims for one flagged move, ordered by TYPE_ORDER then square. With fen_before,
    structural claims also carry board evidence, checked against the payload first."""
    d = move.detail
    if d is None:
        raise ValueError(f"ply {move.ply} is not flagged; claims need its engine detail")
    board = _board_facts(move) if move.fen_before else {}
    raw = []  # (type, subject, evidence)

    ev = move.evaluation
    if ev.delta < 0:
        facts = [_ev("engine", "win_prob_delta", ev.delta)]
        if ev.before is not None and ev.after is not None:
            facts += [_ev("engine", "win_prob_before", ev.before), _ev("engine", "win_prob_after", ev.after)]
        facts += [_ev("server_features", "channel", d.channel), _ev("server_features", "phase", move.phase)]
        raw.append(("wdl_loss", None, facts))
    if move.best_move and move.best_move != move.san:
        raw.append(("engine_best_move", None, [_ev("engine", "best_move", move.best_move),
                                               _ev("engine", "pv", d.pv.moves)]))
    if d.refutation.moves:
        raw.append(("engine_refutation", None, [_ev("engine", "refutation_pv", d.refutation.moves)]))

    c = d.concessions
    for sq in c.new_weak_squares:
        raw.append(("weak_square_created", sq, [_ev("server_features", "new_weak_squares", sq)]))
    for sq in c.new_backward_pawns:
        raw.append(("backward_pawn_created", sq, [
            _ev("server_features", "new_backward_pawns", sq),
            _ev("server_features", "fixed", sq in c.new_fixed_backward_pawns)]))
    for sq in c.new_pawn_unsupported:
        if sq not in c.new_backward_pawns:  # backward already implies it (format_flag_for_llm rule)
            raw.append(("pawn_support_lost", sq, [_ev("server_features", "new_pawn_unsupported", sq)]))

    deltas = {f.name: f.value for f in d.feature_deltas}
    if deltas.get("king_safety_delta", 0) <= -NEGLIGIBLE:
        raw.append(("king_safety_reduced", None,
                    [_ev("server_features", "king_safety_delta", deltas["king_safety_delta"])]))
    if d.channel == "quiet_inaccuracy":
        raw.append(("quiet_structural_concession", None, [_ev("server_features", "channel", d.channel)]))

    raw = [(t, sq, facts + board.get((t, sq), [])) for t, sq, facts in raw]
    raw.sort(key=lambda r: (TYPE_ORDER.index(r[0]), r[1] or ""))
    ids = [f"{game_id}-ply{move.ply}-c{i}" for i in range(1, len(raw) + 1)]
    structural = [cid for cid, r in zip(ids, raw) if r[0] in ("weak_square_created", "backward_pawn_created")]
    return [CoachingClaim(claim_id=cid, type=t, ply=move.ply, move_number=move.move_number, side=move.side,
                          move_san=move.san, subject=subject, evidence=tuple(facts),
                          supports=tuple(structural) if t == "quiet_structural_concession" else ())
            for cid, (t, subject, facts) in zip(ids, raw)]
