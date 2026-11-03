# After the held-out evaluation

Work deliberately postponed until the blind held-out result is frozen and reported. Doing any of it
sooner would change the system under test, or the commit hashes the experiment refers to.

## 1. Fix the known extractor bugs

Turn each bug in [`SEMANTIC_ISSUES.md`](SEMANTIC_ISSUES.md) into a failing regression test, then fix it:

- A king move on the e-file to g or c read as castling on any rank (`e4-g4`).
- The stated colour of a captured piece dropped.

Delete `test_semantic_code_is_still_the_frozen_commit`, in the same change that unfreezes the code. Rerun the
development benchmark, and treat any later performance claim as needing fresh held-out outputs.

## 2. Purge personal game data from Git history

**Still required.** `games/my_games.pgn` left the tracked tree in the current `main`, but it remains
in public Git history, with opponents' usernames. So does a username that was hard-coded in
`scripts/dev/mine_inaccuracies.py`.

It's postponed because rewriting history changes every commit hash, including `046bb19`, which the
freeze, its manifest and the reports refer to.

When the experiment is reported:

1. Archive the report and manifest, and record the old-to-new hash for `046bb19` beside them.
2. Rewrite history with `git filter-repo --invert-paths --path games/my_games.pgn`, and use `--replace-text` for the username.
3. Update `FROZEN_COMMIT` in `evals/queen_heldout/freeze_predictions.py` and any written references.
4. Force-push `main` after checking that nothing else depends on the old hashes. Anyone with a clone must re-clone.
5. Ask GitHub to clear cached views if the old commits remain reachable by hash. Forks and earlier clones cannot be recalled.

Check the result with `git log --all -- games/my_games.pgn` (expect nothing).

## 3. Decide on production integration

See [`QUEEN_INTEGRATION_DESIGN.md`](QUEEN_INTEGRATION_DESIGN.md). Its thresholds should be agreed before the result is read.
