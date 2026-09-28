# Prophylax — Upgrade Plan

Status: **Approved. M0–M2 done (uncommitted); M3+ awaiting approval.**

| Milestone | State | Default suite after |
|-----------|-------|---------------------|
| M0 hygiene | done; stale `test_deep_dive_trajectory` mock fixed (operator option 1); personal PGNs moved to `games/` | — |
| M1 injection | done; `tests/test_prompt_injection.py` (verified failing without the fix) | — |
| M2 validator | done; `tests/test_validator.py`; D1 applied to `test_narration.py` | 48 passed, 16 deselected, 0.63 s, `artifacts/junit.xml` |

Baseline commit: `d5b5617`. Findings marked **[verified]** were reproduced by running
code; **[read]** means established by reading code only.

---

## 1. End-to-end trace (as the code actually runs today)

```
user message
  │
  ▼ app/agent.py  CoachingAgent._run_async_impl
  ├─ normalize_pgn_paste() + is_likely_pgn() regexes, chess.pgn.read_game()
  │    → "PGN intent" if it parses with ≥1 move, else "follow-up intent"
  │
  ├─ PGN intent ───────────────────────────────────────────────────────────────
  │   call_mcp_tool_subprocess("analyze_pgn")            app/agent.py:19
  │    
   ├─ hooks/sanitize_pgn.sanitize_tool_input()  (strips comments/NAGs/
  │     │   variations, whitelists headers, replaces unsafe White/Black names)
  │     └─ spawns `python mcp_server/server.py` PER CALL, writes one JSON-RPC
  │        tools/call line, reads one line, waits for exit. No timeout.
  │   mcp_server/server.py handle_analyze_pgn            server.py:94
  │     ├─ spawns Stockfish PER REQUEST (server.py:129), Threads=1, UCI_ShowWDL
  │     ├─ pass 1: search every position (N+1) at Limit(nodes=STOCKFISH_NODES)
  │     ├─ pass 2: RE-SEARCH every pre-move position for best move/PV
  │     │          (server.py:186) → ~2N searches total
  │     ├─ per move: win-prob delta (W + D/2), phase heuristic, feature deltas
  │     │   and quiet concessions (mcp_server/features.py, pure python-chess)
  │     ├─ Channel 1: WDL drop ≤ phase threshold; Channel 2: drop ≤ 0 AND new
  │     │   weak square AND new backward pawn
  │     └─ returns flags[:max_flags], move_evals, summary
  │   for each flag (sequential):
  │     phase → one of 3 ADK LlmAgents (instruction = SKILL.md body)
  │     prompt = scripts/format_narration.format_flag_for_llm(flag)
  │     narration → evals/validate_narration.validate_narration()
  │       fail → retry once → fail → deterministic fallback sentence
  │   report = code-built header + narration per flag → session state
  │
  └─ Follow-up intent ─────────────────────────────────────────────────────────
      no pgn_text in state → "No game loaded"
      LLM "intent_router" (narrator model) parses {move_number, side, san}
        prompt includes the stored RAW user PGN            app/agent.py:254
      ├─ move query → replay game to target ply, SAN mismatch guard,
      │   call_mcp_tool_subprocess("analyze_position", fen, multipv=3)
      │   → narrate existing flag (or a synthetic flag from move_evals) with
      │     the phase agent → validate → retry → fallback
      └─ otherwise → LLM "conversational" agent over report + flags JSON,
          output NOT validated
```

Evaluation surfaces: `tests/` (fast, mocked), `@golden` engine fixtures with WDL
bands (`tests/fixtures/*.json`, `tests/verify_goldens.py` 1M/3M stability gate),
`@narration` gate against `evals/skills/*.json`, `@judge` calibration
(`evals/judge_calibration.json`).

Baseline test run (this session): default suite **38 passed, 19 deselected,
2.83 s**, `artifacts/junit.xml`. Golden suite: see §7.

---

## 2. What is genuinely strong — preserve

1. **LLM never touches the engine.** Tool calls are made by deterministic code,
   not chosen by the model; sub-agents have no tools. This is the single best
   grounding decision in the repo. Keep it.
2. **WDL-based detection banded by phase** instead of centipawns (rule 4). Correct
   and differentiating.
3. **Channel 2 (quiet concessions)** from deterministic structure analysis. It's
   the product's differentiator (the 10...b5 Carlsbad case).
4. **Golden methodology**: bands recorded by script on a pinned rig, a 3M-node
   stability gate, negative cases (`1...d5` never flagged). Rare rigor.
5. **Fail-closed narration harness**: code-emitted headers, validate → retry →
   deterministic fallback. The shape is right. The validator itself is weak (§3.1).
6. **Model governance**: certified narrator list, judge ≠ narrator collision
   check, judge fails closed on unparsable output.
7. **Pure-function feature layer** (`features.py`) is easy to unit test.

---

## 3. Findings

Severity: **S1** = breaks a hard rule or produces wrong/unsafe output;
**S2** = reliability/perf; **S3** = debt/hygiene.

### 3.1 Grounding

| # | Sev | Finding |
|---|-----|---------|
| G1 | S1 | **Legality check never runs on real games.** [verified] `check_narration_moves_legality` looks the game up in `tests/fixtures/` (`validate_narration.py:20`) and returns `True` if the flag doesn't match a fixture. For any user game, `"After 2.Qxh7 White wins."` passes. |
| G2 | S1 | **SAN in prose escapes the square whitelist.** [verified] `extract_squares` uses `\b[a-h][1-8]\b`, so `Qxh7`/`Bb5` yield no squares, and unnumbered hallucinated moves pass every check. |
| G3 | S1 | **Conversational replies are ungrounded.** [read] The free-form `conversational` agent's output is never validated (`agent.py:358`). |
| G4 | S1 | **Production code fitted to a fixture.** [read] `features.py:98-102` hard-filters concessions when the move is literally `b4` ("must be within {a3, c3}"), matching `test_concession_squares_ranks`. That's test-fitting inside the feature layer. |
| G5 | S2 | **Deep dive discards its own engine data.** [read] `analyze_position`'s multipv lines and features never reach the narrator. For flagged moves the call is used only in the final fallback string. Unflagged moves get `refutation_pv: []` and no features, so there's almost nothing to narrate from. |
| G6 | S2 | Retry prompts don't show the model what was rejected or why (generic warning text), which lowers retry success. |

### 3.2 Security / untrusted input (rule 7)

| # | Sev | Finding |
|---|-----|---------|
| U1 | S1 | **Raw PGN reaches an LLM.** [verified] Session stores the unsanitized paste (`agent.py:164`); the intent-router prompt embeds it (`agent.py:254`). A `{SYSTEM: ...}` comment and an injected `[Event]` both reached the router prompt in a mocked run. The README's "single choke point" claim is false. |
| U2 | S2 | Only `White`/`Black` values are sanitized; `Event`, `Site`, `Round`, etc. pass through verbatim (and `Event` is echoed into server stderr logs). |
| U3 | S2 | No input bounds: PGN length / ply count, `max_flags`, `multipv` are unbounded (a 500-ply PGN = ~1000 searches at 1M nodes). |

### 3.3 Stockfish process management

| # | Sev | Finding |
|---|-----|---------|
| P1 | S1 | **No timeout anywhere on the analysis path.** [verified in python-chess 1.11.2 source] `SimpleEngine._timeout_for` returns `None` when `Limit.time` is unset, so node-limited searches can block forever. The agent's `await proc.stdout.readline()` (`agent.py:57`) also has no timeout. A hung engine hangs the whole agent. |
| P2 | S2 | **Double spawn per tool call.** Each call starts a Python interpreter, re-imports modules, then launches Stockfish and loads NNUE. |
| P3 | S2 | **~2× redundant search.** Pass 2 re-searches every position already searched in pass 1 (`server.py:186`). Golden bands depend only on pass-1 WDL, so removing pass 2 shouldn't move them (must be verified, M4). |
| P4 | S2 | Potential pipe deadlock: the agent reads stdout before draining stderr; a server writing >64 KB to stderr before responding blocks both. Use `communicate()`. |
| P5 | S2 | **Engine version pin is not enforced at runtime.** `config.verify_engine()` is only called from tests; the server checks the path only. The substring check `"18" in version_line` also matches e.g. `dev-20250118`. |
| P6 | S3 | `Hash` isn't pinned explicitly (relies on the default). `if nodes != current_limit` guards are tautological (`nodes` never changes). No test asserts the pinned node count is actually applied, which AGENTS.md requires. |
| P7 | S3 | `STOCKFISH_MOVETIME`/interactive branch of `get_limits` is dead: the server always calls `interactive=False`. |

### 3.4 Caching

| # | Sev | Finding |
|---|-----|---------|
| C1 | S2 | None exists, and an in-server cache is useless while the agent spawns a fresh server per call (P2). |
| C2 | — | **Determinism constraint** for any cross-request cache: within a request the TT is warm across consecutive positions, so a cache hit that skips a search changes TT state for later positions. Result: game B's numbers depend on whether game A ran first. Safe options: dedupe within a request, or cache whole-request results, and clear hash per request. See D3. |

### 3.5 Error handling

| # | Sev | Finding |
|---|-----|---------|
| E1 | S2 | Every failure becomes `"Analysis failed: {str(e)}"` (`agent.py:371`), which can include server stderr, tracebacks and paths. No distinction between bad input, engine failure, timeout and LLM quota. |
| E2 | S2 | Server maps every exception to `-32603`; invalid params should be `-32602`, and engine failures need their own code so the agent can retry or respawn. |
| E3 | S3 | The server's JSON-RPC loop logs and drops malformed requests without responding (client waits forever, compounding P1). |

### 3.6 Evaluation

| # | Sev | Finding |
|---|-----|---------|
| V1 | S1 | **Narration gate contradicts the validator.** `test_narration.py:102` requires the narration's first line to equal the header. The skill contract and `validate_narration` both reject narration starting with "Move ". As written, a case can't pass both. Not run this session (needs LLM credentials). See D1. |
| V2 | S2 | `test_trajectory.py` is fully mocked but marked `narration`, so the default suite skips the IN_ORDER trajectory assertions. |
| V3 | S2 | Test mocks drift from the real schema (`{"multipv": [...]}` vs real `multipv_lines`). No contract test pins the server's output shape. |
| V4 | S3 | Endgame evals are synthetic. The golden set has 3 games. The judge only runs offline. Nothing tracks the fallback rate across runs. |

### 3.7 Multi-agent architecture — is it justified?

**Mostly no.** The three "phase agents" are one prompt template with a swapped
paragraph (a `diff` of the SKILL.md bodies shows only the title, one vocabulary
line and the Phase Interpretation section differ). The routing is a Python
`if phase == …` in the agent, not the model reading skill descriptions, so
"progressive disclosure" and the routing-oriented `description` frontmatter do
nothing at runtime. The intent router is an LLM call for parsing "move 15 white",
which a regex covers deterministically in the common case.

Recommendation: **one narrator agent** whose prompt is `shared contract +
phase section`, with the phase sections loaded from the existing skill files
(the files stay as the source of truth, duplication goes away). Put a
**deterministic move-reference parser** first and keep the LLM router only as a
fallback. The root `CoachingAgent`, a deterministic orchestrator, is the right
design and stays. This touches AGENTS.md conventions → D4.

### 3.8 Debt / hygiene

- No `.gitignore`: tracked `__pycache__/` (40+ files), `app/.adk/session.db`
  (session data, possibly personal), `artifacts/junit*.xml`, `my_games.pgn`.
- Root clutter: `run_analysis.py`, `run_eval_debug.py`, `run_hostile.py`,
  `scratch.py`, `test_ask.py`, `test_mcp.py`, `test_multipv.py`,
  `mine_inaccuracies.py` (uncommitted diff with hard-coded usernames). Root
  `test_*.py` files aren't in `tests/` but are collected by pytest.
- README documents `python3 -m app.agent --pgn …`; **there is no `__main__`**.
- `agent.py`'s 250-line `_run_async_impl` with nested helpers and in-function
  imports; flags narrated sequentially (latency scales with flag count).
- `get_game_phase`: any queen trade ⇒ "endgame" (even at move 6); material count
  includes pawns. That's a design choice, but it should be documented and tested.
- Agent injects `PYTHONPATH` and a cwd-relative `mcp_server/server.py` path, so
  it breaks when launched from another directory.
- `pytest`/`anyio` aren't declared as dev deps; venv is Python 3.14 while the
  project says 3.11+.

---

## 4. Operator decisions needed before the affected milestone

| ID | Decision | Blocks | Operator decision (2026-09-28) |
|----|----------|--------|--------------------------------|
| D1 | Header rule: should narration **include** the header (test_narration) or **never** include it (skill + validator)? | M2 | **Never include.** Code emits the header; `test_narration.py:102` changes to assert the narration does *not* start with the header. |
| D2 | Remove the `b4` hack (G4)? | M6 | **Remove it and make the weak-square logic smarter in general**, so what the hack encoded for b4 falls out of a general rule for any pawn advance. The rule is designed in M6. |
| D3 | Cache scope: (a) per-request dedupe + whole-request result cache, bit-identical to today; or (b) per-position cross-request cache, order-dependent. | M4 | **(a).** |
| D4 | Collapse the three phase agents into one narrator with per-phase sections (§3.7)? | M8 | **Collapse**, on the basis that the benefits are marginal (see M8 note). |
| D5 | Keep hand-rolled JSON-RPC or move to the official `mcp` SDK server? | none | Deferred. |

---

## 5. Milestones

Each milestone is small, lands as its own commit, starts with a failing test,
and leaves the default suite green. **Golden bands and fixtures are never
edited.** Any milestone touching the engine path ends with a golden run whose
junit path and duration are reported. Mapping from your earlier
"Milestone 1" list is noted per item.

### M0 — Repo hygiene & honest test harness (no behavior change)
- Add `.gitignore`. Untrack `__pycache__`, `session.db`, `artifacts/`.
- Move ad-hoc root scripts to `scripts/dev/` (or delete, your call). Move
  `test_ask.py` into `tests/`.
- Re-mark mocked `test_trajectory.py` tests so they run in the default suite (V2).
- Declare dev deps (`pytest`, `anyio`) in `pyproject.toml`.
- **Test:** default suite count goes up by the trajectory tests; still green.

### M1 — Close the prompt-injection gap (U1, U2) ← hard rule 7
- Store the **sanitized** PGN in session state. Every LLM prompt derives from it.
- Sanitize all whitelisted header values, not just player names.
- **Failing test first:** hostile PGN through the full agent (mocked MCP/LLM);
  assert no injection string appears in *any* captured sub-agent prompt. (The
  probe from this review becomes that test.)

### M2 — Make the validator real (G1, G2, G6; needs D1)
- Validator takes the actual game (or FEN + move list) instead of searching
  `tests/fixtures/`.
- Extract every SAN token, numbered or not. Unnumbered SAN must appear in the
  payload's move set (played move, best move, PV, refutation). Numbered SAN must
  be legal at that ply of the real game.
- Retry prompt includes the specific rejection reason.
- **Failing tests first:** `2.Qxh7` and unnumbered `Qxh7` on a non-fixture game
  are rejected; all existing validator tests stay green.

### M3 — Explicit timeouts + clean error handling (P1, P4, E1–E3, U3, P5) ← your list: timeout, error handling
- Agent: `asyncio.wait_for` around the tool call (env-configurable, e.g.
  `PROPHYLAX_TOOL_TIMEOUT_S`); on expiry kill the process group. Use
  `communicate()`.
- Server: per-search wall-clock watchdog. On expiry, kill Stockfish and return a
  typed error. Never stop a node-limited search early and report partial data.
- Error taxonomy: `-32602` invalid params, custom codes for engine timeout,
  engine crash and engine-version mismatch. The agent maps them to short user
  messages and logs details to stderr only.
- Input bounds: max plies, `1 ≤ max_flags ≤ K`, `1 ≤ multipv ≤ 5`.
- Call `verify_engine()` at server startup; make the version match exact
  (`Stockfish 18` token), not a substring.
- **Tests:** fake engine that never answers → typed timeout within budget; fake
  engine that exits mid-search → typed crash error; oversized PGN → `-32602`;
  malformed JSON-RPC line → error response, not silence.

### M4 — Remove redundant search + cache by FEN/settings (P3, C1, C2, P6; needs D3) ← your list: cache
- Reuse pass-1 PV/best move instead of re-searching (≈2× fewer searches).
- Cache keyed by `(fen, nodes, multipv, threads, hash, engine version)`. Scope
  per D3.
- Pin `Hash` explicitly. Delete the tautological `nodes != current_limit` guards.
  Add the missing test asserting the `Limit` sent to the engine carries the pinned
  node count.
- **Tests:** mocked engine counts searches (N+1, not 2N+1); repeat call is a
  cache hit; changed `STOCKFISH_NODES` is a miss. **Golden run must pass with
  unchanged bands**; report the duration before and after.

### M5 — Persistent engine, no per-call respawns (P2) ← your list: avoid respawns
- The agent owns one long-lived server subprocess (lazy start, lock-serialized
  requests, auto-restart on crash or timeout from M3). The server owns one
  long-lived Stockfish and sends `ucinewgame` per request so each request stays
  equivalent to a cold engine.
- Launch via `python -m mcp_server.server` with an explicit `cwd`; drop the
  `PYTHONPATH` injection.
- **Tests:** two tool calls → one spawn; killed server → next call respawns and
  succeeds. **Golden run passes unchanged** (proves the cleared-hash behavior
  matches a cold engine).

### M6 — Replace the `b4` special case with a general rule (G4; D2 decided)
- Today a pawn push reports every square that *no* pawn can defend any more
  (b2–b4 reports a3, a4, c3). The hack then trims b4 to {a3, c3}. The general
  version (exact rule proposed with a failing test before coding): report only
  squares the pushed pawn **could guard before and can't after, and that are
  not still covered by another friendly pawn**. Apply it to every pawn push on
  every file, for both colours.
- Delete the `b4` branch. `test_concession_squares_ranks` must pass unchanged.
- **Tests:** table test over pushes on several files for both colours (a3/c3
  after b2–b4, mirrored for Black, an edge-file push, a push where a neighbour
  pawn still covers the square), plus the unchanged golden suite.

### M7 — Grounded deep dive & conversation (G3, G5)
- Pass `analyze_position` multipv lines and features into the deep-dive prompt;
  allowed-move set = the union of those lines.
- The conversational path either runs through the validator (squares/moves
  must come from stored flags) or is restricted to report lookup. Your call at
  the time; default = validate.
- **Tests:** deep-dive prompt contains multipv lines; conversational reply
  citing a move outside the flags is rejected and falls back.

### M8 — Simplify agents & routing (§3.7; D4 decided)
- What is lost by collapsing: the `adk web` trace shows one narrator name
  instead of three, and per-phase model choice (unused today) would need a
  config field. Nothing else. Reversible if either matters later.
- A deterministic move-reference parser (`15.Bd3`, `11...Bxc4`, "move 15 white")
  runs first; the LLM router is only a fallback.
- One narrator agent; phase section loaded from the existing SKILL.md files.
  Update AGENTS.md accordingly.
- Split `_run_async_impl` into `analyze_game` / `ask_about_move` / `converse`
  functions. Narrate flags concurrently (bounded).
- **Tests:** parser table test (≥15 phrasings); existing routing/trajectory
  tests stay green; narration prompt for each phase contains that phase's section.

### M9 — Evaluation depth (V3, V4)
- Contract test: the real server's output (small node budget) validates against
  one schema that the mocks also use.
- Add a real endgame golden fixture (bands recorded **only** on your explicit
  `--record` instruction).
- Persist the narration fallback / retry rate per run as a tracked metric.
- **Test:** schema test in the default suite; new golden passes after recording.

### M10 — Entry point & architecture doc ← your list: document architecture
- Add the `python -m app.agent --pgn FILE --max-flags N` CLI the README already
  advertises.
- `ARCHITECTURE.md`, derived from §1 as it stands *after* M1–M8, and README
  corrections (single choke point, engine pin, CLI).
- **Test:** CLI smoke test with mocked MCP/LLM.

Suggested order: M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → M9 → M10.
M1–M3 fix hard-rule violations and hangs, so they come first. M4/M5 are the
performance work and are safe to do only after M3's timeouts exist.

---

## 6. Explicitly out of scope (for now)
- Changing WDL thresholds or the phase heuristic (product tuning; needs data).
- Runtime LLM-as-judge (cost/latency; revisit after M9 metrics).
- Migration to the official MCP SDK (D5).
- Multi-threaded Stockfish on the golden path (violates rule 5).

## 7. Golden baseline (this session)
`STOCKFISH_NODES=1000000 pytest -v -m golden`: **5 passed, 52 deselected,
630.00 s (wall 631 s)**, junit at `artifacts/junit_golden_baseline.xml`,
Stockfish 18. This is the before-number for M4/M5's duration comparison.
