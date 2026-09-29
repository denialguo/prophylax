import sys
import re
import chess.pgn
import chess
import io
from typing import Tuple, Dict, Any

WHITELISTED_HEADERS = {
    "Event", "Site", "Date", "Round", "White", "Black", "Result", 
    "ECO", "TimeControl"
}

# Non-name headers must match their format or are dropped. Free text (Event, Site)
# can't be proven injection-free, so it is only charset-limited; it never reaches an
# LLM prompt anyway (prompts get sanitized_movetext only).
HEADER_FORMATS = {
    "Event": r"[A-Za-z0-9 .,'()#:/?-]{1,60}",
    "Site": r"[A-Za-z0-9 .,'()#:/?-]{1,60}",
    "Date": r"[0-9?]{4}\.[0-9?]{2}\.[0-9?]{2}",
    "Round": r"[0-9?.-]{1,10}",
    "Result": r"1-0|0-1|1/2-1/2|\*",
    "ECO": r"[A-E][0-9]{2}|\?",
    "TimeControl": r"[0-9+/:*?-]{1,30}",
}

def _is_safe_player_name(name: str) -> bool:
    if not name or len(name) > 40:
        return False
    if not re.match(r"^[A-Za-z .,'-]{1,40}$", name):
        return False
    return True

def sanitize_pgn_string(pgn_text: str) -> Tuple[str, list[str]]:
    """Sanitizes PGN text, returning (clean_pgn, log_lines)."""
    logs = []
    
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if not game:
        return "", ["Failed to parse PGN."]
    if "FEN" in game.headers:
        try:
            start_fen = chess.Board(game.headers["FEN"]).fen()  # re-emitted, never the raw text
        except ValueError:
            return "", ["Rejected PGN: invalid FEN header."]

    clean_headers = {}
    for key, value in game.headers.items():
        if key in ("SetUp", "FEN"):
            continue
        if key not in WHITELISTED_HEADERS:
            logs.append(f"Stripped header: {key}")
            continue
            
        if key in ("White", "Black"):
            if not _is_safe_player_name(value):
                logs.append(f"Sanitized {key} name: '{value}' -> 'Player1/2'")
                clean_headers[key] = "Player1" if key == "White" else "Player2"
            else:
                clean_headers[key] = value
        elif not re.fullmatch(HEADER_FORMATS[key], value):
            logs.append(f"Dropped malformed header: {key}")
        else:
            clean_headers[key] = value
            
    clean_game = chess.pgn.Game()
    if "FEN" in game.headers:
        clean_game.setup(start_fen)
    for k, v in clean_headers.items():
        clean_game.headers[k] = v
        
    # Transfer mainline moves only, stripping comments, NAGs, and variations.
    curr_clean = clean_game
    for move in game.mainline_moves():
        curr_clean = curr_clean.add_main_variation(move)
        
    exporter = chess.pgn.StringExporter(columns=None, headers=True, variations=False, comments=False)
    clean_pgn = clean_game.accept(exporter)
    logs.append("Stripped all comments, NAGs, and variations from movetext.")
    
    return str(clean_pgn), logs

def sanitized_movetext(pgn_text: str) -> str:
    """Mainline moves only, no headers/comments: the only PGN form allowed into LLM prompts."""
    clean_pgn, _ = sanitize_pgn_string(pgn_text)
    game = chess.pgn.read_game(io.StringIO(clean_pgn)) if clean_pgn else None
    if not game:
        return ""
    return game.accept(chess.pgn.StringExporter(columns=None, headers=False, variations=False, comments=False))

def sanitize_tool_input(tool_name: str, arguments: dict) -> Tuple[bool, dict, str]:
    """
    Sanitizes tool inputs before they hit the MCP server or model context.
    Returns (is_valid, sanitized_arguments, error_message).
    """
    if tool_name == "analyze_pgn":
        pgn = arguments.get("pgn", "")
        clean_pgn, logs = sanitize_pgn_string(pgn)
        for log in logs:
            print(f"[sanitize_pgn] {log}", file=sys.stderr)
        
        # Modify arguments in place
        arguments["pgn"] = clean_pgn
        return True, arguments, ""
        
    elif tool_name == "analyze_position":
        fen = arguments.get("fen", "")
        try:
            chess.Board(fen)
            return True, arguments, ""
        except ValueError as e:
            msg = f"Invalid FEN string rejected: {str(e)}"
            print(f"[sanitize_position] {msg}", file=sys.stderr)
            return False, arguments, msg
            
    return True, arguments, ""
