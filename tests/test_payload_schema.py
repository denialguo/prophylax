"""M9: the real server's output and the agent-test mocks satisfy one schema."""
import json
import os
import shutil

import jsonschema
import pytest

from mcp_server.server import handle_line
from tests.payload_schema import ANALYZE_PGN, ANALYZE_POSITION, FLAG

HAS_ENGINE = bool(os.environ.get("STOCKFISH_PATH") and shutil.which(os.environ["STOCKFISH_PATH"]))
# Carlsbad opening: exercises both flag channels and every concession kind
PGN = open(os.path.join(os.path.dirname(__file__), "fixtures", "carlsbad_quiet.pgn")).read()


def call(tool, args):
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    res = handle_line(json.dumps(req))  # through the real JSON-RPC layer
    assert "result" in res, res
    return json.loads(res["result"]["content"][0]["text"])


@pytest.mark.skipif(not HAS_ENGINE, reason="STOCKFISH_PATH not configured")
def test_real_server_output_matches_schema(monkeypatch):
    monkeypatch.setenv("STOCKFISH_NODES", "5000")  # small budget: shape, not strength
    pgn = call("analyze_pgn", {"pgn": PGN, "max_flags": 50})
    jsonschema.validate(pgn, ANALYZE_PGN)
    assert pgn["flags"], "fixture produced no flags, so the flag schema went unchecked"
    fen = "r1bqr1k1/p2nbppp/2p2n2/1p1p2B1/3P4/2NBP3/PPQ1NPPP/R4RK1 w - - 0 11"
    jsonschema.validate(call("analyze_position", {"fen": fen, "multipv": 3}), ANALYZE_POSITION)


def test_agent_test_mocks_match_schema():
    from tests import test_grounding, test_prompt_injection, test_agent_structure
    for flag in [test_grounding.FLAG, test_prompt_injection.FLAG, *test_agent_structure.FLAGS]:
        jsonschema.validate(flag, FLAG)
    jsonschema.validate(test_grounding.POSITION, ANALYZE_POSITION)
