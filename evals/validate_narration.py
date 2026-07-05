import re
import os
import chess
import chess.pgn
import io
from typing import Dict, Any, Set

def find_fixtures_dir() -> str:
    curr = os.path.dirname(os.path.abspath(__file__))
    while curr:
        candidate = os.path.join(curr, "tests", "fixtures")
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent
    return ""

def get_matching_game(flag: dict) -> chess.pgn.Game:
    fix_dir = find_fixtures_dir()
    if not fix_dir:
        return None
    
    flag_move = flag.get("move_san")
    flag_num = flag.get("move_number")
    flag_side = flag.get("side", "").lower()
    
    for f_name in os.listdir(fix_dir):
        if f_name.endswith(".pgn"):
            try:
                with open(os.path.join(fix_dir, f_name)) as f:
                    game = chess.pgn.read_game(f)
                    if not game:
                        continue
                    board = game.board()
                    matched = False
                    for idx, m in enumerate(game.mainline_moves()):
                        num = int(idx / 2) + 1
                        san = board.san(m)
                        side = "white" if board.turn == chess.WHITE else "black"
                        if num == flag_num and san == flag_move and side == flag_side:
                          matched = True
                          break
                        board.push(m)
                    if matched:
                        return game
            except Exception:
                continue
    return None

def extract_squares(text: str) -> Set[str]:
    """Extracts all square tokens (a1-h8) from the text."""
    squares = re.findall(r'\b[a-h][1-8]\b', text.lower())
    return set(squares)

def extract_squares_from_move(move_san: str) -> Set[str]:
    """Extracts squares from a SAN move string (e.g. Nf3 -> f3, O-O -> empty)."""
    if not move_san:
        return set()
    clean = re.sub(r'[KQRBNx+#=]', '', move_san)
    squares = re.findall(r'[a-h][1-8]', clean.lower())
    return set(squares)

def get_allowed_squares(flag: Dict[str, Any]) -> Set[str]:
    """Computes the set of allowed squares from the flag payload."""
    allowed = set()
    allowed.update(extract_squares_from_move(flag.get("move_san", "")))
    allowed.update(extract_squares_from_move(flag.get("best_move_san", "")))
    
    for move in flag.get("pv", []):
        allowed.update(extract_squares_from_move(move))
    for move in flag.get("refutation_pv", []):
        allowed.update(extract_squares_from_move(move))
        
    concessions = flag.get("concessions", {})
    if concessions:
        for k in ["new_weak_squares", "new_backward_pawns", "new_fixed_backward_pawns"]:
            for sq in concessions.get(k, []):
                allowed.update(re.findall(r'[a-h][1-8]', sq.lower()))
                
    return allowed

def check_narration_moves_legality(narration: str, flag: dict) -> bool:
    game = get_matching_game(flag)
    if not game:
        return True
        
    pattern = r'\b(\d+)(?:\s*(\.\.\.|\.))?\s*([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O-O|O-O)\b'
    matches = re.findall(pattern, narration)
    if not matches:
        return True
        
    val_board = None
    prev_move_num = None
    prev_is_black = None
    
    for move_num_str, dots, san in matches:
        move_num = int(move_num_str)
        is_black = (dots == "...")
        
        consecutive = False
        if val_board is not None:
            if prev_is_black:
                if not is_black and move_num == prev_move_num + 1:
                    consecutive = True
            else:
                if is_black and move_num == prev_move_num:
                    consecutive = True
                    
        if not consecutive:
            val_board = game.board()
            target_ply = (move_num - 1) * 2
            if is_black:
                target_ply += 1
                
            for idx, m in enumerate(game.mainline_moves()):
                if idx == target_ply:
                    break
                val_board.push(m)
                
        try:
            move_obj = val_board.parse_san(san)
            if move_obj not in val_board.legal_moves:
                return False
            val_board.push(move_obj)
            prev_move_num = move_num
            prev_is_black = is_black
        except ValueError:
            return False
            
    return True

def validate_narration(narration: str, flag: Dict[str, Any]) -> bool:
    """
    Validates that:
    1. No internal feature magnitudes like "delta of -X" or similar are mentioned.
    2. All bare square tokens mentioned in the narration exist in the flag's payload whitelist.
    3. All cited SAN moves are legal in their chess position.
    """
    # 1. Regex-reject internal feature magnitudes ("delta of -X")
    if re.search(r'delta\s+of\s+-?\d+', narration.lower()) or re.search(r'delta\s+magnitude', narration.lower()):
        return False
        
    # 2. Whitelist square check
    narrated_squares = extract_squares(narration)
    allowed_squares = get_allowed_squares(flag)
    for sq in narrated_squares:
        if sq not in allowed_squares:
            return False
            
    # 3. Legality check for cited SAN moves
    if not check_narration_moves_legality(narration, flag):
        return False
        
    return True
