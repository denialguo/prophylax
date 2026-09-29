"""JSON Schemas for the MCP tool results. Checked against the real server's output
and against the mocks the agent tests feed in, so mocks can't drift from reality.
Required fields are the ones the agent and prompt code read."""
SQUARES = {"type": "array", "items": {"type": "string", "pattern": "^[a-h][1-8]$"}}
SANS = {"type": "array", "items": {"type": "string"}}
SIDE = {"enum": ["white", "black"]}
PHASE = {"enum": ["opening", "middlegame", "endgame"]}
WDL = {"type": ["object", "null"], "required": ["wins", "draws", "losses"],
       "properties": {k: {"type": "integer"} for k in ("wins", "draws", "losses")}}

FLAG = {
    "type": "object",
    "required": ["move_san", "move_number", "side", "phase", "wdl_delta", "best_move_san",
                 "pv", "refutation_pv", "feature_deltas", "concessions", "channel"],
    "properties": {
        "move_san": {"type": "string", "minLength": 1},
        "move_number": {"type": "integer", "minimum": 1},
        "side": SIDE,
        "phase": PHASE,
        "wdl_before_prob": {"type": "number", "minimum": 0, "maximum": 1},
        "wdl_after_prob": {"type": "number", "minimum": 0, "maximum": 1},
        "wdl_delta": {"type": "number", "minimum": -1, "maximum": 1},
        "best_move_san": {"type": "string"},
        "pv": SANS,
        "refutation_pv": SANS,
        "feature_deltas": {"type": "object", "additionalProperties": {"type": "number"}},
        "concessions": {"type": "object", "additionalProperties": SQUARES,
                        "propertyNames": {"enum": ["new_weak_squares", "new_backward_pawns",
                                                   "new_fixed_backward_pawns", "new_pawn_unsupported"]}},
        "channel": {"enum": ["wdl", "quiet_inaccuracy"]},
    },
}

MOVE_EVAL = {
    "type": "object",
    "required": ["move_san", "move_number", "side", "phase", "wdl_delta", "best_move_san"],
    "properties": {k: FLAG["properties"][k] for k in
                   ("move_san", "move_number", "side", "phase", "wdl_delta", "best_move_san")},
}

ANALYZE_PGN = {
    "type": "object",
    "required": ["flags", "move_evals", "summary"],
    "properties": {
        "flags": {"type": "array", "items": FLAG},
        "move_evals": {"type": "array", "items": MOVE_EVAL},
        "summary": {"type": "object", "required": ["total_flags"],
                    "properties": {"total_flags": {"type": "integer", "minimum": 0},
                                   "phase_distribution": {"type": "object"},
                                   "final_wdl": WDL}},
    },
}

_SIDES = lambda schema: {"type": "object", "required": ["white", "black"],
                         "properties": {"white": schema, "black": schema}}
ANALYZE_POSITION = {
    "type": "object",
    "required": ["multipv_lines", "features"],
    "properties": {
        "multipv_lines": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["pv", "wdl"], "properties": {"pv": SANS, "wdl": WDL}}},
        "features": {"type": "object", "required": ["weak_squares", "pawn_structure"], "properties": {
            "weak_squares": _SIDES({"type": "array", "items": {
                "type": "object", "required": ["square", "complex"],
                "properties": {"square": {"type": "string", "pattern": "^[a-h][1-8]$"},
                               "complex": {"enum": ["light", "dark"]}}}}),
            "pawn_structure": _SIDES({"type": "object", "properties": {
                k: SQUARES for k in ("backward_pawns", "isolated_pawns", "doubled_pawns", "fixed_pawns")}}),
        }},
    },
}


EVAL_KEYS = ("move_san", "move_number", "side", "phase", "wdl_delta", "best_move_san")


def pgn_payload(movetext: str, flags: list = ()) -> dict:
    """A complete analyze_pgn result, shaped like the real server's, around hand-written
    flags: every ply gets a move_evals entry; flagged plies copy their flag's values.
    Agent-test mocks use this so the domain converter sees what production sees."""
    import copy
    import io
    import chess.pgn
    import jsonschema
    game = chess.pgn.read_game(io.StringIO(movetext))
    board, evals = game.board(), []
    by_move = {(f["move_number"], f["side"]): f for f in flags}
    for move in game.mainline_moves():
        side = "white" if board.turn else "black"
        f = by_move.get((board.fullmove_number, side))
        evals.append({k: f[k] for k in EVAL_KEYS} if f else
                     {"move_san": board.san(move), "move_number": board.fullmove_number, "side": side,
                      "phase": "opening", "wdl_delta": 0.0, "best_move_san": board.san(move)})
        board.push(move)
    phases = {}
    for f in flags:
        phases[f["phase"]] = phases.get(f["phase"], 0) + 1
    payload = {"flags": [copy.deepcopy(f) for f in flags], "move_evals": evals,
               "summary": {"total_flags": len(flags), "phase_distribution": phases}}
    jsonschema.validate(payload, ANALYZE_PGN)
    return payload


def position_payload(*pvs: list) -> dict:
    """A complete analyze_position result with the given engine lines and no features."""
    import jsonschema
    payload = {"multipv_lines": [{"pv": list(pv), "wdl": None} for pv in pvs] or [{"pv": [], "wdl": None}],
               "features": {"weak_squares": {"white": [], "black": []},
                            "pawn_structure": {"white": {}, "black": {}}}}
    jsonschema.validate(payload, ANALYZE_POSITION)
    return payload
