# Prophylax

**A positional chess coach that explains your mistakes from engine facts, and finds the ones you keep repeating.**

Stockfish gives you a number; Prophylax tells you what the move gave away. Every
chess fact comes from Stockfish and python-chess. The language model only puts
facts it has been handed into words, and deterministic checks reject anything
else before you see it.

## Architecture

```
PGN (Chess.com / Lichess export, untrusted)
 │  sanitizer: headers whitelisted, names anonymised, comments/variations stripped
 ▼
MCP server (JSON-RPC over stdio) ── Stockfish 18, pinned: 1 thread, 16 MB hash, fixed nodes
 │  per position: win/draw/loss + principal variation            (the only engine call)
 │  per move: win-probability drop, phase, python-chess features (weak squares,
 │  backward / unsupported pawns, king shelter), flags:
 │    Channel 1  win-probability drop beyond a phase band (5 / 8 / 10 %)
 │    Channel 2  quiet structural concession the evaluation barely registers
 ▼
typed analysis (pydantic): payload checked against the replayed game; fails closed
 ▼
verified coaching claims: one per fact, each carrying its evidence and provenance
 (engine / server features / board), re-checked against the board
 ▼
LLM narration (Gemini or Groq, certified models only)
 ▼
fail-closed validation: every move legal at its ply, every square and line from the
 payload, precise terms only where a claim backs them → one retry → deterministic fallback

          +

history (SQLite, your own games)
 │  stored engine searches → the same derivation code → MistakeEvents
 ▼
deterministic recurring-weakness profile (counts and medians, no model)
```

## Why the chess and the language are separated

A language model asked about a chess position will confidently invent lines,
misattribute moves and name weaknesses that aren't there. So Prophylax never
lets it calculate anything.

- **Facts are deterministic and testable.** Stockfish supplies evaluations and
  lines. python-chess derives the structure. Both are pinned, so the same game
  gives the same facts every time, and they're pinned by golden tests.
- **Claims are the contract.** Each claim ("13.b4 created a weak square on a3")
  is built from one signal, with its evidence. A 29-case hand-labelled benchmark
  checks the claim builder against definitions written before the code was
  run: precision and recall are 1.0 on every type.
- **The model only phrases.** Its output is checked by code. If it cites a move
  that isn't legal at that point, a square the facts don't mention, or a term
  like "backward pawn" with no claim behind it, the narration is rejected,
  retried once, and otherwise replaced by the claim sentences themselves.

## Example 1: one game

Scandinavian, move 13. White plays **13.b4**. Stockfish at 1M nodes: White's
win probability drops from 84% to 52%. The verified claims for that move:

```
13.b4 dropped White's win probability from 84% to 52% in the engine's evaluation.
The engine preferred 13.cxd4, with the line 13.cxd4 13...exd4 14.Be2 14...Bxg3 15.hxg3 15...c5.
The engine's refutation of 13.b4 is 13...a5 14.bxa5 14...Nc6 15.Nf5 15...dxc3 16.Bxc3.
13.b4 created a weak square on a3: no White pawn can ever guard it again.
After 13.b4, no White pawn can ever support White's c3 pawn. Black's pawn on d4 attacks it.
```

Each claim carries its evidence. For example, "a3 was guarded by the b2 pawn
that moved" and "Black's d4 pawn attacks c3" are recomputed from the board and
must agree with the engine server's facts, or no claim is made.

A narration of another flagged move in the same game, from a real run
(`python -m app.agent --pgn tests/fixtures/scandinavian_blitz.pgn`):

> The move 21.h4 constitutes a severe strategic error that compromises white's
> king safety. The refutation line 21...Nxf3 22.Kxf3 22...h5 23.Kg2 23...g6
> 24.Nh6+ demonstrates how black immediately exploits the weakened kingside
> structure.

## Example 2: your own history

`python -m history profile`, over 250 of my own rated Chess.com blitz and rapid
games (analysed at 100k nodes, about 29 minutes for the whole history):

```
Your last 20 eligible games (2026-01-02 to 2026-01-14, blitz, rated 1419-1453)
Based on engine-flagged moves, Stockfish at 100,000 nodes per position (analyzer a1, claims c1).

Weak squares created
  14 of your last 20 games (25 flagged moves: 9 in the opening, 10 in the middlegame, 6 in the endgame)
  median win-probability loss: 10.3 points (range 0 to 99.7)
  9 of the 25 were quiet concessions: flagged for the structure, not for an immediate drop in the evaluation
  you did this on 72 moves in those games; 25 of them were engine-flagged
  previous 20 games: 8 games

Backward pawns created
  10 of your last 20 games (15 flagged moves: 2 in the opening, 7 in the middlegame, 6 in the endgame)
  median win-probability loss: 2 points (range 0 to 49.8)
  9 of the 15 were quiet concessions: flagged for the structure, not for an immediate drop in the evaluation
  you did this on 20 moves in those games; 15 of them were engine-flagged
  previous 20 games: 3 games

Flagged moves with no structural pattern (tactical or other): 100 moves in 20 games (not a positional pattern).
```

- **Only engine-flagged moves count.** A weak square appears in 93% of games,
  so a raw count would say nothing. The raw structural events appear only as
  the "you did this on N moves" base rate.
- **Everything is a count, a distinct-game count or a median.** There is no
  weakness score. One chaotic game counts once.
- **Eligibility is explicit and recorded per game.** Of 350 source games,
  250 are profiled. 100 are excluded: 50 bullet, 24 abandoned, 14 under 20
  plies, 6 containing a null move, and 6 not the player's own.
- **Nothing is re-searched when the logic changes.** Engine searches are
  stored once with their provenance (engine, nodes, threads, hash). When the
  claim or detection code changes, a version bump re-derives the whole history
  from the stored searches in seconds, and a test fails if covered code changes
  without a bump.

## Testing and evaluation

- **Fast suite:** 444 tests, about 12s, offline. It covers:
  - the domain models and conversion
  - claims in both colours and in mirrored positions
  - the validators and the prompt contracts
  - the history schema, import, derivation and profile statistics
  - payload parity, from recorded engine searches
- **Golden tests:** engine-backed at the pinned 1M nodes, with WDL bands
  measured, not hand-typed. They include a live check that the refactored
  server returns exactly its pre-refactor output.
- **Claim benchmark:** 29 hand-labelled positions (fixtures, mirrors, targeted
  edge cases, real endgames from my games), approved by review before any
  label was asserted. Labels are never generated by the builder.
- **Narration gate and LLM-as-judge:** the judge is a different model from the
  narrator and is calibrated on a captured real failure.
- **Pre-registered A/B tests for prompt changes:** paired, seeded runs
  alternating which arm goes first, with a switching rule written before the
  data. Provider outages are logged and rerun, never counted. The first
  comparison of claim-grounded narration against the default kept the default:
  the rule said so, and the losing variant was not tuned afterwards to win.

```bash
uv run pytest                    # fast suite (offline)
uv run pytest -m golden          # engine-backed, needs Stockfish (~2 min)
uv run pytest -m narration       # narrator gate (API key)
uv run python -m evals.claim_metrics   # claim benchmark + narration coverage
```

## Running it

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Stockfish 18, and
a Gemini or Groq API key (both have free tiers).

```bash
git clone <repo> && cd prophylax && uv sync
export STOCKFISH_PATH=$(which stockfish) STOCKFISH_NODES=1000000
export GEMINI_API_KEY=...            # or put keys in app/.env (git-ignored)

# one game: a report on the flagged moves
uv run python -m app.agent --pgn mygame.pgn --max-flags 4

# conversational coach (paste a PGN, then ask "what about 13.b4?")
uv run adk web

# your history: import, analyse at 100k nodes, profile
export PROPHYLAX_PLAYER_NAMES=YourChessComName
uv run python -m history import --pgn games/my_games.pgn
uv run python -m history analyze          # ~7 s per game; resumable
uv run python -m history profile          # --include-bullet, --tc rapid, --since 2025-10-01, --json
```

The history database lives at `data/prophylax_history.sqlite3` (git-ignored;
`PROPHYLAX_HISTORY_DB` overrides it). Your PGN files are only ever read.

## Honest limitations

- **The default narration path can misuse terms.** In the example game, the
  default path's 13.b4 narration called a3 an "outpost" (no Black pawn
  attacks it) and c3 "isolated" (it isn't). The claim-grounded path rejects
  exactly these, but it didn't win its pre-registered comparison, so it isn't
  the default.
- **Most mistakes are unclassified.** 1,239 of 1,553 flagged moves have no
  structural category. They're tactical or not yet a defined concept (for
  example, pawn races). Prophylax reports them as such and never guesses a
  category.
- **History uses 100k-node analysis.** On a 6-game check, 100k agreed with 1M
  on 89% of flagged moves, but on only 76% of best moves. So profile counts
  are labelled with their budget, and single positions should be re-checked at
  1M.
- **Coverage gaps:**
  - King safety is a pawn-shelter heuristic. It ignores uncastled kings and
    is switched off in endgames, where it measured king activity, not danger.
  - The exports have no clock data, so time trouble can't be told apart from
    misunderstanding.
- **Scope.** Single player and command-line only. Cross-game conversational
  retrieval and training positions built from your own mistakes are designed
  (`PHASE_C_PLAN.md`) but not built.

`ARCHITECTURE.md` has the full pipeline, configuration and known limits.
