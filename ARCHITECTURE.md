# Prophylax architecture

How a PGN becomes a coaching report, where each hard rule in `AGENTS.md` is
enforced, and what the tests cover. The code is the source of truth; file
references point to where each piece lives.

## Pipeline

```
user message (adk web, or python -m app.agent --pgn FILE)
  │
  ▼ app/agent.py  CoachingAgent._run_async_impl
  ├─ parses as a PGN with ≥1 move?  ──▶ analyze_game
  ├─ no game in session             ──▶ "No game loaded"
  └─ otherwise route():
       scripts/move_reference.py (15.Bd3, 11...Bxc4, "move 15 white")
       └─ nothing found → LLM intent_router (sees sanitized movetext only)
       ├─ a move      ──▶ ask_about_move
       └─ anything else ──▶ converse

analyze_game
  call_mcp_tool_subprocess("analyze_pgn")
    ├─ hooks/sanitize_pgn.py: header whitelist, names anonymised,
    │   comments/NAGs/variations stripped (the single choke point)
    └─ persistent MCP server (one per event loop, lock-serialized)
  mcp_server/server.py handle_analyze_pgn
    ├─ input bounds: ≤100k chars, ≤400 plies, max_flags ≤400
    ├─ search_game(): one search per position (N+1), cached per game
    ├─ per move: win-prob delta, phase, feature deltas, concessions
    │   (mcp_server/features.py, pure python-chess)
    └─ Channel 1 / Channel 2 flags, move_evals, summary
  narrate each flag (up to 4 concurrently, reported in game order):
    narrator_for(phase) → prompt (scripts/format_narration.py)
    → validator → retry once with the reason → deterministic fallback
  report + narration_stats → session state

ask_about_move
  replay to the ply, check the typed SAN matches the game
  call_mcp_tool_subprocess("analyze_position", fen, multipv=3)
  flag (stored, or built from move_evals) + engine alternatives
  + position facts → narrator → validator → retry → fallback

converse
  conversational agent over the report and flags
  → grounding check (stored flags + moves actually played) → retry → fallback
```

## Components

| Path | Role |
|---|---|
| `app/agent.py` | Root ADK agent: routing, narration harness, MCP client, CLI (`main`). |
| `mcp_server/server.py` | JSON-RPC 2.0 over stdio. Owns Stockfish. Returns data, never prose. |
| `mcp_server/features.py` | Static features: king safety, pawn structure, weak squares, concessions. |
| `hooks/sanitize_pgn.py` | PGN/FEN sanitisation before any tool call or prompt. |
| `scripts/format_narration.py` | Builds narrator prompts, headers and numbered lines. |
| `scripts/move_reference.py` | Deterministic "which move?" parser. |
| `evals/validate_narration.py` | Grounding checks for narration and conversation. |
| `.agents/skills/` | `narration_contract.md` (shared) + one phase section per `SKILL.md`. |
| `config/` | Engine path, version pin, limits, timeouts, certified narrator models. |

## Engine process model

- **One server, one engine.** The agent keeps one MCP server subprocess alive
  per event loop and sends requests one at a time. The server keeps one
  Stockfish (Threads 1, Hash 16 MB, `UCI_ShowWDL`).
- **Cold per request.** Each request uses a fresh python-chess game token, so
  Stockfish gets `ucinewgame` and a cleared hash. Results match a newly started
  engine; the golden suite runs on one shared engine to prove it.
- **Search budget.** Node-limited (`STOCKFISH_NODES`), one search per position.
  The best move and PV for a move come from the search of the position before
  it, so there is no second pass.
- **Cache.** `lru_cache` over a whole game's search sequence and over single
  positions, keyed by FEN/moves, node limit and engine path. A hit replaces the
  whole sequence, so it cannot change the hash-table history of later searches.
  Errors are never cached.
- **Timeouts.** Each search has a watchdog (`STOCKFISH_SEARCH_TIMEOUT_S`) that
  kills a hung engine; the next request reopens it. Each tool call has an
  agent-side deadline (`PROPHYLAX_TOOL_TIMEOUT_S`) that kills the server's
  process group, Stockfish included. A dead or timed-out server is respawned
  on the next call.
- **Startup checks.** The server verifies the exact Stockfish version and that
  `STOCKFISH_NODES` is set before it accepts requests.

## Detection

Win probability is `W + D/2` from Stockfish's WDL, from the mover's side.

**Phase:** endgame if no queens remain or at most 6 rooks, minors and pawns
remain in total; otherwise opening through move 9, then middlegame.

**Channel 1 (WDL drop):** the drop reaches the threshold for its phase:
moves 1–3 −20%, then opening −5%, middlegame −8%, endgame −10%.

**Channel 2 (quiet concession):** after move 3, the move doesn't gain win
probability and it creates both a new weak square and a new backward pawn.
Channel 2 flags rank below all Channel 1 flags.

**Features** (`mcp_server/features.py`):

- *Weak square (hole):* a square in the mover's half (ranks 3–4 for White,
  5–6 for Black) that no friendly pawn can ever guard again and that doesn't
  hold a friendly pawn.
- *Weak squares a pawn move gives up:* only the squares the moved pawn guarded
  directly before it moved, plus farther new holes an enemy pawn already
  attacks (outposts).
- *Backward pawn:* no friendly pawn behind it on an adjacent file, and the
  square ahead is attacked by more enemy pawns than friendly ones.
- *Pawn-unsupported (`new_pawn_unsupported`):* no friendly pawn stands behind
  it on an adjacent file, so no forward advance can ever support it. A neutral
  fact: it never triggers a flag, and the narrator only sees it when the pawn
  isn't also backward. See `docs/unsupported_pawn_question.md`.

## Narration harness

- **Contract.** `.agents/skills/narration_contract.md` holds the rules every
  phase shares; each `SKILL.md` holds its phase section. `make_narrator`
  builds each phase's narrator from both (trace names `analysing_openings`,
  `analysing_middlegames`, `analysing_endgames`).
- **Header by code.** The report header is built in code; a narration starting
  with "Move " is rejected.
- **Validator** (`evals/validate_narration.py`). Every cited SAN move must be in
  the payload (played, best, PV, refutation, engine alternatives), every bare
  square must be in the payload (those moves' squares, concessions, position
  facts), numbered moves must be legal at that ply of the real game, and
  internal magnitudes ("delta of −X") are banned.
- **Fail closed.** One retry that names the violation, then a deterministic
  fallback built only from payload fields. Counts per report are kept in
  session state as `narration_stats`.
- **Conversation** is held to the same grounding checks, against every stored
  flag plus the moves actually played.

## Where the hard rules are enforced

| Rule (AGENTS.md) | Enforcement |
|---|---|
| 1. Model never calculates | All evals, PVs and alternatives come from MCP results; the validator rejects any move not in the payload. |
| 2. Narrate only from signals | Validator square/move whitelist and legality check, on narration, deep dives and conversation. |
| 3. Read-only | The server only reads PGN/FEN and runs searches; the agent writes only session state. |
| 4. WDL, phase-banded | Channel thresholds in `handle_analyze_pgn`. |
| 5. Pinned engine | Exact version check at startup; node limit on every `go`; Threads 1 and Hash 16 MB pinned in config and sent whenever the engine's defaults differ (asserted over UCI in `tests/test_search_budget.py`). |
| 6. No hardcoded secrets or paths | Engine path, limits, models and keys come from the environment. |
| 7. PGN metadata untrusted | `sanitize_tool_input` before every tool call; prompts get `sanitized_movetext` only (`tests/test_prompt_injection.py`). |

## Errors

The server returns JSON-RPC codes: `-32602` invalid params, `-32001` engine
timeout, `-32002` engine crashed, `-32003` engine unavailable, `-32603`
internal error. The agent raises `ToolError` with the code and shows the user
a short message. Invalid input explains what was wrong; everything else hides
internals, which go to stderr. The CLI exits 1 when no report was produced.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `STOCKFISH_PATH` | required | Stockfish 18 binary. |
| `STOCKFISH_NODES` | required | Node limit per search (1,000,000 for goldens). |
| `STOCKFISH_SEARCH_TIMEOUT_S` | 60 | Watchdog for one search. |
| `PROPHYLAX_TOOL_TIMEOUT_S` | 900 | Agent-side deadline for one tool call. |
| `NARRATOR_MODEL` | `gemini-3.5-flash` | Must be in `APPROVED_NARRATORS`. |
| `JUDGE_MODEL` | `gemini-3.1-flash-lite` | Must differ from the narrator. |
| `GEMINI_API_KEY` | required for Gemini models | Never stored in the repo (`app/.env` is git-ignored). |
| `GROQ_API_KEY` | required for `groq/...` models | Provider-prefixed model names (e.g. `groq/llama-3.3-70b-versatile`) run through LiteLLM (`config.settings.resolve_model`). |

A narrator call that hits a 503 is retried once after 3 s; a persistent 503 or
a 429 falls back through the other `APPROVED_NARRATORS`, across providers.

Fixed bounds in `config/settings.py`: 100,000 PGN characters, 400 plies,
`max_flags` ≤ 400, `multipv` ≤ 5, Threads 1, Hash 16 MB.

## Tests

| Command | Covers | Needs |
|---|---|---|
| `pytest` | Everything below that runs offline, plus a real-server schema check at 5k nodes (skipped without Stockfish). | Stockfish for the schema check only |
| `pytest -m golden` | Engine fixtures with WDL bands at 1M nodes, including the `kp_opposition` endgame study. | Stockfish, ~2.5 min |
| `pytest -m narration` | Real narrator against `evals/skills/*.json`; records retries in junit properties. | API key |
| `pytest -m judge` | LLM-as-judge calibration. | API key |
| `python tests/verify_goldens.py` | Bands at 1M and decision stability at 3M. `--record` writes only missing bands, on operator instruction. | Stockfish |

Mocks used by the agent tests are validated against the same schema as the
real server output (`tests/payload_schema.py`).

## Known limits

- A PGN that starts from a position (`[SetUp]`/`[FEN]` headers) works when
  calling the server directly. Through the agent it fails: the sanitizer drops
  those headers, then hits an illegal move while re-exporting the game from the
  standard start, and the user sees the generic error message.
- `analyze_pgn` numbers moves from 1 regardless of the FEN's move number.
- King safety ignores uncastled kings on the d/e files.
