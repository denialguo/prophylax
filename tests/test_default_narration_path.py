"""D12: the production default (claims) end to end: real Stockfish over MCP, real narrator,
no PROPHYLAX_NARRATION_INPUT override. tests/test_narration.py's gate builds the flag
prompt directly, so it covers the flag path only."""
import os
import re
import shutil

import pytest

import app.agent as agent_mod
from config.settings import get_narration_input

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "scandinavian_blitz.pgn")
HAS_ENGINE = bool(os.environ.get("STOCKFISH_PATH") and shutil.which(os.environ["STOCKFISH_PATH"]))


@pytest.mark.narration
@pytest.mark.skipif(not HAS_ENGINE, reason="needs STOCKFISH_PATH")
def test_cli_report_on_the_default_path(monkeypatch, capsys):
    monkeypatch.delenv("PROPHYLAX_NARRATION_INPUT", raising=False)
    assert get_narration_input() == "claims"
    built = []
    real_build = agent_mod.build_claims
    monkeypatch.setattr(agent_mod, "build_claims", lambda *a, **k: built.append(a[0].ply) or real_build(*a, **k))

    assert agent_mod.main(["--pgn", FIXTURE, "--max-flags", "4"]) == 0  # a ClaimConsistencyError would fail here
    out, err = capsys.readouterr()
    summary = re.search(r"(\d+) narrations attempted / (\d+) passed first try / (\d+) passed on retry / (\d+) fell back", err)
    attempted, first, retry, fallback = map(int, summary.groups())
    print(f"\n{summary.group(0)}\n{out}")
    assert attempted == len(built) > 0  # every narration went through the claims builder
    assert fallback == 0 and first + retry == attempted
