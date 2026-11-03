"""Personal game data stays out of the tracked tree, and the license files stay in it."""
import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))


def tracked():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        pytest.skip(f"git unavailable: {e}")
    return out.splitlines()


def test_no_personal_pgn_is_tracked():
    assert not [f for f in tracked() if f.startswith("games/")]


def test_the_personal_pgn_location_is_ignored():
    done = subprocess.run(["git", "check-ignore", "-q", "games/my_games.pgn"], cwd=ROOT)
    assert done.returncode == 0


def test_the_benchmark_fixture_uses_synthetic_names_and_no_personal_metadata():
    with open(os.path.join(ROOT, "tests", "fixtures", "claim_benchmark_games.pgn"), encoding="utf-8") as f:
        text = f.read()
    assert set(re.findall(r'\[(?:White|Black) "(.+)"\]', text)) == {"Synthetic White", "Synthetic Black"}
    assert set(re.findall(r"^\[(\w+) ", text, re.M)) == {"Event", "White", "Black", "Result", "FEN", "SetUp"}


def test_license_and_notices_are_present_and_say_what_they_cover():
    with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as f:
        assert f.read().startswith("MIT License")
    with open(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), encoding="utf-8") as f:
        notices = f.read()
    assert "does not include its weights" in notices and "evals/queen_heldout/raw/" in notices and "GPL-3.0-or-later" in notices
