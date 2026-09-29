# Prophylax

**A positional chess second that reads your engine and explains your mistakes.**

Engines tell you *what* — a number. Prophylax tells you *why*. It ingests a PGN,
delegates every calculation to Stockfish over an MCP server, and translates the
engine's signals into tournament-level positional coaching: weak squares, pawn
structure, king safety, prophylaxis. The LLM never calculates a move. If a claim
has no engine signal behind it, Prophylax is not allowed to say it.

Built for the Kaggle **AI Agents: Intensive Vibe Coding Capstone** (Concierge
Agents track).


## Why this exists

A drop from +1.0 to +0.3 sounds mild in centipawns, which is what most chess players see when they load up a standard chess engine. It's actually 87% → 54% win
probability, or a third of the game handed back in one move. Centipawns lie about
practical consequences, so Prophylax measures every move in **win probability
(WDL)**, banded by game phase.

And some real mistakes never show up in the eval at all. For example, in the Exchange QGD,
10...b5 costs about 0.2 — in engine terms, this is barely noticeable. But it concedes c5 permanently and fixes c6 as a backward-pawn target. Prophylax's **quiet channel** flags it anyway, from deterministic structural analysis, and cites the engine's own a4! plan as
evidence. Drop-detectors cannot see this move by construction. That is the
product.

## Architecture
PGN ──▶ PreToolUse sanitizer ──▶ Root agent (google-adk)
(deterministic)        │  intent routing / session state
▼
MCP server (stdio, JSON-RPC 2.0)
pinned Stockfish · analyze_pgn / analyze_position
Channel 1: WDL drops (5%/8%/10% by phase)
Channel 2: quiet structural concessions
│  flags, ranked feature deltas,
│  refutation PV
▼
Phase skills (.agents/skills/, progressive disclosure)
one shared contract + opening · middlegame · endgame sections
│
▼
Deterministic harness around the LLM:
code-emitted report skeleton · narration validator
(SAN legality, payload whitelist) · retry-once-then-
fallback — a fabricated move never ships

- **MCP server** (`mcp_server/`): stateless JSON-RPC 2.0 over stdio wrapping a
  pinned Stockfish. Two tools; all features computed deterministically with
  python-chess. Returns data only — it never narrates.
- **Skills** (`.agents/skills/`): one shared narration contract plus a phase
  section per skill (opening, middlegame, endgame). One theme per flagged move = the top-ranked feature delta. No signal,
  no claim.
- **Agent** (`app/`): one narrator definition, specialised per flag by the
  server's phase field; flags are narrated concurrently. Move questions
  (`15.Bd3`, "move 15 white") are parsed deterministically, with an LLM router
  only as fallback; `ask_about_move` deep-dives any move on request
  (`analyze_pgn → analyze_position`, strictly in order).
- **Fail-closed narration**: every narration is validated against the payload
  (move legality, square whitelist, no internal units). One retry, then a
  deterministic fallback built from payload fields only.

## Setup

Requirements: Python 3.11+, a local Stockfish binary, a Gemini API key.

```bash
git clone <repo> && cd prophylax
python3 -m venv venv && ./venv/bin/pip install -e .
brew install stockfish        # or your platform's package manager

export STOCKFISH_PATH=/opt/homebrew/bin/stockfish
export STOCKFISH_NODES=1000000          # pinned eval budget
export GEMINI_API_KEY=<your key>        # never committed, never in code
```

The engine version is pinned in `config/settings.py`; startup fails loudly on a
mismatch. Narrator models are certified: only models that have passed the
narration gate may ship (`APPROVED_NARRATORS`), with automatic quota fallback.

## Usage

```bash
# full game report (top flags, both channels)
STOCKFISH_PATH=... STOCKFISH_NODES=1000000 python3 -m app.agent --pgn mygame.pgn --max-flags 4

# conversational coach (paste a PGN, then ask "what about 11...Bxc4?")
adk web
```

Raw chess.com/lichess exports are fine — headers, comments, and variations are
sanitized at a single choke point before anything reaches the model or engine.

## Evaluation-driven development

The answer key existed before the features did. See `tests/` and `evals/`:

- **Golden fixtures** from real games, with WDL bands *measured* on the pinned
  rig (Stockfish 18, 1M nodes, single thread) — recorded by script, never
  hand-typed (`tests/verify_goldens.py`, `--record` guarded by
  `--force-rerecord`).
- **Stability gate**: every verdict must survive 3× deeper search. It rejected
  two shallow-search mirages from the answer key during development.
- **Trajectory evals**: tool-call order asserted (`analyze_pgn` before
  `analyze_position`); the MCP server is asserted to run as a subprocess.
- **LLM-as-Judge** (a different model than the narrator), calibrated on a real
  captured failure: a narration that misread a knight-trapping line as a king
  attack must fail; the correct narration of the same move must pass.

```bash
./venv/bin/pytest -v                 # fast suite (no engine, no LLM)
pytest -v -m golden                  # engine-backed fixtures (~10 min)
pytest -v -m narration               # narrator gate (LLM)
pytest -v -m judge                   # judge calibration (two LLMs)
```

## Security

PGN metadata is untrusted input. A deterministic PreToolUse hook whitelists
headers to the tag roster, anonymizes player names, strips comments and
variations (the injection surface), and validates FENs — at one choke point, so
no tool can be reached unsanitized. A hostile fixture with injection strings in
tags and comments is part of the fast suite: the strings provably do not
survive.

## Scope and roadmap

Prophylax is a post-mortem coach: it diagnoses errors. Asked about a non-error,
it reports the move fell below the flagging thresholds and shows the engine's
preference. Strong-move narration is signal-ready but unbuilt. Semantic misreads
are measured by the offline judge rather than blocked at runtime; runtime
judging is the deployment-hardening step. Endgame evals are synthetic pending a
real endgame fixture; uncastled-king safety is a known feature gap.
