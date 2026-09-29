"""Coaching claims (M12): the statements the narrator may make about a move, each built
from one existing signal. No new chess rules live here: every claim maps to a field the
server already returns (see PHASE_B_PLAN.md, M12 table). Pure; no engine, no LLM."""
from domain.models import ClaimEvidence, CoachingClaim, MoveAnalysis

TYPE_ORDER = ("wdl_loss", "engine_best_move", "engine_refutation", "weak_square_created",
              "backward_pawn_created", "pawn_support_lost", "king_safety_reduced",
              "quiet_structural_concession")
NEGLIGIBLE = 0.01  # same cut as format_flag_for_llm's rank_and_prune_features


def _ev(provenance: str, fact: str, value) -> ClaimEvidence:
    return ClaimEvidence(provenance=provenance, fact=fact, value=value)


def build_claims(move: MoveAnalysis, game_id: str) -> list[CoachingClaim]:
    """Claims for one flagged move, ordered by TYPE_ORDER then square."""
    d = move.detail
    if d is None:
        raise ValueError(f"ply {move.ply} is not flagged; claims need its engine detail")
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

    raw.sort(key=lambda r: (TYPE_ORDER.index(r[0]), r[1] or ""))
    ids = [f"{game_id}-ply{move.ply}-c{i}" for i in range(1, len(raw) + 1)]
    structural = [cid for cid, r in zip(ids, raw) if r[0] in ("weak_square_created", "backward_pawn_created")]
    return [CoachingClaim(claim_id=cid, type=t, ply=move.ply, move_number=move.move_number, side=move.side,
                          move_san=move.san, subject=subject, evidence=tuple(facts),
                          supports=tuple(structural) if t == "quiet_structural_concession" else ())
            for cid, (t, subject, facts) in zip(ids, raw)]
