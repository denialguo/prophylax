# server.py - Stateless JSON-RPC 2.0 stdio MCP Server for Stockfish
import sys
import json
import traceback
import chess
import chess.pgn
import chess.engine
import io
import contextlib
import threading
from typing import Dict, Any, List, Optional
from config.settings import (
    get_stockfish_path, get_limits, get_search_timeout, STOCKFISH_THREADS,
    MAX_PGN_CHARS, MAX_PGN_PLIES, MAX_FLAGS, MAX_MULTIPV,
)

# JSON-RPC error codes. -32000..-32099 are reserved for implementation-defined errors.
PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
ENGINE_TIMEOUT = -32001
ENGINE_CRASHED = -32002
ENGINE_UNAVAILABLE = -32003

class EngineTimeout(Exception):
    pass

class EngineUnavailable(RuntimeError):
    pass

def log(msg: str):
    print(msg, file=sys.stderr, flush=True)

@contextlib.contextmanager
def search_deadline(engine: chess.engine.SimpleEngine):
    """Node-limited searches have no python-chess timeout. If one outlives the
    deadline, kill the engine (close() is thread-safe) and raise EngineTimeout;
    partial results are never returned."""
    seconds = get_search_timeout()
    fired = threading.Event()

    def kill():
        fired.set()
        engine.close()

    timer = threading.Timer(seconds, kill)
    timer.start()
    try:
        yield
    except chess.engine.EngineError:
        if fired.is_set():
            raise EngineTimeout(f"Stockfish search exceeded {seconds:g}s")
        raise
    finally:
        timer.cancel()

def open_engine() -> chess.engine.SimpleEngine:
    stockfish_path = get_stockfish_path()
    if not stockfish_path:
        raise EngineUnavailable("STOCKFISH_PATH is not set.")
    engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
    try:
        engine.configure({"Threads": STOCKFISH_THREADS, "UCI_ShowWDL": True})
    except Exception:
        engine.close()
        raise
    return engine

def _bounded_int(params: Dict[str, Any], key: str, default: int, hi: int) -> int:
    val = params.get(key, default)
    if type(val) is not int or not 1 <= val <= hi:
        raise ValueError(f"'{key}' must be an integer in [1, {hi}], got {val!r}")
    return val

# Helper to compute game phase
def get_game_phase(board: chess.Board, move_number: int) -> str:
    """
    Infers the game phase from move number, material count, and queen presence.
    """
    # Count pieces
    white_queens = len(board.pieces(chess.QUEEN, chess.WHITE))
    black_queens = len(board.pieces(chess.QUEEN, chess.BLACK))
    
    total_material = 0
    for color in [chess.WHITE, chess.BLACK]:
        for pt in [chess.ROOK, chess.KNIGHT, chess.BISHOP, chess.PAWN]:
            total_material += len(board.pieces(pt, color))

    # Endgame: no queens for both sides, or queens exist but total minor/major pieces <= 2
    no_queens = (white_queens == 0 and black_queens == 0)
    low_material = (total_material <= 6)  # roughly 3 minor pieces per side or equivalent

    if no_queens or low_material:
        return "endgame"
    elif move_number <= 9:
        return "opening"
    else:
        return "middlegame"

def get_win_probability(wdl: Optional[chess.engine.PovWdl], side: chess.Color, board: Optional[chess.Board] = None) -> float:
    """
    Retrieves win probability as win_rate + 0.5 * draw_rate from side's perspective.
    Handles None and checkmate/draw boards gracefully.
    """
    if board is not None:
        if board.is_checkmate():
            return 0.0 if board.turn == side else 1.0
        if board.is_game_over():
            return 0.5

    if not wdl:
        return 0.5

    pov_wdl = wdl.pov(side)
    w = pov_wdl.wins
    d = pov_wdl.draws
    l = pov_wdl.losses
    total = w + d + l
    if total == 0:
        return 0.5
    return (w + 0.5 * d) / total

def get_pv_san(board: chess.Board, pv_moves: List[chess.Move]) -> List[str]:
    """
    Generates SAN sequences for a list of moves, keeping at most 6 plies.
    """
    temp_board = board.copy()
    san_list = []
    for m in pv_moves[:6]:  # Limit to 6 plies
        if m in temp_board.legal_moves:
            san_list.append(temp_board.san(m))
            temp_board.push(m)
        else:
            break
    return san_list

def analyse_with_longest_pv(engine, board, nodes):
    """
    Analyses the position and returns the final info dictionary,
    overwriting info['pv'] with the longest PV observed during the search
    to prevent truncation from fail-high/fail-low updates.
    """
    info = {}
    best_pv = []
    with search_deadline(engine), engine.analysis(board, chess.engine.Limit(nodes=nodes)) as analysis:
        for info_item in analysis:
            info.update(info_item)
            if "pv" in info_item:
                if len(info_item["pv"]) > len(best_pv):
                    best_pv = info_item["pv"]
    info["pv"] = best_pv
    return info

def handle_analyze_pgn(params: Dict[str, Any]) -> Dict[str, Any]:
    pgn_text = params.get("pgn", "")
    if not isinstance(pgn_text, str) or len(pgn_text) > MAX_PGN_CHARS:
        raise ValueError(f"'pgn' must be a string of at most {MAX_PGN_CHARS} characters.")
    max_flags = _bounded_int(params, "max_flags", 4, MAX_FLAGS)

    # Parse PGN
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if not game or game.errors:
        raise ValueError("Invalid PGN format or parsing error.")

    # Check for illegal moves or parsing errors
    board = game.board()
    moves = []
    for m in game.mainline_moves():
        if m not in board.legal_moves:
            raise ValueError(f"Illegal move detected in PGN: {m} in position {board.fen()}")
        moves.append(m)
        board.push(m)

    if not moves:
        raise ValueError("Empty PGN or no moves parsed.")
    if len(moves) > MAX_PGN_PLIES:
        raise ValueError(f"Game has {len(moves)} plies; the limit is {MAX_PGN_PLIES}.")

    # Setup engine
    limits = get_limits(interactive=False)
    nodes = limits.get("nodes", 1000000) # Pinned to 1M

    import time
    start_time = time.time()
    current_limit = nodes
    print(f"Stockfish search limit: Limit(nodes={current_limit})", file=sys.stderr, flush=True)

    engine = open_engine()
    try:
        # Replay the game, analyzing each position
        temp_board = game.board()

        # Analyze start pos
        if nodes != current_limit:
            raise ValueError(f"Limit changed mid-run: expected {current_limit}, got {nodes}")
        with search_deadline(engine):
            info = engine.analyse(temp_board, chess.engine.Limit(nodes=nodes))
        start_wdl = info.get("wdl")
        
        # Track position and state history
        # Each step stores: (board_state, wdl, pv)
        history = [(temp_board.copy(), start_wdl, [])]

        for m in moves:
            temp_board.push(m)
            if nodes != current_limit:
                raise ValueError(f"Limit changed mid-run: expected {current_limit}, got {nodes}")
            info = analyse_with_longest_pv(engine, temp_board, nodes)
            wdl = info.get("wdl")
            pv = info.get("pv", [])
            history.append((temp_board.copy(), wdl, pv))

        # We can now scan moves and detect flags
        from mcp_server.features import get_feature_deltas, get_quiet_concessions

        channel1_flags = []
        channel2_flags = []
        move_evals = []

        temp_board = game.board()
        for idx, m in enumerate(moves):
            # Boards before and after
            board_before, wdl_before, _ = history[idx]
            board_after, wdl_after, post_move_pv = history[idx + 1]

            # Move info
            move_number = int(idx / 2) + 1
            side = "white" if board_before.turn == chess.WHITE else "black"
            color = board_before.turn
            phase = get_game_phase(board_before, move_number)

            # Win probabilities
            wp_before = get_win_probability(wdl_before, color, board_before)
            wp_after = get_win_probability(wdl_after, color, board_after)
            wdl_delta = wp_after - wp_before  # negative is worse for mover

            # Evaluate feature changes
            feat_deltas = get_feature_deltas(board_before, board_after, color)

            # Analyze best move at this position for PV and best move SAN
            # Since we need PV from before position, we run analysis on board_before
            if nodes != current_limit:
                raise ValueError(f"Limit changed mid-run: expected {current_limit}, got {nodes}")
            best_info = analyse_with_longest_pv(engine, board_before, nodes)
            pv_list = best_info.get("pv", [])
            best_move = pv_list[0] if pv_list else None
            best_move_san = board_before.san(best_move) if best_move else ""
            pv = get_pv_san(board_before, pv_list)
            move_san = board_before.san(m)

            refutation_pv = get_pv_san(board_after, post_move_pv)
            concessions = get_quiet_concessions(board_before, board_after, color)

            flag_obj = {
                "move_san": move_san,
                "move_number": move_number,
                "side": side,
                "phase": phase,
                "wdl_before_prob": wp_before,
                "wdl_after_prob": wp_after,
                "wdl_delta": wdl_delta,
                "best_move_san": best_move_san,
                "pv": pv,
                "refutation_pv": refutation_pv,
                "feature_deltas": feat_deltas,
                "concessions": concessions
            }

            move_evals.append({
                "move_san": move_san,
                "move_number": move_number,
                "side": side,
                "phase": phase,
                "wdl_delta": wdl_delta,
                "best_move_san": best_move_san
            })

            # Channel 1 drop thresholds (negative bounds)
            if move_number <= 3:
                threshold = -0.20
            else:
                threshold = -0.05 if phase == "opening" else (-0.08 if phase == "middlegame" else -0.10)
            
            channel1_triggered = (wdl_delta <= threshold)

            if channel1_triggered:
                flag_obj["channel"] = "wdl"
                channel1_flags.append(flag_obj)
            elif (move_number > 3) and (wdl_delta <= 0.0):
                # Channel 2: Quiet concessions check
                has_new_ws = "new_weak_squares" in concessions
                has_new_bp = "new_backward_pawns" in concessions
                if has_new_ws and has_new_bp:
                    flag_obj["channel"] = "quiet_inaccuracy"
                    channel2_flags.append(flag_obj)

            temp_board.push(m)

        # Rank flags:
        # Channel 1 ranked by WDL drop ascending (most negative first)
        channel1_flags.sort(key=lambda x: x["wdl_delta"])
        # Channel 2 ranked below Channel 1
        channel2_flags.sort(key=lambda x: x["wdl_delta"])
        all_flags = channel1_flags + channel2_flags
        selected_flags = all_flags[:max_flags]

        # Phase distribution
        phase_counts = {"opening": 0, "middlegame": 0, "endgame": 0}
        for f in selected_flags:
            phase_counts[f["phase"]] += 1

        final_wdl_obj = history[-1][1]
        final_wdl = {"wins": final_wdl_obj.relative.wins, "draws": final_wdl_obj.relative.draws, "losses": final_wdl_obj.relative.losses} if final_wdl_obj else None

        # Log summary line to stderr
        duration = time.time() - start_time
        event_name = game.headers.get("Event", "Unknown Event")
        print(f"Fixture: {event_name} | Positions analyzed: {len(moves) + 1} | Node limit: {nodes} | Duration: {duration:.2f}s", file=sys.stderr, flush=True)

        return {
            "flags": selected_flags,
            "move_evals": move_evals,
            "summary": {
                "total_flags": len(selected_flags),
                "phase_distribution": phase_counts,
                "final_wdl": final_wdl
            }
        }
    finally:
        engine.close()  # never raises, even after a deadline kill

def handle_analyze_position(params: Dict[str, Any]) -> Dict[str, Any]:
    fen = params.get("fen", "")
    multipv = _bounded_int(params, "multipv", 3, MAX_MULTIPV)

    if not isinstance(fen, str):
        raise ValueError("'fen' must be a string.")
    board = chess.Board(fen)
    if not board.is_valid():
        # e.g. missing kings: Stockfish's behaviour on such positions is undefined
        raise ValueError(f"FEN is not a legal position: {board.status()!r}")
    limits = get_limits(interactive=False)
    nodes = limits.get("nodes", 1000000)

    import time
    start_time = time.time()
    current_limit = nodes
    print(f"Stockfish search limit: Limit(nodes={current_limit})", file=sys.stderr, flush=True)

    engine = open_engine()
    try:
        # MultiPV analysis
        if nodes != current_limit:
            raise ValueError(f"Limit changed mid-run: expected {current_limit}, got {nodes}")
        with search_deadline(engine):
            results = engine.analyse(board, chess.engine.Limit(nodes=nodes), multipv=multipv)
        # If multipv=1, analyze returns a dict, otherwise a list of dicts
        if isinstance(results, dict):
            results = [results]

        lines = []
        for r in results:
            wdl_obj = r.get("wdl")
            rel_wdl = wdl_obj.relative if wdl_obj else None
            lines.append({
                "pv": get_pv_san(board, r.get("pv", [])),
                "wdl": {"wins": rel_wdl.wins, "draws": rel_wdl.draws, "losses": rel_wdl.losses} if rel_wdl else None
            })

        # Deterministic absolute features
        from mcp_server.features import evaluate_position_features
        abs_features = evaluate_position_features(board)
        # Convert weak_squares sets to sorted lists for JSON serialization
        for color_key in ["white", "black"]:
            ws_list = sorted(list(abs_features["weak_squares"][color_key]), key=lambda x: x[0])
            abs_features["weak_squares"][color_key] = [{"square": s, "complex": c} for s, c in ws_list]

        duration = time.time() - start_time
        print(f"Fixture: Position Analysis | Positions analyzed: 1 | Node limit: {nodes} | Duration: {duration:.2f}s", file=sys.stderr, flush=True)

        return {
            "multipv_lines": lines,
            "features": abs_features
        }
    finally:
        engine.close()

# Stdio JSON-RPC 2.0 message parser
TOOLS = [
    {
        "name": "analyze_pgn",
        "description": "Parses a full PGN game and evaluates moves using Stockfish engine at pinned search limits. Flags theoretical blunders (Channel 1) and quiet positional/structural concessions (Channel 2) like backward pawns and weak squares.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pgn": {
                    "type": "string",
                    "description": "Full PGN chess game string to analyze."
                },
                "max_flags": {
                    "type": "integer",
                    "default": 4,
                    "description": "Maximum number of blunder/concession flags to return."
                }
            },
            "required": [
                "pgn"
            ]
        }
    },
    {
        "name": "analyze_position",
        "description": "Performs deep analysis on a single FEN position using Stockfish, returning MultiPV lines and absolute static positional features (weak squares in camps, king safety, pawns structure).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fen": {
                    "type": "string",
                    "description": "FEN string of the position to analyze."
                },
                "multipv": {
                    "type": "integer",
                    "default": 3,
                    "description": "Number of alternative lines to calculate."
                }
            },
            "required": [
                "fen"
            ]
        }
    }
]

HANDLERS = {
    "analyze_pgn": lambda p: handle_analyze_pgn(p),
    "analyze_position": lambda p: handle_analyze_position(p),
}

def _error_code(err: Exception) -> int:
    if isinstance(err, EngineTimeout):
        return ENGINE_TIMEOUT
    if isinstance(err, chess.engine.EngineError):
        return ENGINE_CRASHED
    if isinstance(err, (EngineUnavailable, OSError)):
        return ENGINE_UNAVAILABLE
    if isinstance(err, ValueError):
        return INVALID_PARAMS
    return INTERNAL_ERROR

def _error(req_id, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

def handle_line(line: str) -> Optional[Dict[str, Any]]:
    """One JSON-RPC request line -> response dict (None for notifications)."""
    try:
        req = json.loads(line)
        if not isinstance(req, dict):
            raise ValueError("request must be a JSON object")
    except ValueError as e:
        return _error(None, PARSE_ERROR, f"Parse error: {e}")

    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "prophylax-stockfish-server", "version": "0.1.0"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        tool_name = params.get("name")
        if tool_name not in HANDLERS:
            return _error(req_id, INVALID_PARAMS, f"Unknown tool: {tool_name}")
        try:
            res_data = HANDLERS[tool_name](params.get("arguments") or {})
        except Exception as err:
            code = _error_code(err)
            log(f"Error executing tool {tool_name} (code {code}): {err}\n{traceback.format_exc()}")
            # Internal errors keep details in stderr; the rest are safe to surface
            message = "Internal server error" if code == INTERNAL_ERROR else str(err)[:300]
            return _error(req_id, code, message)
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": json.dumps(res_data)}]
        }}
    if req_id is None:
        return None  # notification
    return _error(req_id, METHOD_NOT_FOUND, f"Method not found: {method}")

def main():
    log("Stockfish MCP server starting up...")
    try:
        from config import verify_engine
        verify_engine()
        get_limits(interactive=False)
    except Exception as e:
        log(f"Startup check failed: {e}")
        sys.exit(1)

    for line in sys.stdin:
        if not line.strip():
            continue
        res = handle_line(line)
        if res is not None:
            print(json.dumps(res), flush=True)

if __name__ == "__main__":
    main()
