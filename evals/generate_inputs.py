import os
import sys
import json
import io
import chess.pgn

# Add path root so config and mcp_server work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mcp_server.server import handle_analyze_pgn

FIXTURES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../tests/fixtures"))
EVALS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "skills"))

def get_top_feature(flag):
    if not flag.get("feature_deltas"):
        return "none"
    return max(flag["feature_deltas"].items(), key=lambda x: abs(x[1]))[0]

def main():
    os.makedirs(EVALS_DIR, exist_ok=True)
    
    # 1. Fools mate PGN -> opening_prep.json
    fools_pgn = open(os.path.join(FIXTURES_DIR, "fools_mate.pgn")).read()
    res_fools = handle_analyze_pgn({"pgn": fools_pgn, "max_flags": 100})
    
    g4_flag = next(f for f in res_fools["flags"] if f["move_san"] == "g4" and f["move_number"] == 2)
    top_g4 = get_top_feature(g4_flag)
    
    opening_cases = [
        {
            "name": "fools_mate_g4_blunder",
            "input": {
                "flagged_move": g4_flag,
                "config": {
                    "explanation_depth": 1,
                    "audience_rating": 1800
                }
            },
            "expected_primary_theme": top_g4,
            "expected_vocabulary_tier": "1800+",
            "forbidden_claims": ["loses material", "weakens the queenside"]
        },
        # Synthetic opening case 2
        {
            "name": "synthetic_opening_development_concession",
            "input": {
                "flagged_move": {
                    "move_san": "h3",
                    "move_number": 4,
                    "side": "white",
                    "phase": "opening",
                    "wdl_delta": -0.075,
                    "best_move_san": "Nf3",
                    "pv": ["Nf3", "d5", "d4"],
                    "refutation_pv": ["d5", "d4", "Nf3"],
                    "channel": "wdl",
                    "feature_deltas": {
                        "king_safety_delta": -0.3,
                        "pawn_structure_delta": -0.1
                    },
                    "synthetic": True
                },
                "config": {
                    "explanation_depth": 1,
                    "audience_rating": 1800
                }
            },
            "expected_primary_theme": "king_safety_delta",
            "expected_vocabulary_tier": "1800+",
            "forbidden_claims": ["tactical mating net", "loses a rook"]
        },
        # Synthetic opening case 3
        {
            "name": "synthetic_opening_center_abandonment",
            "input": {
                "flagged_move": {
                    "move_san": "a3",
                    "move_number": 4,
                    "side": "white",
                    "phase": "opening",
                    "wdl_delta": -0.07,
                    "best_move_san": "d4",
                    "pv": ["d4", "Nf6", "c4"],
                    "refutation_pv": ["e5", "d4", "Nf6"],
                    "channel": "wdl",
                    "feature_deltas": {
                        "king_safety_delta": 0.0,
                        "pawn_structure_delta": 0.0
                    },
                    "synthetic": True
                },
                "config": {
                    "explanation_depth": 1,
                    "audience_rating": 1800
                }
            },
            "expected_primary_theme": "none",
            "expected_vocabulary_tier": "1800+",
            "forbidden_claims": ["checkmate in two", "hangs the queen"]
        }
    ]
    
    with open(os.path.join(EVALS_DIR, "opening_prep.json"), "w") as fw:
        json.dump(opening_cases, fw, indent=2)
    print("Generated opening_prep.json")

    # 2. Scandinavian blitz & Carlsbad quiet -> middlegame_analysis.json
    scand_pgn = open(os.path.join(FIXTURES_DIR, "scandinavian_blitz.pgn")).read()
    res_scand = handle_analyze_pgn({"pgn": scand_pgn, "max_flags": 100})
    
    carlsbad_pgn = open(os.path.join(FIXTURES_DIR, "carlsbad_quiet.pgn")).read()
    res_carls = handle_analyze_pgn({"pgn": carlsbad_pgn, "max_flags": 100})
    
    h4_flag = next(f for f in res_scand["flags"] if f["move_san"] == "h4" and f["move_number"] == 21)
    b4_flag = next(f for f in res_scand["flags"] if f["move_san"] == "b4" and f["move_number"] == 13)
    b5_flag = next(f for f in res_carls["flags"] if f["move_san"] == "b5" and f["move_number"] == 10)
    
    top_h4 = get_top_feature(h4_flag)
    top_b4 = get_top_feature(b4_flag)
    top_b5 = "new_weak_squares"

    middlegame_cases = [
        {
            "name": "scandinavian_h4_blunder",
            "input": {
                "flagged_move": h4_flag,
                "config": {
                    "explanation_depth": 1,
                    "audience_rating": 1800
                }
            },
            "expected_primary_theme": top_h4,
            "expected_vocabulary_tier": "1800+",
            "forbidden_claims": ["hangs a piece", "promotes a pawn"]
        },
        {
            "name": "scandinavian_b4_blunder",
            "input": {
                "flagged_move": b4_flag,
                "config": {
                    "explanation_depth": 1,
                    "audience_rating": 1800
                }
            },
            "expected_primary_theme": top_b4,
            "expected_vocabulary_tier": "1800+",
            "forbidden_claims": ["blunders the queen", "allows back rank mate"]
        },
        {
            "name": "carlsbad_b5_quiet",
            "input": {
                "flagged_move": b5_flag,
                "config": {
                    "explanation_depth": 1,
                    "audience_rating": 1800
                }
            },
            "expected_primary_theme": top_b5,
            "expected_vocabulary_tier": "1800+",
            "forbidden_claims": ["blunder", "loses a pawn", "tactical threat"]
        }
    ]
    
    with open(os.path.join(EVALS_DIR, "middlegame_analysis.json"), "w") as fw:
        json.dump(middlegame_cases, fw, indent=2)
    print("Generated middlegame_analysis.json")

    # 3. Synthetic Endgame -> endgame_analysis.json
    endgame_cases = [
        {
            "name": "synthetic_king_activity_concession",
            "input": {
                "flagged_move": {
                    "move_san": "Kf2",
                    "move_number": 40,
                    "side": "white",
                    "phase": "endgame",
                    "wdl_delta": -0.18,
                    "best_move_san": "Ke3",
                    "pv": ["Ke3", "Ke7", "Kd4"],
                    "refutation_pv": ["Ke7", "Kd4"],
                    "channel": "wdl",
                    "feature_deltas": {
                        "king_safety_delta": -0.6
                    },
                    "synthetic": True
                },
                "config": {
                    "explanation_depth": 1,
                    "audience_rating": 1800
                }
            },
            "expected_primary_theme": "none",
            "expected_vocabulary_tier": "1800+",
            "forbidden_claims": ["checkmate threat", "blunders the rook"]
        },
        {
            "name": "synthetic_endgame_zugzwang",
            "input": {
                "flagged_move": {
                    "move_san": "a4",
                    "move_number": 45,
                    "side": "black",
                    "phase": "endgame",
                    "wdl_delta": -0.40,
                    "best_move_san": "Kh7",
                    "pv": ["Kh7", "Kf6", "Kh6"],
                    "refutation_pv": ["Kf6", "Kh6", "Kf7"],
                    "channel": "wdl",
                    "feature_deltas": {
                        "king_safety_delta": -0.7
                    },
                    "synthetic": True
                },
                "config": {
                    "explanation_depth": 1,
                    "audience_rating": 1800
                }
            },
            "expected_primary_theme": "none",
            "expected_vocabulary_tier": "1800+",
            "forbidden_claims": ["discovered check", "promotes a queen"]
        },
        {
            "name": "synthetic_endgame_pawn_race",
            "input": {
                "flagged_move": {
                    "move_san": "h5",
                    "move_number": 50,
                    "side": "white",
                    "phase": "endgame",
                    "wdl_delta": -0.40,
                    "best_move_san": "Kf4",
                    "pv": ["Kf4", "h4", "g4"],
                    "refutation_pv": ["h4", "g4", "Kf5"],
                    "channel": "wdl",
                    "feature_deltas": {
                        "pawn_structure_delta": -0.8
                    },
                    "synthetic": True
                },
                "config": {
                    "explanation_depth": 1,
                    "audience_rating": 1800
                }
            },
            "expected_primary_theme": "pawn_structure_delta",
            "expected_vocabulary_tier": "1800+",
            "forbidden_claims": ["loses the queen", "back rank mate"]
        }
    ]
    
    with open(os.path.join(EVALS_DIR, "endgame_analysis.json"), "w") as fw:
        json.dump(endgame_cases, fw, indent=2)
    print("Generated endgame_analysis.json")

if __name__ == "__main__":
    main()
