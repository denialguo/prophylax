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
  domain/convert.py: payload → GameAnalysis, checked against the replayed
    game; anything malformed or mismatched fails the turn (PayloadError)
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

## Player history (M15, M16)

```
games/*.pgn (read only)
  │ history/importer.py: strict header parse → typed columns; sanitized movetext only;
  │ explicit exclusion_reason (not_player, null_move, abandoned, too_short, bullet, …)
  ▼
games ──▶ analyze_pgn(include_searches) through the MCP server at 100k nodes
  │       → engine_runs + engine_positions: per-position WDL + PV, with provenance
  │         (engine, nodes, Threads, Hash); immutable, stored once per configuration
  ▼
history/derive.py: stored searches → derive_game_payload (the live server code)
  → game_analysis_from_payload → build_claims       (no engine; re-run on a version bump)
  → move_analyses, claims, mistake_events + mistake_categories
  ▼
history/profile.py: recurring weaknesses over MistakeEvents (last 20 eligible games,
  3+ distinct games, previous-20 trend, phase split, median loss, created→flagged rate)
```

- **One derivation path.** `handle_analyze_pgn` is `search_game` (the only engine
  call) then `derive_game_payload` (pure). History re-derives stored searches
  through the same function; `tests/test_derive_parity.py` proves the split kept
  the payload identical (offline from recorded searches, and live at 1M).
- **MistakeEvent** = a move by the profiled player that the existing pipeline
  flagged (Channel 1 or 2). Its category is the move's structural claim types
  (`weak_square_created`, `backward_pawn_created`, `pawn_support_lost`,
  `king_safety_reduced`), else `unclassified`. Raw structural changes are only
  the denominator of the created→flagged rate.
- **Staleness.** A derivation whose `ANALYZER_VERSION` / `CLAIMS_VERSION` differs
  from the code is re-derived from stored searches (`python -m history
  rederive`, seconds, no Stockfish). A change to covered code fails
  `tests/test_versions.py` until the version (or, for a pure refactor, the
  recorded hash) is updated.
- **Eligibility is explicit** (`games.exclusion_reason`), recomputed on every
  import; storage never implies profile inclusion.

## Components

| Path | Role |
|---|---|
| `app/agent.py` | Root ADK agent: routing, narration harness, MCP client, CLI (`main`). |
| `mcp_server/server.py` | JSON-RPC 2.0 over stdio. Owns Stockfish. Returns data, never prose. |
| `mcp_server/features.py` | Static features: king safety, pawn structure, weak squares, concessions. |
| `domain/` | Typed analysis models (pydantic) and the payload → model conversion boundary. |
| `hooks/sanitize_pgn.py` | PGN/FEN sanitisation before any tool call or prompt. A `[FEN]` start header is kept only after it parses as a board, and is re-emitted from that board. |
| `scripts/format_narration.py` | Builds narrator prompts, headers and numbered lines. |
| `scripts/move_reference.py` | Deterministic "which move?" parser. |
| `evals/validate_narration.py` | Grounding checks for narration and conversation. |
| `.agents/skills/` | `narration_contract.md` (shared) + one phase section per `SKILL.md`. |
| `config/` | Engine path, version pin, limits, timeouts, certified narrator models. |
| `domain/verify.py` | Semantic fact verifier: judges simple board and variation statements as VERIFIED / FALSE / UNKNOWN. Not wired into production. |
| `domain/extract.py` | Turns concrete statements in QUEEN's prose into typed candidates for the verifier, without judging them. Not wired into production. |
| `evals/queen_extraction.py` | Offline benchmark: stored QUEEN output → candidates → verifier. Corpus and labels in `evals/queen_benchmark/`. |
| `domain/versions.py` | `ANALYZER_VERSION` / `CLAIMS_VERSION` for stored history, with source-hash guards (`tests/test_versions.py`). |
| `history/` | Persistent player history (M15) and the recurring-weakness profile (M16); `python -m history`. |

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
remain in total; otherwise opening through move 9, then middlegame. Move
numbers are the board's own (a `[FEN]` start keeps its numbering).

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
- **Claims input (M13; the default since D12, `PROPHYLAX_NARRATION_INPUT=flag` for the payload path).** The narrator
  sees only the move's verified claims (`domain/claims.py`, one sentence each)
  plus `narration_contract_claims.md`. The validator grounds on the claims'
  moves and squares, and rejects "backward pawn", "weak square", "hole" or
  "outpost" when no claim of that kind exists. The fallback is the claim
  sentences. Game reports only; deep dives and conversation use the flag path.

## Semantic fact verifier

`evals/validate_narration.py` checks tokens: a square or move must be in the payload.
Two grounded squares do not make the stated relation between them true, so "the bishop
on c5 attacks g5" passes it. `domain/verify.py` checks the relation itself. It is a
separate layer with no prose parsing and no production caller yet; input is typed.

- `verify_fact(fen, Fact)` judges one statement. Predicates wrap python-chess: placement,
  attacks and defends, rays and blockers, pin geometry, open / semi-open / clear files,
  open diagonals, passed pawns, promotion distance, material, check, mate, stalemate,
  insufficient material, terminal state.
- `verify_variation(fen, [Ply])` replays typed moves and judges each ply's claims on that
  branch's board: legality, side to move, move number, piece and capture identity, the
  SAN's own `x`, `+` and `#`, material change, moves after the game has ended, and any
  facts attached to the ply.
- **UNKNOWN is not a pass.** No predicate exists for strategic ideas (traps, only moves,
  prophylaxis, reroutes, coordination), so they are UNKNOWN, as are malformed arguments,
  invalid boards, ambiguous SAN, and a promotion count with a piece on the path.
- **Definitions:** an open file has no pawns, though pieces may stand on it (`is_file_clear`
  is the stricter check). `attacks` is geometry; it ignores pins, and reports a pinned
  attacker in the evidence. A verified line is playable with true statements, not good.
- Engine lines can run past a dead draw: Stockfish keeps searching after insufficient
  material, and 3 of 1,553 stored history lines do this. The verifier rejects those plies.

## Claim extraction

`domain/extract.py` reads what a sentence claims; `domain/verify.py` decides whether it
holds. They stay separate: the extractor imports only the verifier's data types, and a false
claim is extracted as written (`"The bishop on c5 attacks g5."` → `attacks(c5, g5)` → FALSE).
It is a deterministic grammar over QUEEN's regular output format, with no LLM and no production
caller.

- **Extraction status** is `EXTRACTED`, `UNSUPPORTED`, `AMBIGUOUS` or `PARSE_ERROR`, never a
  verdict. Candidates keep the source id, sentence, character span, predicate and arguments.
- **Every fact is bound to a board.** Prose claims mostly describe hypothetical lines, so each
  candidate carries a replay prefix from the root. Moves attach by their number (counted from
  the root), then by legality. Several possible places with no number stop the line: the prefix
  is `None`, and the judge returns UNKNOWN rather than judging on the root.
- **Other guards against a false VERIFIED:** identities stated in the sentence become guard facts
  (`attacks` alone accepts an empty target). "Creates" and "opens" carry `change_from`, a fact that
  must not already have held. Negated, modal and planned clauses, and moves after words like
  "preparing", never become facts.
- **Not extracted, on purpose:** strategic language, and "eyes", "targets", "pressures" and net
  material ("wins a pawn"), where no predicate matches what the words mean. The audit counts some of
  these as false, so catching them needs new predicates or looser reading, not this layer.
- **Development/tuning set:** `python -m evals.queen_extraction`. 18 outputs and the audit, stored
  with SHA-256 hashes. The grammar was written against these outputs, so the scores describe this
  corpus, not new text. Labels in `gold.jsonl` are hand-drafted; `label_review.jsonl` logs every
  correction. An audit found labels describing the grammar's limits, and the fix moved exact fact
  matches from 83/83 to 83/106.
- **Held-out evaluation set:** `evals/queen_heldout/`, 24 other positions scored separately. The code
  under evaluation is frozen at `046bb19`, and `tests/test_queen_heldout.py` fails if it changes.
  The flow is `run_queen` → `freeze_predictions` → `build_labeling_package` → blind labels →
  `import_blind_labels` → `score` → `generate_report`. Predictions are written read-only, with
  hashes, before any label exists. The labelling package is standalone and contains nothing about
  how any software reads the texts.
- **Open bugs:** `docs/SEMANTIC_ISSUES.md`, left unfixed for the evaluation. Post-evaluation plan: `docs/QUEEN_INTEGRATION_DESIGN.md`.
- **Known failure:** 13 I.b (`white pawn c3-c4`) is illegal at the branch the text means, but it
  becomes legal later in the parent line. With no move number the extractor treats the branch point
  as unstated, so it comes out AMBIGUOUS and the illegal move is not reported.

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
| `PROPHYLAX_NARRATION_INPUT` | `claims` | `claims` narrates from verified claims only (chosen by D12: judge passes 27/27 vs 25/27, everything else level or within tolerance; theme subscore 30 vs 43). `flag` narrates from the server payload (alternate/debug path). |
| `JUDGE_MODEL` | `gemini-3.1-flash-lite` | Must differ from the narrator. |
| `GEMINI_API_KEY` | required for Gemini models | Never stored in the repo (`app/.env` is git-ignored). |
| `GROQ_API_KEY` | required for `groq/...` models | Provider-prefixed model names (e.g. `groq/llama-3.3-70b-versatile`) run through LiteLLM (`config.settings.resolve_model`). |
| `PROPHYLAX_HISTORY_DB` | `data/prophylax_history.sqlite3` | Application-owned history database (git-ignored; AGENTS.md rule 3). |
| `PROPHYLAX_PLAYER_NAMES` | none | The profiled player's usernames, comma-separated (or `--player`). |

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
| `pytest tests/test_history*.py tests/test_derive_parity.py tests/test_versions.py` | M15/M16 offline: schema, importer, derivation from recorded searches, versions, profile statistics. | nothing |
| `pytest tests/test_verify.py` | Semantic verifier: false relations from the QUEEN benchmark as board positions, with positive controls. | nothing |
| `pytest tests/test_queen_extraction.py` | Claim extraction and its benchmark on stored QUEEN output. | nothing |
| `pytest tests/test_queen_heldout.py` | Held-out experiment infrastructure, with the generator faked and the network blocked. | nothing |
| `pytest tests/test_verify_properties.py` | The verifier against python-chess and independent checks on seeded random games. | nothing |
| `python tests/verify_goldens.py` | Bands at 1M and decision stability at 3M. `--record` writes only missing bands, on operator instruction. | Stockfish |

Mocks used by the agent tests are validated against the same schema as the
real server output (`tests/payload_schema.py`).

## Known limits

For the frozen extractor and verifier, see `docs/SEMANTIC_ISSUES.md`.

- King safety ignores uncastled kings on the d/e files.
- `get_quiet_concessions` builds its square lists from sets, so their order in a
  live payload follows the process's hash seed (the stored history sorts them).
- Profile statistics rest on 100k-node analysis: on a 6-game check, 100k and 1M
  agreed on 89% of the player's flagged moves and 8 of 9 structural ones, but on
  only 76% of best moves (see `docs/plans/PHASE_C_PLAN.md` §3.2). King-safety events have no
  per-move base rate stored, so no created→flagged rate.
