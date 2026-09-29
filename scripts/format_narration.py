# scripts/format_narration.py
from typing import Dict, Any, List

def rank_and_prune_features(feature_deltas: Dict[str, float], depth: int) -> List[tuple]:
    """
    Sorts feature deltas by absolute magnitude descending, filters out zeros or extremely negligible deltas,
    and prunes the list to the specified explanation_depth.
    """
    if not feature_deltas:
        return []
    # Filter out negligible deltas (magnitude < 0.01)
    valid_features = [(k, v) for k, v in feature_deltas.items() if abs(v) >= 0.01]
    # Sort by absolute magnitude descending
    sorted_features = sorted(valid_features, key=lambda x: abs(x[1]), reverse=True)
    return sorted_features[:depth]

def render_numbered_pv(moves: List[str], start_move_number: int, start_side: str) -> str:
    """
    Renders a list of SAN moves with correct chess numbering.
    start_side is 'white' or 'black'.
    """
    if not moves:
        return ""
    
    parts = []
    curr_num = start_move_number
    curr_black = (start_side.lower() == "black")
    
    for m in moves:
        if curr_black:
            parts.append(f"{curr_num}...{m}")
            curr_num += 1
            curr_black = False
        else:
            parts.append(f"{curr_num}.{m}")
            curr_black = True
            
    return " ".join(parts)

def build_header(flag: Dict[str, Any]) -> str:
    move_san = flag.get("move_san", "")
    move_num = flag.get("move_number", 1)
    side = flag.get("side", "").capitalize()
    wdl_delta = flag.get("wdl_delta", 0.0)
    drop_pct = abs(wdl_delta) * 100
    if side == "White":
        return f"Move {move_num}.{move_san} (White): WDL drop {drop_pct:.1f}%"
    else:
        return f"Move {move_num}...{move_san} (Black): WDL drop {drop_pct:.1f}%"

def position_facts(features: Dict[str, Any]) -> Dict[str, List[str]]:
    """Squares named by analyze_position's static features, keyed by a prompt label.
    Only non-empty entries are kept."""
    facts = {}
    for side in ("white", "black"):
        structure = features.get("pawn_structure", {}).get(side, {})
        entries = {
            "weak squares": [w["square"] for w in features.get("weak_squares", {}).get(side, [])],
            "backward pawns": structure.get("backward_pawns", []),
            "isolated pawns": structure.get("isolated_pawns", []),
            "doubled pawns": structure.get("doubled_pawns", []),
        }
        for label, squares in entries.items():
            if squares:
                facts[f"{side.capitalize()} {label}"] = sorted(squares)
    return facts

def format_flag_for_llm(flag: Dict[str, Any], depth: int, rating: int) -> str:
    """
    Formulates a structured text prompt for the LLM based on the flagged move data,
    explanation depth, and audience rating.
    """
    move_num = flag.get("move_number", 1)
    side = flag.get("side", "").capitalize()
    if "phase" not in flag:
        raise KeyError("Required contract field 'phase' is missing from the flag payload.")
    phase = flag["phase"]
    wdl_delta = flag.get("wdl_delta", 0.0)
    best_move = flag.get("best_move_san", "")
    pv = flag.get("pv", [])
    refutation_pv = flag.get("refutation_pv", [])
    channel = flag.get("channel", "wdl")
    concessions = flag.get("concessions", {})
    
    header_str = build_header(flag)
        
    ranked_feats = rank_and_prune_features(flag.get("feature_deltas", {}), depth)
    
    prompt = f"header: {header_str}\n"
    prompt += f"Phase: {phase}\n"
    prompt += f"Channel: {channel}\n"
    prompt += f"WDL Delta: {wdl_delta:+.4f}\n"
    prompt += f"Audience Rating: {rating}\n"
    prompt += f"Explanation Depth: {depth}\n"
    
    # Pre-render PV and Refutation Lines
    rendered_pv = render_numbered_pv(pv, move_num, side)
    
    ref_start_num = move_num
    ref_start_side = "black" if side.lower() == "white" else "white"
    if side.lower() == "black":
        ref_start_num += 1
    rendered_refutation = render_numbered_pv(refutation_pv, ref_start_num, ref_start_side)
    
    prompt += f"Engine Best Move: {best_move}\n"
    prompt += f"Engine Best Line (PV): {rendered_pv}\n"
    prompt += f"Refutation Line: {rendered_refutation}\n"
    
    new_ws = concessions.get("new_weak_squares", [])
    new_bp = concessions.get("new_backward_pawns", [])
    if new_ws:
        prompt += f"New Weak Squares: {', '.join(new_ws)}\n"
    if new_bp:
        prompt += f"New Backward Pawns: {', '.join(new_bp)}\n"
    # A backward pawn already implies it; don't give the narrator both
    unsupported = [sq for sq in concessions.get("new_pawn_unsupported", []) if sq not in new_bp]
    if unsupported:
        prompt += f"Pawns That Lost All Possible Pawn Support: {', '.join(unsupported)}\n"
            
    alternatives = flag.get("alternatives", [])
    if alternatives:
        prompt += "Engine Alternatives From The Position Before The Move:\n"
        for i, line in enumerate(alternatives, 1):
            prompt += f"  {i}) {render_numbered_pv(line, move_num, side)}\n"
    for label, squares in flag.get("position_facts", {}).items():
        prompt += f"Before The Move, {label}: {', '.join(squares)}\n"

    prompt += "feature_deltas:\n"
    for rank, (feat, val) in enumerate(ranked_feats, 1):
        prompt += f"  {rank}. {feat}: {val:+.4f}\n"
        
    return prompt


def _move_label(number: int, side: str, san: str) -> str:
    return f"{number}.{san}" if side == "white" else f"{number}...{san}"


def _pawns_attack(owner: str, squares, already: str = "") -> str:
    """ "Black's pawn on d4 attacks it." / "Black's pawns on b4 and d4 attack it." """
    squares = list(squares)
    if len(squares) == 1:
        return f"{owner}'s pawn on {squares[0]} {already}attacks it."
    return f"{owner}'s pawns on {', '.join(squares[:-1])} and {squares[-1]} {already}attack it."


def claim_sentence(claim) -> str:
    """The canonical English statement of one CoachingClaim (domain/claims.py). This is
    the text of the narrator's VERIFIED CLAIMS block; every square and move in it comes
    from the claim itself."""
    facts = {e.fact: e.value for e in claim.evidence}
    side, other = ("White", "Black") if claim.side == "white" else ("Black", "White")
    move = _move_label(claim.move_number, claim.side, claim.move_san)
    t = claim.type
    if t == "wdl_loss":
        if "win_prob_before" in facts:
            return (f"{move} dropped {side}'s win probability from {round(facts['win_prob_before'] * 100)}% "
                    f"to {round(facts['win_prob_after'] * 100)}% in the engine's evaluation.")
        return f"{move} lowered {side}'s winning chances in the engine's evaluation."
    if t == "engine_best_move":
        line = render_numbered_pv(list(facts["pv"]), claim.move_number, claim.side)
        best = render_numbered_pv([facts["best_move"]], claim.move_number, claim.side)
        return f"The engine preferred {best}, with the line {line}."
    if t == "engine_refutation":
        start = claim.move_number + (claim.side == "black")
        line = render_numbered_pv(list(facts["refutation_pv"]), start, "black" if claim.side == "white" else "white")
        return f"The engine's refutation of {move} is {line}."
    if t == "weak_square_created":
        text = f"{move} created a weak square on {claim.subject}: no {side} pawn can ever guard it again."
        if facts.get("enemy_pawn_attackers"):
            text += " " + _pawns_attack(other, facts["enemy_pawn_attackers"], "already ")
        return text
    if t == "backward_pawn_created":
        front = facts.get("front_square", "the square in front of it")
        front = f"{front}, the square in front of it," if "front_square" in facts else front
        return (f"{move} left {side}'s {claim.subject} pawn backward: no {side} pawn stands behind it on an "
                f"adjacent file, and {front} is covered by more {other} pawns than {side} pawns.")
    if t == "pawn_support_lost":
        text = f"After {move}, no {side} pawn can ever support {side}'s {claim.subject} pawn."
        if facts.get("enemy_pawn_attackers"):
            text += " " + _pawns_attack(other, facts["enemy_pawn_attackers"])
        return text
    if t == "king_safety_reduced":
        return f"{move} thinned the pawn cover around {side}'s king."
    if t == "quiet_structural_concession":
        return (f"The engine barely registers {move}, but it is a lasting structural concession: "
                f"it creates both a weak square and a backward pawn.")
    raise ValueError(f"no sentence for claim type {t!r}")
