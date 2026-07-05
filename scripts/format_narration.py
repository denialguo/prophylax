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
            
    prompt += "feature_deltas:\n"
    for rank, (feat, val) in enumerate(ranked_feats, 1):
        prompt += f"  {rank}. {feat}: {val:+.4f}\n"
        
    return prompt
