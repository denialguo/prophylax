# server.py - Stateless JSON-RPC 2.0 stdio MCP Server for Stockfish
import sys
import json
import traceback
import chess
import chess.pgn
import chess.engine
import io
from typing import Dict, Any, List, Optional
from config.settings import get_stockfish_path, get_limits, STOCKFISH_THREADS, PINNED_STOCKFISH_VERSION

def log(msg: str):
    print(msg, file=sys.stderr, flush=True)

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
    elif move_number <= 10:
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

def handle_analyze_pgn(params: Dict[str, Any]) -> Dict[str, Any]:
    pgn_text = params.get("pgn", "")
    max_flags = params.get("max_flags", 4)

    # Initialize clean engine instance per request
    stockfish_path = get_stockfish_path()
    if not stockfish_path:
        raise RuntimeError("STOCKFISH_PATH is not set.")

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

    # Setup engine
    limits = get_limits(interactive=False)
    nodes = limits.get("nodes", 1000000) # Pinned to 1M

    import time
    start_time = time.time()
    current_limit = nodes
    print(f"Stockfish search limit: Limit(nodes={current_limit})", file=sys.stderr, flush=True)

    engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
    try:
        engine.configure({"Threads": STOCKFISH_THREADS, "UCI_ShowWDL": True})

        # Replay the game, analyzing each position
        temp_board = game.board()
        
        # Analyze start pos
        if nodes != current_limit:
            raise ValueError(f"Limit changed mid-run: expected {current_limit}, got {nodes}")
        info = engine.analyse(temp_board, chess.engine.Limit(nodes=nodes))
        start_wdl = info.get("wdl")
        
        # Track position and state history
        # Each step stores: (board_state, wdl, mover_side)
        history = [(temp_board.copy(), start_wdl)]

        for m in moves:
            temp_board.push(m)
            if nodes != current_limit:
                raise ValueError(f"Limit changed mid-run: expected {current_limit}, got {nodes}")
            info = engine.analyse(temp_board, chess.engine.Limit(nodes=nodes))
            wdl = info.get("wdl")
            history.append((temp_board.copy(), wdl))

        # We can now scan moves and detect flags
        from mcp_server.features import get_feature_deltas, get_quiet_concessions, evaluate_position_features

        channel1_flags = []
        channel2_flags = []

        temp_board = game.board()
        for idx, m in enumerate(moves):
            # Move info
            move_number = int(idx / 2) + 1
            side = "white" if temp_board.turn == chess.WHITE else "black"
            color = temp_board.turn
            phase = get_game_phase(temp_board, move_number)

            # Boards before and after
            board_before, wdl_before = history[idx]
            board_after, wdl_after = history[idx + 1]

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
            best_info = engine.analyse(board_before, chess.engine.Limit(nodes=nodes))
            best_move = best_info.get("pv", [None])[0]
            best_move_san = board_before.san(best_move) if best_move else ""
            pv = get_pv_san(board_before, best_info.get("pv", []))
            move_san = board_before.san(m)

            # Reformat WDL for exact output JSON: relative wins, draws, losses
            rel_before = wdl_before.relative if wdl_before else None
            rel_after = wdl_after.relative if wdl_after else None

            flag_obj = {
                "move_san": move_san,
                "move_number": move_number,
                "side": side,
                "phase": phase,
                "wdl_before": {"wins": rel_before.wins, "draws": rel_before.draws, "losses": rel_before.losses} if rel_before else None,
                "wdl_after": {"wins": rel_after.wins, "draws": rel_after.draws, "losses": rel_after.losses} if rel_after else None,
                "wdl_before_prob": wp_before,
                "wdl_after_prob": wp_after,
                "wdl_delta": wdl_delta,
                "best_move_san": best_move_san,
                "pv": pv,
                "feature_deltas": feat_deltas
            }

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
                concessions = get_quiet_concessions(board_before, board_after, color)
                has_new_ws = "new_weak_squares" in concessions
                has_new_bp = "new_backward_pawns" in concessions
                if has_new_ws and has_new_bp:
                    flag_obj["channel"] = "quiet_inaccuracy"
                    flag_obj["concessions"] = concessions
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
            "summary": {
                "total_flags": len(selected_flags),
                "phase_distribution": phase_counts,
                "final_wdl": final_wdl
            }
        }
    finally:
        engine.quit()

def handle_analyze_position(params: Dict[str, Any]) -> Dict[str, Any]:
    fen = params.get("fen", "")
    multipv = params.get("multipv", 3)

    stockfish_path = get_stockfish_path()
    if not stockfish_path:
        raise RuntimeError("STOCKFISH_PATH is not set.")

    board = chess.Board(fen)
    limits = get_limits(interactive=False)
    nodes = limits.get("nodes", 1000000)

    import time
    start_time = time.time()
    current_limit = nodes
    print(f"Stockfish search limit: Limit(nodes={current_limit})", file=sys.stderr, flush=True)

    engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
    try:
        engine.configure({"Threads": STOCKFISH_THREADS, "UCI_ShowWDL": True})

        # MultiPV analysis
        if nodes != current_limit:
            raise ValueError(f"Limit changed mid-run: expected {current_limit}, got {nodes}")
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
        # Convert weak_squares set to sorted list for JSON serialization
        ws_list = sorted(list(abs_features["weak_squares"]), key=lambda x: x[0])
        abs_features["weak_squares"] = [{"square": s, "complex": c} for s, c in ws_list]

        duration = time.time() - start_time
        print(f"Fixture: Position Analysis | Positions analyzed: 1 | Node limit: {nodes} | Duration: {duration:.2f}s", file=sys.stderr, flush=True)

        return {
            "multipv_lines": lines,
            "features": abs_features
        }
    finally:
        engine.quit()

# Stdio JSON-RPC 2.0 message parser
def main():
    log("Stockfish MCP server starting up...")
    
    # Verify Stockfish path is set
    try:
        path = get_stockfish_path()
        if not path:
            log("Error: STOCKFISH_PATH environment variable is missing.")
            sys.exit(1)
    except Exception as e:
        log(f"Startup check failed: {e}")
        sys.exit(1)

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            method = req.get("method")
            params = req.get("params", {})
            req_id = req.get("id")

            # Handle JSON-RPC methods
            if method == "initialize":
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "serverInfo": {
                            "name": "prophylax-stockfish-server",
                            "version": "0.1.0"
                        }
                    }
                }
                print(json.dumps(res), flush=True)
            elif method == "tools/list":
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [
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
                                    "required": ["pgn"]
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
                                    "required": ["fen"]
                                }
                            }
                        ]
                    }
                }
                print(json.dumps(res), flush=True)
            elif method == "tools/call":
                tool_name = params.get("name")
                tool_params = params.get("arguments", {})

                try:
                    if tool_name == "analyze_pgn":
                        res_data = handle_analyze_pgn(tool_params)
                    elif tool_name == "analyze_position":
                        res_data = handle_analyze_position(tool_params)
                    else:
                        raise ValueError(f"Unknown tool: {tool_name}")

                    res = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(res_data)
                                }
                            ]
                        }
                    }
                except Exception as err:
                    log(f"Error executing tool {tool_name}: {err}\n{traceback.format_exc()}")
                    res = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32603,
                            "message": f"Tool execution failed: {str(err)}"
                        }
                    }
                print(json.dumps(res), flush=True)
            else:
                if req_id is not None:
                    res = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}"
                        }
                    }
                    print(json.dumps(res), flush=True)
        except Exception as e:
            log(f"JSON-RPC Server Error: {e}\n{traceback.format_exc()}")

if __name__ == "__main__":
    main()
