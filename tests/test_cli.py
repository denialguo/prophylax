"""M10: `python -m app.agent --pgn FILE --max-flags N` smoke test (MCP and LLM mocked)."""
from unittest.mock import patch

from app.agent import main
from tests.payload_schema import pgn_payload, position_payload

FLAG = {"move_san": "g4", "move_number": 2, "side": "white", "phase": "opening", "wdl_delta": -0.34,
        "best_move_san": "", "pv": [], "refutation_pv": [], "feature_deltas": {}, "concessions": {},
        "channel": "wdl"}


def test_cli_prints_report(tmp_path, capsys):
    pgn = tmp_path / "game.pgn"
    pgn.write_text('[Event "Club"]\n\n1. f3 e5 2. g4 Qh4# 0-1\n')
    calls = []

    async def mcp(tool, args):
        calls.append((tool, args))
        return pgn_payload("1. f3 e5 2. g4 Qh4# 0-1", [FLAG])

    async def llm(self, agent, prompt):
        return "The engine disliked it; it opens the e1-h4 diagonal."

    with patch("app.agent.call_mcp_tool_subprocess", new=mcp), \
         patch("app.agent.CoachingAgent._run_sub_agent", new=llm):
        code = main(["--pgn", str(pgn), "--max-flags", "2"])
    out = capsys.readouterr().out
    assert code == 0
    assert calls[0][0] == "analyze_pgn" and calls[0][1]["max_flags"] == 2
    assert "Game Report | Total Flags: 1" in out and "Move 2.g4 (White)" in out


def test_cli_missing_file(tmp_path, capsys):
    assert main(["--pgn", str(tmp_path / "nope.pgn")]) == 1
    assert "Cannot read" in capsys.readouterr().err


def test_cli_failed_turn_exits_nonzero(tmp_path, capsys):
    pgn = tmp_path / "game.pgn"
    pgn.write_text("1. e4 e5 *\n")

    async def mcp(tool, args):
        raise RuntimeError("boom")

    with patch("app.agent.call_mcp_tool_subprocess", new=mcp):
        assert main(["--pgn", str(pgn)]) == 1
    assert "went wrong" in capsys.readouterr().out.lower()
