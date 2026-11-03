# Phase C plan: persistent multi-game personalization (M15–M18)

Status: **M15 and M16 implemented (2026-09-29); M17 and M18 deferred until after the
application (operator decision).** The decisions C1–C10 are as approved, with these
deviations from this plan:

- The database is `data/prophylax_history.sqlite3`, overridable with
  `PROPHYLAX_HISTORY_DB` (C8).
- `CLAIMS_VERSION` lives in `domain/versions.py` alongside `ANALYZER_VERSION`. It
  isn't in `domain/claims.py`, because that file feeds the paused D12 run's
  recorded configuration hash.
- The real export forced two fixes:
  - An exclusion reason, `null_move`: every 50th game in the export ends in a null
    move. Those games are excluded, never repaired.
  - A 16 MiB MCP response line limit, since a long game's reply with
    `include_searches` exceeds asyncio's 64 KiB default.
- The profile also shows how many of a category's flagged moves were quiet
  (Channel 2) concessions, so a near-zero median loss can be read.

**Batch (2026-09-29):**

| Item | Count |
|---|---|
| Source games | 350 |
| Imported | 350 |
| Eligible and analysed | 250 |
| Excluded: bullet | 50 |
| Excluded: abandoned | 24 |
| Excluded: under 20 plies | 14 |
| Excluded: null move | 6 |
| Excluded: not the player's | 6 |

- 1,553 MistakeEvents (1,239 unclassified; 267 weak square; 99 backward pawn;
  62 king safety; 61 pawn support lost).
- Stockfish 18 at 100k nodes, 1 thread, 16 MB. 1,743s of analysis across four
  segments (resumed after each fix).
- Checks: 18,232 stored plies match the source; there are no events on opponent
  moves; stored flags re-derive to the live analysis.

The goal is to move Prophylax from

> "This move created a backward pawn."

to

> "You've created a backward pawn on a flagged move in 5 of your last 20 serious games, 4 of them in the middlegame."

and then to

> "Here are positions from your own games where this happened. Solve them again."

---

## 0. Findings that shape the design

These come from the repository as it stands and from two measurements made
for this plan: the no-engine audit of `games/my_games.pgn` and the 100k/1M
node comparison in section 3.

1. **Structural events alone are too common to mean anything.** On the
   operator's own moves across 344 games:

   | Event | Events | Games with it |
   |---|---|---|
   | New weak square | 1,453 | 320 (93%) |
   | Pawn support lost | 421 | 226 |
   | King safety reduced (middlegame/opening only, after Q2) | 379 | 169 |
   | New backward pawn | 232 | 151 |

   So a mistake has to mean an engine-flagged move, with the structural
   claims attached to it as its category. The structural event on its own is
   the base rate, not the mistake.
2. **Every derived fact is reproducible from a small engine record.** The
   server's only engine call per game is `search_game()`: one `(wdl, pv)` per
   position, N+1 positions. Everything else is deterministic python-chess code
   run on those searches: phase, win probabilities, flags, feature deltas,
   concessions, and then `build_claims`. If we store the searches, we can
   re-derive all of it later without Stockfish.
3. **The result carries no engine provenance.**
   - `analyze_pgn` doesn't say which Stockfish version, node budget, Threads
     or Hash produced it. The node count only goes to stderr, and
     `search_game`'s cache key leaves Threads, Hash and version out.
   - `domain/convert.py` rejects unknown top-level keys, so adding
     provenance is a small, additive change to the contract.
4. **The metadata Phase C needs is exactly what the sanitizer drops.**
   - `hooks/sanitize_pgn.py` whitelists only Event, Site, Date, Round,
     White, Black, Result, ECO and TimeControl. It anonymises names and never
     lets headers reach a prompt.
   - Phase C needs WhiteElo/BlackElo, Termination, EndTime and the player's
     identity.
   - These must be parsed by a separate, strict importer into typed columns,
     and must never reach a model.
5. **The history has no clock data and no ECO.** No game has `%clk` or an
   `ECO` header. So time trouble can't be separated from misunderstanding in
   this data, and the opening has to be derived from the moves.
6. **AGENTS.md hard rule 3 conflicts with Phase C.** It says: "Read-only. The
   agent analyses; it mutates nothing. No writes to user data." Persistent
   history means writing a Prophylax-owned database. This needs an explicit
   amendment (decision C1). Nothing in this plan writes to the user's PGN
   files.
7. **The history's audience doesn't match the stated target.** AGENTS.md
   targets competitive tournament players. The history is:
   - Chess.com online play only
   - rated 1265 to 1625 (rising from 1342 to 1625 over nine months)
   - 139 games at 3+0, 124 at 5+0, 51 at 1+0 bullet, 29 at 10+0, and one at
     15+10
   - 24 abandoned games, and games as short as 4 plies

   See section 9.

---

## 1. Principles for M15–M18

- **Deterministic facts, then claims, then an LLM explanation, then
  validation.** This is Phase B's pipeline, extended across games.
- **Engine observations are expensive and immutable.** Everything derived
  from them is cheap, versioned and recomputable. We store the first, and
  re-derive the second when the code changes.
- **One derivation code path.** History is derived by the same functions that
  serve a live `analyze_pgn` call, never by a copy of them.
- **The LLM never categorises.** A mistake's category is the set of
  deterministic claim types on that move. The LLM may compare retrieved
  examples, but it never decides that two mistakes are the same kind.
- **Counts must be explainable.** Every profile number is a count, a
  distinct-game count or a median that a user can check. No opaque scores.
- **Proportional.**
  - Standard-library `sqlite3`, and no ORM.
  - Numbered SQL migrations with `PRAGMA user_version`.
  - Three version constants, not event sourcing.
  - No embeddings, vector store, RAG framework, new agents or cloud.

---

## 2. M15: persistent analysis history

### 2.1 Data flow

```
games/*.pgn (user data, read only)
  │  history/importer.py: strict header parse, identify the player, sanitized movetext
  ▼
games  (immutable facts about each game)
  │  one analyze_pgn call per game, through the MCP server (rule: only the server runs
  │  Stockfish) with include_searches=true → per-position (W/D/L, pv) + analysis_config
  ▼
engine_runs + engine_positions  (expensive, immutable, keyed by engine config)
  │  history/derive.py: the server's own post-search code (factored out of
  │  handle_analyze_pgn) + domain.convert + domain.claims; no engine
  ▼
derivations → move_analyses, claims, mistake_events  (cheap, versioned, recomputable)
  │
  ├─ M16 history/profile.py: recurrence, computed on demand (no stored score)
  ├─ M17 history/query.py: structured retrieval of examples
  └─ M18 history/training.py: exercises + attempts
```

### 2.2 Where engine observations stop and derivations start

`handle_analyze_pgn` is currently one function: parse, then `search_game`,
then derive. M15 splits it into two parts with no behaviour change:

- `search_game(...)`: unchanged. It is the only engine call.
- `derive_game_payload(game, searches, max_flags) -> dict`: new and pure. It
  is the existing code from "we can now scan moves" to the returned payload.
  `handle_analyze_pgn` becomes parse, then `search_game`, then
  `derive_game_payload`.

A golden test proves the payload is byte-identical before and after the split,
at 1M nodes.

History derivation always calls `derive_game_payload` with
`max_flags=MAX_FLAGS` (400). The live default of 4 flags exists to keep a
report short; in history it would silently drop most mistakes.

`analyze_pgn` gains two optional, additive parameters and fields:

- **`include_searches: bool` (default false).** When set, the result also
  carries `searches`: for every position, `{"wdl": [w, d, l] | null, "pv": [uci...]}`.
  UCI is used, not SAN, so the stored form doesn't depend on the SAN renderer.
- **`analysis_config` (always returned).** It holds:
  - `engine`: `engine.id["name"]`, for example "Stockfish 18"
  - `nodes`, `threads`, `hash_mb`, `multipv: 1`
  - `analyzer_version` (see 2.5)

  `domain/convert.py` and `tests/payload_schema.py` accept both keys. Live
  sessions ignore them, and the prompts are unchanged.

### 2.3 Schema (SQLite, `history/migrations/0001_initial.sql`)

The database file comes from `PROPHYLAX_DB_PATH`, default
`data/prophylax.sqlite`, with `data/` gitignored.

**`games`**: one row per imported game. Immutable.

| column | type | notes |
|---|---|---|
| `game_pk` | TEXT PK | sha256 of the source identity (Site, Date, EndTime, White, Black, Result, TimeControl) plus the sanitized movetext. `GameAnalysis.game_id` is only sha1(start_fen\|moves), and identical short games (for example the same 4-ply abandonment twice) would collide. |
| `content_id` | TEXT, indexed | `GameAnalysis.game_id`. It links to claim IDs and isn't unique. |
| `source_file`, `source_index` | TEXT, INT | where the game was imported from |
| `played_on`, `end_time` | TEXT | ISO date; `EndTime` if present |
| `player_color` | TEXT | 'white'/'black', or NULL if the configured player isn't in the game (the game is then ineligible) |
| `player_rating`, `opponent_rating` | INT NULL | from WhiteElo/BlackElo |
| `time_control` | TEXT | the raw validated string, for example '180' or '900+10' |
| `tc_base_s`, `tc_increment_s` | INT | parsed |
| `tc_class` | TEXT | bullet/blitz/rapid/classical, from base + 40×increment: under 180s bullet, under 480s blitz, under 1500s rapid, otherwise classical. So 60 is bullet, 180/300 blitz, 600 and 900+10 rapid. |
| `result` | TEXT | 'win'/'draw'/'loss' from the player's side |
| `termination` | TEXT | normal/time/abandoned/other, from a fixed regex over `Termination` |
| `start_fen`, `movetext`, `plies` | | sanitized movetext only (rule 7) |
| `imported_at` | TEXT | |

Opponent names aren't stored; nothing needs them. Indexes: `(played_on)` and
`(player_color, tc_class, played_on)`.

**`engine_runs`**: one row per game per engine configuration. Immutable.

| column | notes |
|---|---|
| `run_id` INTEGER PK | |
| `game_pk` FK | |
| `engine` | 'Stockfish 18', from the engine |
| `nodes`, `threads`, `hash_mb` | from `analysis_config` |
| `created_at`, `code_ref` | `code_ref` is the git short SHA; informational only |

`UNIQUE(game_pk, engine, nodes, threads, hash_mb)`. The same configuration is
never re-searched; a different one gets a new row, and old rows stay.

**`engine_positions`**: the engine observations. Immutable.

| column | notes |
|---|---|
| `run_id`, `position` | PK; position 0 is the start, i is after ply i |
| `wins`, `draws`, `losses` | INT NULL (the WDL from the side to move, as `search_game` returns it) |
| `pv_uci` | TEXT (space-separated) |

That's about 64 rows per game, and roughly 22k rows and a few MB for the whole
history.

**`derivations`**: one row per engine run per derivation version.

| column | notes |
|---|---|
| `derivation_id` INTEGER PK | |
| `run_id` FK | |
| `analyzer_version`, `claims_version` | see 2.5 |
| `created_at` | |

`UNIQUE(run_id, analyzer_version, claims_version)`.

**`move_analyses`**: every ply. Derived, recomputable.

| column | notes |
|---|---|
| `derivation_id`, `ply` | PK |
| `move_number`, `side`, `san`, `uci`, `is_player_move` | |
| `phase`, `fen_before` | |
| `wp_before`, `wp_after`, `wdl_delta` | the mover's win probability |
| `best_move_san` | |
| `flag_channel`, `flag_rank` | NULL unless flagged |
| `pv`, `refutation_pv` | JSON |
| `feature_deltas`, `concessions` | JSON; computed for every ply, not only flags, so base rates (unflagged structural events) come from the same row |

Indexes: `(derivation_id, is_player_move, flag_channel)`.

**`claims`**: claims for flagged moves. Derived.

| column | notes |
|---|---|
| `derivation_id`, `claim_id` | PK. `claim_id` is today's `{content_id}-ply{p}-c{n}`, unique within one derivation only, which is why the PK includes the derivation |
| `ply`, `type`, `subject` | |
| `evidence`, `supports` | JSON; the `ClaimEvidence` and `supports` fields as they are |

Index: `(type, derivation_id)`.

**`mistake_events`**: see 2.6. Derived.

| column | notes |
|---|---|
| `event_id` INTEGER PK | |
| `derivation_id`, `ply` | UNIQUE together |
| `game_pk` | denormalized, for filtering |
| `played_on`, `tc_class`, `player_rating`, `phase` | denormalized |
| `channel` | wdl / quiet_inaccuracy |
| `wdl_delta`, `wp_before` | |
| `fen_before`, `played_san`, `best_move_san` | |
| `position_state` | winning / balanced / losing, from `wp_before` with fixed cuts at 0.8 and 0.2 |

**`mistake_categories`**: the deterministic category key.

| column | notes |
|---|---|
| `event_id`, `category`, `subject` | PK is all three; `subject` is '' when the claim has none |

`category` is a structural claim type, or `unclassified` when the move has only
engine claims (a tactical or other error). Index: `(category, event_id)`.

**PlayerWeakness is not a table in v1.** It's a query result (section 4),
computed in milliseconds from a few thousand rows. Storing it would be a cache
that goes stale. A snapshot table can come later if progress-over-time charts
are wanted.

M18 adds **`exercises`** and **`attempts`** (section 6).

### 2.4 What is immutable and what is recomputed

| | Written | Changes when | Recompute cost |
|---|---|---|---|
| `games` | on import | never (a re-import with the same `game_pk` is a no-op) | none |
| `engine_runs`, `engine_positions` | once per game per configuration | never; a new configuration gets new rows | engine: about 9s per game at 100k nodes, about 90s at 1M |
| `derivations` and everything under them | from engine rows | `analyzer_version` or `claims_version` changes | no engine: python-chess only, seconds for the whole history |
| profile, retrieval results | on demand | every query | milliseconds |

### 2.5 Versions and staleness

There are three constants, each guarded by a test so a change can't be
forgotten:

- **`ANALYZER_VERSION`** (in `mcp_server/`): covers everything between the
  searches and the payload:
  - `get_game_phase`
  - `get_win_probability`
  - the flag thresholds and channels
  - `mcp_server/features.py`
- **`CLAIMS_VERSION`** (in `domain/claims.py`): covers claim types, their
  evidence, and rules like Q2. The Q2 change would have bumped it.
- **Engine configuration**: engine name, nodes, threads and hash, stored on
  every run.

**Guard test.** `tests/test_versions.py` hashes the source of the modules each
version covers and compares the hash with a value recorded next to the
constant. When the code changes, the test fails until someone either bumps
the version (the derivation meaning changed) or re-records the hash (a pure
refactor). The failure message says which. This is the same idea as
`compare_narration.prompt_version`.

**What counts as stale:**
- A **derivation is stale** when its versions differ from the current
  constants. `python -m history rederive` recomputes it from the stored
  engine rows, with no engine and in seconds, and keeps the old one until the
  new one is written.
- An **engine run is stale** when its engine name differs from
  `PINNED_STOCKFISH_VERSION`, or its node budget is below what a use requires
  (section 3). `python -m history status` lists them. Re-searching is always
  an explicit, costed command, never automatic.

Profiles, retrieval and training only ever read current derivations.

### 2.6 MistakeEvent definition

**A MistakeEvent is a flagged move by the player in an eligible game, in the
current derivation.** "Flagged" means exactly what the server flags today:

- **Channel 1 (`wdl`):** the win-probability drop crosses the phase band.
- **Channel 2 (`quiet_inaccuracy`):** after move 3, the move doesn't gain win
  probability and creates both a new weak square and a new backward pawn.

It's stored with:

- game, ply, the FEN before the move, the played and best moves
- phase, channel, WDL delta, win probability before the move
- rating, time control and date (denormalized)
- the analysis configuration (via its derivation and run)
- its categories: the structural claims on that move, or `unclassified`

The verified claims themselves are joined from `claims`, not copied.

**Does every flag become an event? Yes, with no extra filter at storage
time.** Eligibility is decided when querying (section 4.2), so policies can
change without re-deriving anything. Two alternatives were considered and
rejected:

- **Dropping flags in decided positions.** The phase bands already exclude
  most of them (a 10% drop is impossible from a 5% position), and
  `position_state` lets the profile exclude them in a query if needed.
- **Dropping unclassified (tactical) flags.** They aren't Prophylax's
  speciality, but the share of a player's mistakes that are structural is
  itself useful ("most of your flagged moves are tactical"). They're kept
  under `unclassified` and never presented as a positional pattern.

### 2.7 Importer

`history/importer.py` parses the PGN headers with fixed regexes: Date,
EndTime, WhiteElo, BlackElo, TimeControl, Termination and Result. It works
out the player's colour from `PROPHYLAX_PLAYER_NAMES` (a comma-separated list
of the operator's usernames, compared exactly).

- It never forwards a header value to any model.
- The movetext goes through the existing `sanitized_movetext`.
- A game whose headers fail validation is imported with those fields NULL,
  which makes it ineligible, never guessed.

Import and analysis are idempotent and resumable: games already in the
database, and runs whose configuration already exists, are skipped.

### 2.8 Migrations

- `history/db.py` opens the database, enables foreign keys, and reads
  `PRAGMA user_version`.
- It applies each `history/migrations/NNNN_*.sql` with a higher number in one
  transaction, then sets `user_version`.
- A database newer than the code is refused.
- Migrations are forward-only. The derived tables can always be dropped and
  rebuilt with `rederive`; the three immutable tables are migrated with care.

### 2.9 Files (M15)

New:
- `history/__init__.py`
- `history/db.py`
- `history/migrations/0001_initial.sql`
- `history/importer.py`
- `history/derive.py`
- `history/__main__.py`, a CLI with `import`, `analyze`, `rederive` and `status`
- `tests/test_history_db.py`
- `tests/test_history_import.py`
- `tests/test_history_derive.py`
- `tests/test_versions.py`

Modified:
- `mcp_server/server.py`: extract `derive_game_payload`, add
  `include_searches` and `analysis_config`, and `ANALYZER_VERSION`
- `domain/convert.py`: accept the two optional keys
- `domain/claims.py`: `CLAIMS_VERSION`
- `tests/payload_schema.py`
- `config/settings.py`: `get_db_path`, `get_player_names`
- `.gitignore`: `data/`
- `ARCHITECTURE.md`
- AGENTS.md: rule 3 amendment, if C1 is approved

**Reused, not duplicated:**
- `search_game`, `get_game_phase`, `get_win_probability` and `features.*`,
  through `derive_game_payload`
- `game_analysis_from_payload`, `build_claims` and
  `sanitize_pgn`/`sanitized_movetext`
- `ply_of`
- `call_mcp_tool_subprocess`, for engine access through the server

### 2.10 Tests written first (M15)

1. **Split parity:**
   - The derivation from stored searches equals the live result at 5k nodes
     for the four fixture games (fast suite, fake-UCI engine where possible).
   - The golden test proves byte-identical results at 1M nodes.
2. **Round trip:** store searches, then derive, and the `GameAnalysis` equals
   `game_analysis_from_payload` on the live result.
3. **Provenance:**
   - `analysis_config` reports the actual engine name, nodes, Threads and
     Hash.
   - Two runs at different node counts give two `engine_runs` rows, and a
     query can't mix them silently.
4. **Importer:**
   - The header parse table (valid, malformed, missing).
   - The time-control classes for 60, 180, 300, 600 and 900+10.
   - The termination classes.
   - The player colour, including a game without the player.
   - No header text appears in any stored movetext or model-bound string.
5. **Idempotence:** importing twice gives the same row counts, and a repeated
   analyze doesn't call the engine (mocked).
6. **Version guard:** changing a covered module without a bump fails, with a
   clear message.
7. **Staleness:** bumping `CLAIMS_VERSION` in a test marks derivations stale;
   `rederive` rebuilds them from engine rows with no engine call.
8. **MistakeEvent:**
   - Every flagged player move, and only those, becomes an event.
   - Categories equal the structural claim types of that move's claims.
   - A move with only engine claims is `unclassified`.
9. **Migrations:** a fresh database and a database migrated step by step have
   identical schemas, and a newer `user_version` is refused.

---

## 3. Historical engine budget

### 3.1 What the node budget can change

- **Engine-dependent:**
  - WDL, and so the win probability before and after and the delta
  - whether the drop crosses the phase band (Channel 1), and whether a move
    counts as "not gaining" (Channel 2)
  - the best move and PV, and the refutation line
- **Not engine-dependent:** phase, feature deltas, concessions and every
  structural claim, which are pure functions of the board. A given move gets
  the same structural claims at any budget. What the budget changes is
  **whether that move is flagged**, and so whether it becomes a MistakeEvent,
  plus the engine claims (best move, refutation) attached to it.

### 3.2 Measurement (6 games from the history, 100k vs 1M, same code)

Six games from `games/my_games.pgn` (every 50th game after dropping abandoned
and very short ones: indices 0, 58, 114, 179, 236, 297; time controls 3+0,
1+0, 5+0, 3+0, 1+0, 10+0; 576 plies in all). Each was run through the
production `handle_analyze_pgn` with `max_flags=400` on Stockfish 18, 1 thread,
16 MB hash. A throwaway script produced this, and it is not committed.

| | 100k | 1M |
|---|---|---|
| time per game | 6.7 to 9.3s | 65.7 to 88.3s (about 9.5 times as long) |
| flags, both sides | 88 | 91 |

| Agreement (1M taken as the reference) | Result |
|---|---|
| All flags, same move flagged at both budgets | 77 (11 only at 100k, 14 only at 1M) |
| Flag channel, when both flag the move | 77/77 the same |
| **The player's flags** | **39 at both; 5 only at 100k, 5 only at 1M (89% precision and 89% recall against 1M)** |
| **The player's flags with a structural claim (the profile's positional categories)** | **8 at both, 1 only at 100k, 0 only at 1M** |
| Best move, all plies | 437/576 (76%) |
| \|delta\| difference in win probability, all plies | median 0, 90th percentile 3.8 points, 99th percentile 29 points, max 41 points |

Most disagreements are moves near a phase band, such as a −7 or −9 point drop
at one budget and just inside the band at the other. A few are tactical
misjudgements at 100k: the move flagged −33 only at 100k was not a mistake at
1M, and a −23 flag appears only at 1M.

**What this supports.** For counting recurring mistakes over many games, 100k
agrees with 1M on about 9 in 10 of the player's flags, and on 8 of 9
structural ones. That's good enough for counts shown as "based on quick
analysis". It isn't good enough for any single position shown to the user,
because the best move differs on a quarter of plies. Hence the 1M re-check
below.

This is 6 games. A larger check (for example 30 games, about 45 min at 1M)
would tighten these numbers, and it is only worth running if C4 is chosen.

### 3.3 Policy (proposed)

| Use | Budget | Why |
|---|---|---|
| Golden / evaluation path | 1M (unchanged) | the pinned contract (AGENTS.md rule 5) |
| Bulk history import | 100k | about 35–40 minutes for 344 games, against about 6 hours at 1M |
| Profile counts (M16) | 100k runs allowed | counts over many games tolerate per-move noise; section 3.2 shows how much |
| Examples shown to the user (M17) and training exercises (M18) | 1M re-check of that one position | anything a user studies must meet the evaluation budget |
| A recurring weakness reported as "recurring" | every event behind it re-checked at 1M before the claim is made (optional in v1, decision C4) | the user-facing claim should not rest on noisy 100k flags |

A 1M re-check of a single position is two searches (the position before the
move, with multipv, and the position after it), roughly 2 to 5s (a single 1M
search measured 1.35s from the start position). It's stored as another
`engine_runs` row for the same game at 1M, restricted to the positions it
covers (`engine_positions` rows only exist for those positions).

---

## 4. M16: recurring weaknesses

### 4.1 Question answered

"What do I repeatedly do wrong?", answered with counts a user can check, in
this form:

```
Backward pawns created on flagged moves
  5 of your last 20 eligible games (6 moves)
  4 in the middlegame, 2 in the endgame
  median win-probability loss: 8 points (range 4–21)
  you created backward pawns 13 times in those games; 6 of those moves were flagged
  previous 20 games: 2 games
```

### 4.2 Eligibility (a function, not stored)

A game counts only if all of these hold:

- the player is identified (`player_color` isn't NULL)
- the termination isn't `abandoned`
- it has at least 20 plies
- it has a current derivation
- its time class is in the selected classes

The default classes are blitz, rapid and classical. **Bullet is excluded by
default** and available with `--include-bullet`.

### 4.3 The statistics (all simple, all shown)

For each category in the window:

- **Distinct games**: games with at least one event in this category. This is
  the primary measure, so one chaotic game counts once.
- **Events**: all moves in this category (shown, not ranked on).
- **Phase split** of events.
- **Severity**: the median and range of `-wdl_delta`, in percentage points. It
  isn't weighted, and the median resists a single blunder.
- **Conversion**: of all the player's moves in the window that created this
  structural feature (from `move_analyses.concessions`, flagged or not), how
  many were flagged. This uses the audit's base rates rather than hiding
  them. "You create backward pawns often, and 1 in 5 costs you" is more
  useful than a raw count.
- **Trend**: distinct games in the previous window of the same size.

**The rule for calling something recurring:** at least 3 distinct games in the
window. Categories are ranked by distinct games, then by median severity.
`unclassified` is reported on its own line ("flagged moves with no structural
pattern: N games") and never ranked as a positional weakness.

**Windows:**
- The default view is the last 20 eligible games.
- The "all time" view shows the same numbers over every eligible game.
- There is no exponential decay: a window is easier to explain and handles
  the rating climb by recency.
- Each view shows the rating range of the games it covers.

**Time controls:**
- 3+0 and 5+0 (blitz) and 10+0 and 15+10 (rapid) aren't weighted against
  each other.
- If the window contains more than one class, counts are shown per class
  as well as in total.
- Weighting would be a hidden formula; separate columns are not.

### 4.4 Files and tests (M16)

- **New:** `history/profile.py` (pure functions over a database connection)
  and a `profile` subcommand in `history/__main__.py`.
- **Tests first** (`tests/test_history_profile.py`, over a fixture database
  built from synthetic rows):
  - eligibility, one test per exclusion rule
  - distinct-game counting, with 5 events in one game counted as 1
  - the 3-game threshold, at 2 and at 3
  - median severity, with odd and even counts
  - conversion, including a denominator of zero
  - the window boundary, with game 21 excluded
  - trend
  - bullet excluded by default and included with the flag
  - the per-class split when classes are mixed
  - `unclassified` never ranked

---

## 5. M17: cross-game retrieval

### 5.1 Structured retrieval only

`history/query.py`:
`find_events(category=None, phase=None, tc_classes=..., since=None, min_loss=None, opening_prefix=None, subject_file=None, limit=5) -> list[Example]`.

An `Example` is the event, its game's date and time class, its claims, and
its FEN before the move. Results come newest first, and ties are broken by
larger loss.

**What structured metadata can answer now:**
- "Do I keep making this mistake?" (the current move's categories)
- "Show me my backward-pawn mistakes in the middlegame"
- "Did this happen more in bullet?"
- "What about in this opening?" (as a move prefix)
- "Is it getting better?" (the trend)

**What it can't answer, and what would justify semantic retrieval later:**
- questions about concepts the claim vocabulary doesn't have (for example
  "games where I misplayed the minority attack", or pawn races (Q3))
- free-text notes the user writes about their games

**The first answer to both is new deterministic concepts** (as Q3 already
decided for pawn races), not embeddings. Embeddings would be justified only
for user-written free text, which Prophylax doesn't store.

**Openings:**
- There's no ECO header and no opening table in the repo, so the opening key
  is the first 8 plies' SAN sequence plus the player's colour. It's exact and
  crude, but it groups repertoire lines.
- An opening-name table (for example the CC0 Lichess `chess-openings` TSV) is
  decision C6.

### 5.2 How a question reaches retrieval

- The **category comes from deterministic sources only:**
  - the claims of the move being discussed, in the current session
  - or a fixed keyword map from user text to claim types ("backward pawn"
    maps to `backward_pawn_created`, "hole"/"weak square" to
    `weak_square_created`, and so on)
- If neither yields a category, the answer lists the pattern types Prophylax
  can look up. It doesn't guess.
- Routing is an addition to `CoachingAgent.route`: a deterministic history
  intent first ("do I keep", "have I done this before", "my games"), then the
  existing LLM router.
- The narration for a history answer gets:
  - the counts (from M16)
  - up to 3 retrieved examples, as their claim sentences
  - an instruction to compare only what those claims state
- Its validator ground is the union of the examples' claim grounds (the
  existing `claims_ground`), so squares and moves not in any example are
  rejected as today.
- This depends on the D12 outcome (which narration input is default), so it's
  designed against whichever path wins.

### 5.3 Tests (M17)

- the query filters, one test each
- ordering
- the keyword map, with unmapped text giving no category
- the LLM never supplies a category (the router output is ignored for
  category)
- a history answer that cites a square from no example is rejected

---

## 6. M18: training from the player's own mistakes

### 6.1 Flow

```
eligible MistakeEvent (category ≠ unclassified by default, see C7)
  → re-check at 1M: analyze_position(fen_before, multipv=3)
  → qualifies as an exercise? store it
  → show the position (FEN / board), side to move, "find the best move"
  → user answers with a move (SAN)
  → legal? → in the stored solution set? if not, evaluate it: one search of the position after it
  → verdict + the original verified claims + the engine line
```

### 6.2 Decisions in the design

- **Qualifies as an exercise** only if the 1M re-check confirms all three:
  - The played move still loses at least the phase band.
  - The best move's win probability beats the second-best by at least 8
    percentage points, so there's one clear answer.
  - The position isn't already decided (`wp` of the best move between 0.2
    and 0.95).

  Positions with several good moves are skipped in v1. They'd need a
  plan-based exercise, which can't be marked deterministically.
- **Answer type:** a move only in v1. Plans can't be verified by the engine
  or the claims, and grading them would need an LLM judge, which this
  project keeps out of runtime.
- **Grading** (all engine, no LLM):
  - correct if the answer is within 3 points of the best move's win
    probability
  - close if it's better than the played move
  - otherwise it's a miss
- **Not giving the answer away:**
  - The exercise shows only the position and the side to move.
  - It shows no date, opponent, result or evaluation, and never the played
    move.
  - Exercises are served in random order, not in game order.
- **Afterwards:**
  - The verdict, the engine's best line, and what the game move was and what
    it conceded, as the stored claim sentences for that move (reused as they
    are, no new narration needed).
  - An LLM explanation is optional, through the existing narrator with the
    same validation, and only if the default narration path supports it after
    D12.
- **No spaced repetition in v1.** Attempts are stored, so it can be added
  later.

### 6.3 Schema additions (`0002_training.sql`)

- **`exercises`**: `exercise_id` PK, `event_id` FK, `fen`, `side_to_move`,
  `solutions` (JSON: SAN and win probability of the qualifying moves),
  `best_wp`, `played_wp`, `run_id` (the 1M re-check run) and `created_at`.
  Recomputable from events plus a 1M re-check.
- **`attempts`**: `attempt_id` PK, `exercise_id` FK, `answered_at`,
  `answer_san`, `answer_wp`, `verdict`. These are user data: append-only,
  never recomputed or dropped by `rederive`.

### 6.4 Files and tests (M18)

- **New:** `history/training.py` and a `train` subcommand (CLI first).
- **Tests first:**
  - qualification, one test per rule, with a fake engine
  - grading boundaries at 3 points and at the played move
  - the exercise text contains no played move, date or result
  - claim sentences are reused unchanged
  - attempts survive `rederive`

---

## 7. Invariants (all milestones)

1. Only the MCP server runs Stockfish. The history CLI calls it through
   `call_mcp_tool_subprocess`.
2. No header value, name or comment reaches a model. Stored movetext is
   `sanitized_movetext`.
3. The live `analyze_pgn` payload without `include_searches` is byte-identical
   to today's, apart from the added `analysis_config`.
4. Every derived row traces to one engine run with a recorded configuration
   and to the current `ANALYZER_VERSION` and `CLAIMS_VERSION`. Queries never
   mix derivations or budgets.
5. Categories come only from deterministic claim types. An LLM output is
   never written into a category.
6. The user's PGN files are only read. The only writes go to
   `PROPHYLAX_DB_PATH`.
7. The fast suite stays offline and needs no engine. Golden and live tests
   keep their markers.
8. No fixture, band, eval case or benchmark label changes as part of Phase C.

---

## 8. Expensive operations (none run without approval)

| Operation | Cost | When |
|---|---|---|
| Bulk import at 100k nodes | about 35–40 min (344 games, measured 9.3s for 88 plies) | once, after M15 is merged |
| Bulk import at 1M | about 6 h, extrapolated (about 88s per 88 plies, measured) | not proposed |
| 1M re-check per example or exercise | about 3s per position | on demand; about 5 min for 100 exercises |
| `rederive` for the whole history | seconds (no engine) | after any version bump |
| Golden split-parity test | about the current golden suite plus about 2 min | M15 |
| Narration for history answers (M17) | one narrator call per answer | live use; free tier |

---

## 9. Audience and positioning (decision C2)

**Recommendation:** in Phase C, Prophylax serves **improving competitive
players, roughly 1300–2000 online rapid and blitz, who know basic tactics and
want pattern-level feedback across their own games.** Tournament players stay
in scope. "No beginner content" stays: no rules, no piece values, no
one-move-tactic lessons.

What that means:
- **Vocabulary.**
  - Keep the precise terms (backward pawn, hole, outpost), since they're
    exactly what the claims support.
  - Each term gets one plain-words gloss the first time a profile or report
    uses it (for example "a backward pawn: it can't be protected by another
    pawn, and the square in front of it is controlled by the opponent").
- **Depth.** Explanation depth 1 stays the default. The profile is the new
  depth: patterns across games rather than longer single-move essays.
- **Training difficulty.** Only positions with one clear best move (section
  6.2). Nothing is "hard" in the abstract; the difficulty is the player's own
  mistakes.
- **Bullet.** Out of the default profile, retrieval and training. At 1+0,
  mistakes mostly measure speed, and there's no clock data to separate time
  trouble. It's available on request.

---

## 10. Operator decisions required

| # | Decision | Recommendation |
|---|---|---|
| C1 | Amend AGENTS.md rule 3 to allow writes to a Prophylax-owned local database (never to user files) | Yes: "reads user files; writes only its own history database at PROPHYLAX_DB_PATH" |
| C2 | Audience positioning (section 9) | as proposed |
| C3 | Bulk budget 100k, with a 1M re-check for anything shown or trained | Yes: 89% flag agreement on the player's moves is enough for counts; 76% best-move agreement is not enough for single positions |
| C4 | Re-check every event behind a "recurring" claim at 1M before reporting it | v1: no. Label counts "based on quick analysis"; the measured structural disagreement is 1 in 9. Revisit with a 30-game check if profiles look noisy. |
| C5 | Profile defaults: last 20 eligible games, at least 3 distinct games, bullet excluded, at least 20 plies, abandoned excluded | as proposed |
| C6 | Opening names: move-prefix only, or add the CC0 Lichess openings table as data | prefix-only in v1 |
| C7 | Train on unclassified (tactical) mistakes too, or structural only | structural only in v1 (the product's speciality); tactical as an option |
| C8 | Database location | `data/prophylax.sqlite`, gitignored, overridable with `PROPHYLAX_DB_PATH` |
| C9 | History answers (M17) narrated by the D12 winner, or deterministic text first | deterministic text first (counts plus claim sentences), narration second |
| C10 | Should games analysed in a live chat session also be saved to history? | Not in v1. History is filled only by the explicit `python -m history import` / `analyze` commands, which keeps the write scope under C1 obvious. |

---

## 11. Milestones and issue-sized tasks

Each task is one PR: tests first, then code, the fast suite green, and a
junit path in the description.

**M15: persistent history**
1. `derive_game_payload` extraction from `handle_analyze_pgn`, with a
   byte-identical parity test (fast and golden).
2. `analysis_config` in the `analyze_pgn` result, plus the converter and
   schema updates.
3. The `include_searches` option, with a round-trip test (searches, derive,
   equal payload).
4. `ANALYZER_VERSION`, `CLAIMS_VERSION` and `tests/test_versions.py` (the
   source-hash guard).
5. `history/db.py` with `0001_initial.sql`, the `user_version` migrations and
   the migration tests.
6. `history/importer.py`: the header parse, time-control and termination
   classes, player identification, and the sanitized movetext.
7. `history/derive.py`: engine rows to `move_analyses`, `claims`,
   `mistake_events` and `mistake_categories`.
8. The `python -m history import|analyze|rederive|status` CLI, idempotent and
   resumable.
9. ARCHITECTURE.md, the AGENTS.md rule 3 amendment (after C1), and
   `.gitignore`.
10. **Operator-approved run:** the bulk import at 100k nodes (about 40 min).

**M16: recurring weaknesses**
1. Eligibility function and tests.
2. Per-category statistics: distinct games, events, phase split, median
   severity.
3. Conversion (base-rate) statistic.
4. Windows, trend and the per-class split.
5. `python -m history profile`, with a text output and a JSON output.

**M17: retrieval**
1. `find_events` with its filters and ordering.
2. The opening prefix key.
3. The keyword map from text to categories.
4. The history intent in `route`, deterministic first.
5. A history answer from deterministic text (counts plus example claim
   sentences).
6. (After D12, and after C9) the narrated history answer, with the union
   grounding check.

**M18: training**
1. Exercise qualification, using the 1M re-check through `analyze_position`.
2. The `exercises` and `attempts` migration (`0002`).
3. Grading.
4. `python -m history train`: the CLI loop and the post-answer explanation
   from stored claims.
5. The no-giveaway test and the test that attempts survive `rederive`.

---

## 12. Explicitly not in Phase C

The following stay out of Phase C:
- embeddings, vector databases and RAG frameworks
- new agents, cloud infrastructure and fine-tuning
- spaced repetition
- plan-graded exercises
- an LLM judge at runtime
- opening-name data (unless C6)
- clock-based time-trouble detection (no `%clk` in the data)
- event sourcing
