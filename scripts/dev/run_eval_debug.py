import sys
import os
import json

def get_11_black_delta():
    from mcp_server.server import handle_analyze_pgn
    with open("jyotibikash_vs_DankSonPotato_2026.06.20.pgn") as f:
        pgn = f.read()
    
    os.environ["STOCKFISH_PATH"] = "/opt/homebrew/bin/stockfish"
    os.environ["STOCKFISH_NODES"] = "100000"
    
    result = handle_analyze_pgn({"pgn": pgn})
    move_evals = result.get("move_evals", [])
    for e in move_evals:
        if e["move_number"] == 11 and e["side"] == "black":
            print(f"FOUND EVAL: {e}")

if __name__ == "__main__":
    get_11_black_delta()
