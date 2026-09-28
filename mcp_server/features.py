# features.py - Deterministic static chess features
import chess
from typing import Dict, Any, Set, Tuple

def evaluate_position_features(board: chess.Board) -> Dict[str, Any]:
    """
    Computes absolute static signals for a given board position:
      - king_safety
      - pawn_structure
      - weak_squares
      - piece_activity
    """
    # 1. King Safety
    king_safety = compute_king_safety(board)

    # 2. Pawn Structure
    pawn_structure = compute_pawn_structure(board)

    # 3. Weak Squares
    weak_squares = compute_weak_squares(board)

    # 4. Piece Activity
    piece_activity = compute_piece_activity(board)

    return {
        "king_safety": king_safety,
        "pawn_structure": pawn_structure,
        "weak_squares": weak_squares,
        "piece_activity": piece_activity
    }

def get_feature_deltas(board_before: chess.Board, board_after: chess.Board, mover_color: chess.Color) -> Dict[str, Dict[str, Any]]:
    """
    Computes feature deltas across a move from the perspective of the mover.
    Returns delta objects ranked by magnitude descending.
    """
    feat_before = evaluate_position_features(board_before)
    feat_after = evaluate_position_features(board_after)

    # We compute deltas as: (after - before) from the perspective of the mover.
    # Positive means improvements for mover, negative means concessions/damage.
    # For relative features, we map them accordingly.
    
    # King safety delta: mover's safety change minus opponent's safety change
    # (or absolute change in mover's king safety)
    mover_ks_before = feat_before["king_safety"]["white" if mover_color == chess.WHITE else "black"]
    mover_ks_after = feat_after["king_safety"]["white" if mover_color == chess.WHITE else "black"]
    ks_delta = mover_ks_after - mover_ks_before

    # Pawn structure delta
    mover_ps_before = feat_before["pawn_structure"]["white" if mover_color == chess.WHITE else "black"]["score"]
    mover_ps_after = feat_after["pawn_structure"]["white" if mover_color == chess.WHITE else "black"]["score"]
    ps_delta = mover_ps_after - mover_ps_before

    # Weak squares delta: change in the count of weak squares in own camp (fewer is better, so delta is before - after)
    mover_ws_before = len(feat_before["weak_squares"]["white" if mover_color == chess.WHITE else "black"])
    mover_ws_after = len(feat_after["weak_squares"]["white" if mover_color == chess.WHITE else "black"])
    ws_delta = mover_ws_before - mover_ws_after

    # Piece activity delta: mover's mobility change
    mover_act_before = feat_before["piece_activity"]["white" if mover_color == chess.WHITE else "black"]
    mover_act_after = feat_after["piece_activity"]["white" if mover_color == chess.WHITE else "black"]
    act_delta = mover_act_after - mover_act_before

    deltas = {
        "king_safety_delta": {"val": ks_delta, "mag": abs(ks_delta)},
        "pawn_structure_delta": {"val": ps_delta, "mag": abs(ps_delta)},
        "weak_squares": {"val": ws_delta, "mag": abs(ws_delta)},
        "piece_activity_delta": {"val": act_delta, "mag": abs(act_delta)}
    }

    # Sort keys by absolute magnitude descending
    sorted_keys = sorted(deltas.keys(), key=lambda k: deltas[k]["mag"], reverse=True)
    return {k: deltas[k]["val"] for k in sorted_keys}

def get_quiet_concessions(board_before: chess.Board, board_after: chess.Board, mover_color: chess.Color) -> Dict[str, Any]:
    """
    Checks if a move resulted in:
      - A new weak square in own camp (ranks 3-4 for White, 5-6 for Black).
      - A new backward pawn in own camp.
    Returns a dict with concession details.
    """
    feat_before = evaluate_position_features(board_before)
    feat_after = evaluate_position_features(board_after)

    side_key = "white" if mover_color == chess.WHITE else "black"

    ws_before = {sq for sq, _ in feat_before["weak_squares"][side_key]}
    ws_after = {sq for sq, _ in feat_after["weak_squares"][side_key]}
    new_ws = ws_after - ws_before

    # Ensure squares lie in correct camp ranks (ranks 3-4 for White, 5-6 for Black)
    if mover_color == chess.WHITE:
        new_ws = {sq for sq in new_ws if int(sq[1]) in [3, 4]}
    else:
        new_ws = {sq for sq in new_ws if int(sq[1]) in [5, 6]}

    # A pawn move concedes the squares it stopped guarding directly, plus any
    # farther new hole an enemy pawn already supports (an outpost). Holes that are
    # neither (e.g. a4 after b2-b4 with no Black pawn on it) are noise.
    move = board_after.peek()
    directly_guarded = (
        chess.BB_PAWN_ATTACKS[mover_color][move.from_square]
        if board_before.piece_type_at(move.from_square) == chess.PAWN else 0
    )
    enemy_pawns = board_after.pieces(chess.PAWN, not mover_color)
    new_ws = {
        sq for sq in new_ws
        if chess.BB_SQUARES[chess.parse_square(sq)] & directly_guarded
        or board_after.attackers(not mover_color, chess.parse_square(sq)) & enemy_pawns
    }

    bp_before = set(feat_before["pawn_structure"][side_key]["backward_pawns"])
    bp_after = set(feat_after["pawn_structure"][side_key]["backward_pawns"])
    new_bp = bp_after - bp_before

    concessions = {}
    if new_ws:
        concessions["new_weak_squares"] = list(new_ws)
    if new_bp:
        concessions["new_backward_pawns"] = list(new_bp)

        # Check if the new backward pawns are fixed
        fixed_pawns = feat_after["pawn_structure"][side_key]["fixed_pawns"]
        new_fixed_bp = [p for p in new_bp if p in fixed_pawns]
        if new_fixed_bp:
            concessions["new_fixed_backward_pawns"] = new_fixed_bp

    return concessions

# --- KING SAFETY HELPER FUNCTIONS ---
def compute_king_safety(board: chess.Board) -> Dict[str, float]:
    """
    Evaluates king safety for White and Black:
      - Pawn shield integrity (pawns on ranks 2 and 3 in front of the king).
      - Penalties for open/semi-open files on the king's file and adjacent files.
    """
    res = {}
    for color in [chess.WHITE, chess.BLACK]:
        king_sq = board.king(color)
        if king_sq is None:
            res["white" if color == chess.WHITE else "black"] = 0.0
            continue

        king_file = chess.square_file(king_sq)
        # Do not expand king-safety to uncastled / center kings
        if king_file in [3, 4]:
            res["white" if color == chess.WHITE else "black"] = 0.0
            continue

        shield_score = 0.0
        # Check files around the king
        adjacent_files = [f for f in [king_file - 1, king_file, king_file + 1] if 0 <= f <= 7]

        # Pawn shield evaluation: statically anchored to the home ranks
        shield_rank = 1 if color == chess.WHITE else 6
        extended_shield_rank = 2 if color == chess.WHITE else 5

        for f in adjacent_files:
            # Check shield_rank
            sq_shield = chess.square(f, shield_rank) if 0 <= shield_rank <= 7 else None
            sq_ext = chess.square(f, extended_shield_rank) if 0 <= extended_shield_rank <= 7 else None

            shield_pawn = False
            if sq_shield is not None:
                p = board.piece_at(sq_shield)
                if p and p.piece_type == chess.PAWN and p.color == color:
                    shield_score += 1.0
                    shield_pawn = True

            if not shield_pawn and sq_ext is not None:
                p = board.piece_at(sq_ext)
                if p and p.piece_type == chess.PAWN and p.color == color:
                    shield_score += 0.5

        # Open files penalties (files around the king with no friendly pawns, or no pawns at all)
        open_file_penalty = 0.0
        for f in adjacent_files:
            friendly_pawn = False
            enemy_pawn = False
            for r in range(8):
                sq = chess.square(f, r)
                p = board.piece_at(sq)
                if p and p.piece_type == chess.PAWN:
                    if p.color == color:
                        friendly_pawn = True
                    else:
                        enemy_pawn = True
            
            if not friendly_pawn:
                if not enemy_pawn:
                    # Fully open file
                    open_file_penalty += 1.0
                else:
                    # Semi-open file towards our king
                    open_file_penalty += 0.5

        res["white" if color == chess.WHITE else "black"] = shield_score - open_file_penalty
    return res

# --- PAWN STRUCTURE HELPER FUNCTIONS ---
def compute_pawn_structure(board: chess.Board) -> Dict[str, Any]:
    """
    Detects backward, fixed, isolated, and doubled pawns.
    Returns detailed metrics and a structure score.
    """
    res = {}
    for color in [chess.WHITE, chess.BLACK]:
        backward_pawns = []
        fixed_pawns = []
        isolated_pawns = []
        doubled_pawns = []

        pawns_mask = board.pieces(chess.PAWN, color)
        enemy_pawns_mask = board.pieces(chess.PAWN, not color)

        for sq in pawns_mask:
            file_idx = chess.square_file(sq)
            rank_idx = chess.square_rank(sq)

            # Doubled pawns: other friendly pawns on the same file
            same_file_pawns = [s for s in pawns_mask if chess.square_file(s) == file_idx and s != sq]
            if same_file_pawns:
                doubled_pawns.append(sq)

            # Isolated pawns: no friendly pawns on adjacent files
            adj_files = [f for f in [file_idx - 1, file_idx + 1] if 0 <= f <= 7]
            has_adj_pawn = False
            for f in adj_files:
                for r in range(8):
                    if chess.square(f, r) in pawns_mask:
                        has_adj_pawn = True
                        break
                if has_adj_pawn:
                    break
            if not has_adj_pawn:
                isolated_pawns.append(sq)

            # Backward pawn check:
            # 1. No friendly pawns behind it on adjacent files.
            # 2. Cannot safely advance one square because it is controlled or blocked.
            # Specifically, if any adjacent file has friendly pawns, are they all further advanced?
            behind_adj_pawn = False
            for f in adj_files:
                for r in range(8):
                    other_sq = chess.square(f, r)
                    if other_sq in pawns_mask:
                        # Behind means rank is lower for White, higher for Black
                        if color == chess.WHITE and r < rank_idx:
                            behind_adj_pawn = True
                        elif color == chess.BLACK and r > rank_idx:
                            behind_adj_pawn = True
            
            # If there is no friendly pawn behind this one on adjacent files
            if not behind_adj_pawn and len(adj_files) > 0:
                # Can it advance? Check the square ahead
                ahead_rank = rank_idx + (1 if color == chess.WHITE else -1)
                if 0 <= ahead_rank <= 7:
                    ahead_sq = chess.square(file_idx, ahead_rank)
                    
                    # Check if the advance square or the pawn itself is defended/attacked
                    # It is backward if it is defended by fewer pawns than attackers, or if the ahead square is blocked/controlled.
                    # Simplified: if the ahead square is controlled by an enemy pawn and not defendable by a friendly pawn.
                    enemy_pawn_attacks = board.attackers(not color, ahead_sq) & enemy_pawns_mask
                    friendly_pawn_attacks = board.attackers(color, ahead_sq) & pawns_mask
                    
                    if enemy_pawn_attacks and (len(friendly_pawn_attacks) < len(enemy_pawn_attacks)):
                        backward_pawns.append(sq)
                        
                        # Is it fixed? A backward pawn is fixed if it is blocked or cannot advance
                        # e.g., if there is a piece directly blocking it in front
                        block_sq = chess.square(file_idx, ahead_rank)
                        if board.piece_at(block_sq) is not None:
                            fixed_pawns.append(sq)

        # Structure score calculation: start from 0, penalize weaknesses
        score = 0.0
        score -= len(isolated_pawns) * 0.5
        score -= len(doubled_pawns) * 0.3
        score -= len(backward_pawns) * 0.5
        score -= len(fixed_pawns) * 0.5

        # Format square names for readability
        res["white" if color == chess.WHITE else "black"] = {
            "score": score,
            "backward_pawns": [chess.square_name(s) for s in backward_pawns],
            "fixed_pawns": [chess.square_name(s) for s in fixed_pawns],
            "isolated_pawns": [chess.square_name(s) for s in isolated_pawns],
            "doubled_pawns": [chess.square_name(s) for s in doubled_pawns]
        }
    return res

# --- WEAK SQUARES HELPER FUNCTIONS ---
def compute_weak_squares(board: chess.Board) -> Dict[str, Set[Tuple[str, str]]]:
    """
    Finds weak squares inside each side's camp:
      - Ranks 3-4 for White's camp.
      - Ranks 5-6 for Black's camp.
    A square is weak if:
      - It cannot be defended by any friendly pawn (either friendly pawns have advanced
        past its file, or friendly pawns on adjacent files are already past/dead).
    Returns a set of tuples: (square_name, color_complex) where color_complex is 'light' or 'dark'.
    """
    res = {}
    for color in [chess.WHITE, chess.BLACK]:
        weak_set = set()
        camp_ranks = [2, 3] if color == chess.WHITE else [4, 5] # 0-indexed corresponding to ranks 3-4 / 5-6
        
        pawns_mask = board.pieces(chess.PAWN, color)

        for file_idx in range(8):
            for rank_idx in camp_ranks:
                sq = chess.square(file_idx, rank_idx)
                
                # Check if a friendly pawn can ever defend this square.
                # Pawns only move forward.
                # A friendly pawn could defend this square if it is currently on an adjacent file
                # and behind the rank of defense (i.e. rank < rank_idx for White, rank > rank_idx for Black)
                # (pawns attack squares diagonally forward).
                can_defend = False
                adj_files = [f for f in [file_idx - 1, file_idx + 1] if 0 <= f <= 7]
                
                for f in adj_files:
                    for r in range(8):
                        if color == chess.WHITE:
                            # White pawns can defend from rank r if r < rank_idx
                            if r < rank_idx and chess.square(f, r) in pawns_mask:
                                can_defend = True
                                break
                        else:
                            # Black pawns can defend from rank r if r > rank_idx
                            if r > rank_idx and chess.square(f, r) in pawns_mask:
                                can_defend = True
                                break
                    if can_defend:
                        break
                
                if not can_defend:
                    # Weak square identified! Determine its color complex
                    # Weak square identified! Determine its color complex
                    file_idx_check = chess.square_file(sq)
                    rank_idx_check = chess.square_rank(sq)
                    complex_tag = "light" if (file_idx_check + rank_idx_check) % 2 == 1 else "dark"
                    weak_set.add((chess.square_name(sq), complex_tag))

        res["white" if color == chess.WHITE else "black"] = weak_set
    return res

# --- PIECE ACTIVITY HELPER FUNCTIONS ---
def compute_piece_activity(board: chess.Board) -> Dict[str, int]:
    """
    Calculates total piece mobility.
    Uses the count of pseudo-legal moves for each color as a proxy for piece activity.
    """
    res = {}
    # Generate pseudo-legal moves for both sides
    for color in [chess.WHITE, chess.BLACK]:
        # Temporarily set board turn to generate moves
        original_turn = board.turn
        board.turn = color
        pseudo_moves = list(board.generate_pseudo_legal_moves())
        board.turn = original_turn
        
        res["white" if color == chess.WHITE else "black"] = len(pseudo_moves)
    return res
