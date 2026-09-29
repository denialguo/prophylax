# verify_goldens.py - Reproducibility Stability Gate for Stockfish golden datasets
import os
import sys
import json
import argparse
import chess.pgn
from typing import Dict, Any, List

# Add path root so config works
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config.settings import get_stockfish_path
from mcp_server.server import handle_analyze_pgn

FIXTURES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures"))

MANIFEST = {
    "fools_mate": {
        "pgn_file": "fools_mate.pgn",
        "json_file": "fools_mate.json",
        "positives": [
            {"move_number": 2, "move_san": "g4", "side": "white"}
        ],
        "negatives": []
    },
    "scandinavian_blitz": {
        "pgn_file": "scandinavian_blitz.pgn",
        "json_file": "scandinavian_blitz.json",
        "positives": [
            {"move_number": 22, "move_san": "g4", "side": "white"},
            {"move_number": 12, "move_san": "Ne7", "side": "black"},
            {"move_number": 13, "move_san": "b4", "side": "white"},
            {"move_number": 21, "move_san": "h4", "side": "white"}
        ],
        "negatives": [
            {"move_number": 1, "move_san": "d5", "side": "black"}
        ]
    },
    "kp_opposition": {
        "pgn_file": "kp_opposition.pgn",
        "json_file": "kp_opposition.json",
        # 4.Ke3 gives Black the opposition (win -> draw); the same move at move 2 is fine
        "positives": [
            {"move_number": 4, "move_san": "Ke3", "side": "white"}
        ],
        "negatives": [
            {"move_number": 1, "move_san": "Kd3", "side": "white"},
            {"move_number": 2, "move_san": "Ke3", "side": "white"},
            {"move_number": 5, "move_san": "d3", "side": "white"}
        ]
    },
    "carlsbad_quiet": {
        "pgn_file": "carlsbad_quiet.pgn",
        "json_file": "carlsbad_quiet.json",
        "positives": [
            {"move_number": 10, "move_san": "b5", "side": "black", "channel": "quiet_inaccuracy"}
        ],
        "negatives": [
            {"move_number": 5, "move_san": "c6", "side": "black"},
            {"move_number": 6, "move_san": "e3", "side": "white"}
        ]
    }
}

def record_goldens(names):
    """
    Runs the asserted games on the pinned rig, records exact measurements,
    and writes the corresponding .json files containing ONLY expected WDL bands.
    """
    print("Recording goldens on the pinned rig...")
    
    for name in names:
        config = MANIFEST[name]
        pgn_path = os.path.join(FIXTURES_DIR, config["pgn_file"])
        pgn_text = open(pgn_path).read()
        res = handle_analyze_pgn({"pgn": pgn_text, "max_flags": 100})
        
        recorded_flags = []
        for pos in config["positives"]:
            match = [
                f for f in res["flags"]
                if f["move_san"] == pos["move_san"]
                and f["move_number"] == pos["move_number"]
                and f["side"] == pos["side"]
            ]
            if not match:
                raise ValueError(f"Manifest move {pos['move_number']}...{pos['move_san']} for {name} not found in flags.")
            f = match[0]
            recorded_flags.append({
                "move_san": f["move_san"],
                "move_number": f["move_number"],
                "wdl_delta_min": f["wdl_delta"] - 0.015,
                "wdl_delta_max": f["wdl_delta"] + 0.015
            })
            
        bands_data = {"flags": recorded_flags}
        json_path = os.path.join(FIXTURES_DIR, config["json_file"])
        with open(json_path, "w") as fw:
            json.dump(bands_data, fw, indent=2)
        print(f"Recorded {config['json_file']} successfully.")

def verify_goldens() -> bool:
    """
    Verifies that the recorded bands hold at 1,000,000 nodes,
    and decision stability holds at 3,000,000 nodes.
    """
    print("Starting Golden Verification Gate...")
    
    original_nodes = os.environ.get("STOCKFISH_NODES")
    success = True
    expected_decisions = {}
    
    # Run 1,000,000 nodes (Bands Verification)
    print("\nEvaluating Stability at 1,000,000 nodes...")
    os.environ["STOCKFISH_NODES"] = "1000000"
    
    for name, config in MANIFEST.items():
        pgn_path = os.path.join(FIXTURES_DIR, config["pgn_file"])
        pgn_text = open(pgn_path).read()
        res = handle_analyze_pgn({"pgn": pgn_text, "max_flags": 100})
        
        json_path = os.path.join(FIXTURES_DIR, config["json_file"])
        with open(json_path) as fr:
            gold_data = json.load(fr)
            
        # Verify positives
        for gold_flag in gold_data["flags"]:
            pos_config = next(p for p in config["positives"] if p["move_san"] == gold_flag["move_san"] and p["move_number"] == gold_flag["move_number"])
            match = [
                f for f in res["flags"]
                if f["move_san"] == gold_flag["move_san"]
                and f["move_number"] == gold_flag["move_number"]
                and f["side"] == pos_config["side"]
            ]
            if not match:
                print(f"FAIL: {name} expected move {gold_flag['move_number']}. {gold_flag['move_san']} was not flagged.")
                success = False
            else:
                f = match[0]
                if not (gold_flag["wdl_delta_min"] <= f["wdl_delta"] <= gold_flag["wdl_delta_max"]):
                    print(f"FAIL: {name} WDL delta {f['wdl_delta']:.4f} out of bounds [{gold_flag['wdl_delta_min']:.4f}, {gold_flag['wdl_delta_max']:.4f}] for {gold_flag['move_san']}")
                    success = False
                else:
                    expected_decisions[(name, gold_flag["move_number"], gold_flag["move_san"])] = {
                        "channel": f.get("channel"),
                        "phase": f.get("phase"),
                        "sign": (f["wdl_delta"] >= 0.0)
                    }
                    
        # Verify negatives
        for neg in config["negatives"]:
            match = [
                f for f in res["flags"]
                if f["move_san"] == neg["move_san"]
                and f["move_number"] == neg["move_number"]
                and f["side"] == neg["side"]
            ]
            if match:
                print(f"FAIL: {name} negative move {neg['move_number']}. {neg['move_san']} was unexpectedly flagged.")
                success = False

    # Run 3,000,000 nodes (Decisions Verification Only)
    print("\nEvaluating Stability at 3,000,000 nodes...")
    os.environ["STOCKFISH_NODES"] = "3000000"
    
    for name, config in MANIFEST.items():
        pgn_path = os.path.join(FIXTURES_DIR, config["pgn_file"])
        pgn_text = open(pgn_path).read()
        res_3m = handle_analyze_pgn({"pgn": pgn_text, "max_flags": 100})
        
        # Verify positives at 3M
        for pos in config["positives"]:
            key = (name, pos["move_number"], pos["move_san"])
            if key in expected_decisions:
                exp = expected_decisions[key]
                match = [
                    f for f in res_3m["flags"]
                    if f["move_san"] == pos["move_san"]
                    and f["move_number"] == pos["move_number"]
                    and f["side"] == pos["side"]
                ]
                if not match:
                    print(f"FAIL: {name} expected move {pos['move_number']}. {pos['move_san']} was not flagged at 3M.")
                    success = False
                else:
                    f = match[0]
                    if f.get("channel") != exp["channel"]:
                        print(f"FAIL: {name} channel mismatch at 3M for {pos['move_san']}: expected {exp['channel']}, got {f.get('channel')}")
                        success = False
                    if f.get("phase") != exp["phase"]:
                        print(f"FAIL: {name} phase mismatch at 3M for {pos['move_san']}: expected {exp['phase']}, got {f.get('phase')}")
                        success = False
                    actual_sign = (f["wdl_delta"] >= 0.0)
                    if actual_sign != exp["sign"]:
                        print(f"FAIL: {name} delta sign mismatch at 3M for {pos['move_san']}: expected {exp['sign']}, got {actual_sign}")
                        success = False
                        
        # Verify negatives at 3M
        for neg in config["negatives"]:
            match = [
                f for f in res_3m["flags"]
                if f["move_san"] == neg["move_san"]
                and f["move_number"] == neg["move_number"]
                and f["side"] == neg["side"]
            ]
            if match:
                print(f"FAIL: {name} negative move {neg['move_number']}. {neg['move_san']} was unexpectedly flagged at 3M.")
                success = False

    # Restore environment
    if original_nodes is None:
        os.environ.pop("STOCKFISH_NODES", None)
    else:
        os.environ["STOCKFISH_NODES"] = original_nodes
        
    return success

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reproducibility golden gate verification.")
    parser.add_argument("--record", action="store_true", help="Record expected bands on the pinned rig.")
    parser.add_argument("--force-rerecord", action="store_true", help="Force re-recording even if bands exist.")
    args = parser.parse_args()
    
    if args.record or args.force_rerecord:
        # --record only writes bands that don't exist yet; existing bands are never
        # touched unless --force-rerecord is given explicitly
        missing = [n for n, c in MANIFEST.items() if not os.path.exists(os.path.join(FIXTURES_DIR, c["json_file"]))]
        names = list(MANIFEST) if args.force_rerecord else missing
        if not names:
            print("bands exist; re-recording requires --force-rerecord")
            sys.exit(1)
        record_goldens(names)
        sys.exit(0)
    else:
        passed = verify_goldens()
        if passed:
            print("\nStability Golden Gate passed successfully!")
            sys.exit(0)
        else:
            print("\nStability Golden Gate FAILED!")
            sys.exit(1)
