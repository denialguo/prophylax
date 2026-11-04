"""keep_trying.sh against a stub `uv` that exits with scripted codes; no network, no tokens."""
import os
import subprocess

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "evals", "queen_heldout", "keep_trying.sh")
STUB = """#!/usr/bin/env bash
echo "$HF_TOKEN" >> "$CALLS"
n=$(wc -l < "$CALLS")
exit $(sed -n "$((n))p" "$CODES")
"""


def run(tmp_path, codes, **extra):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text(STUB)
    (bin_dir / "uv").chmod(0o755)
    (tmp_path / "codes").write_text("\n".join(map(str, codes)) + "\n")
    (tmp_path / "env").write_text("HF_TOKEN=main\nHF_TOKEN_SCHOOL=school\n")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(tmp_path / "calls"), "CODES": str(tmp_path / "codes"),
           "ENV_FILE": str(tmp_path / "env"), "RETRY_SECONDS": "0", **extra}
    result = subprocess.run(["bash", SCRIPT], env=env, capture_output=True, text=True, timeout=30)
    return result.returncode, (tmp_path / "calls").read_text().split()


def test_quota_rotates_tokens_and_retries_until_complete(tmp_path):
    assert run(tmp_path, [3, 3, 3, 0]) == (0, ["main", "school", "main", "school"])


def test_third_token_is_tried_when_set(tmp_path):
    assert run(tmp_path, [3, 3, 0], HF_TOKEN_FRIEND="friend") == (0, ["main", "school", "friend"])


def test_changed_revision_stops_at_once(tmp_path):
    assert run(tmp_path, [4, 0]) == (4, ["main"])


def test_repeated_errors_stop_but_a_quota_refusal_resets_the_count(tmp_path):
    assert run(tmp_path, [1, 3, 1, 1, 0], MAX_ERRORS="2") == (1, ["main", "school", "main", "school"])
