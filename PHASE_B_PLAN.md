# Phase B plan: structured coaching (M11–M14)

Status: **approved 2026-09-28, with the D12 controlled-comparison amendment.
Implemented milestone by milestone, in order.**

| Milestone | State | Suites after |
|---|---|---|
| M11.0 FEN numbering | done; follow-up: the session movetext kept no start FEN, so deep dives and conversation on a FEN game replayed from the standard position (`sanitized_movetext` now keeps the re-emitted FEN; deep-dive test verified failing first); `tests/test_fen_numbering.py` (validator and server tests verified failing first); `ply_of` in `scripts/move_reference.py` used by the agent and validator. For FEN games, the move-1–3 threshold and the opening phase now follow the FEN's real move number | fast 147 passed, 17 deselected, 13s (`artifacts/junit_m11_0.xml`); golden 6 passed, 141s (`artifacts/junit_golden_m11_0.xml`) |
| M11 models + converter | done (commit 2 of 3); `domain/models.py`, `domain/convert.py`, `tests/test_domain_models.py` (22 tests; each malformed-payload case checked to fail for its own reason). Commit 3 (wiring) was blocked: 13 agent tests across 7 files mock `analyze_pgn` with payloads the real server never returns (empty or partial `move_evals`, an extra `multipv` key), and the converter rejects them | fast 171 passed, 17 deselected, 13s (`artifacts/junit_m11_models.xml`) |
| M11 wiring | done; operator option A: agent-test mocks now built by `pgn_payload` / `position_payload` in `tests/payload_schema.py` (own commit, no assertion changed). `analyze_game` and `ask_about_move` convert every server result; narration prompts byte-identical (tested). CLI end to end on `fools_mate` and a FEN game with real Stockfish (200k nodes) and the real narrator: both reports produced, exit 0 | fast 174 passed, 17 deselected, 13s (`artifacts/junit_m11.xml`) |

This plan is based on reading the code at `fe177b8`. It adds a typed domain layer
and a deterministic claim layer on top of the working pipeline. The MCP server,
Stockfish settings, detection thresholds and golden bands do not change. The one
exception is a proposed prerequisite fix (M11.0).

## What the code looks like today (facts the design depends on)

1. **Flags carry no ply index or FEN.** A flag names its move by `move_number` and
   `side`. Everything that needs the board replays the sanitized movetext. That
   includes `ask_about_move`, the validator's legality check and the eval
   harness's `get_matching_game`, and all of them use the formula
   `ply = (move_number - 1) * 2 + (side == black)`.
2. **The server numbers moves from 1, whatever the FEN says**
   (`server.py:242`, `int(idx / 2) + 1`). For a FEN that starts with Black to
   move, this breaks. Ply 0 is `1...` and ply 1 is labelled `1.` (White) when it
   should be `2.`. The formula in (1) then points at the wrong ply. This became
   reachable in `8ae2d61`, when FEN games started working through the agent.
3. **Two different weak-square numbers exist.** `feature_deltas.weak_squares` is
   the unfiltered change in the count of holes: 13.b4 gives −2. The M6 rule in
   `concessions.new_weak_squares` keeps only the reportable ones: 13.b4 gives
   `a3`. Claims must come from the concessions, never from the count.
4. **What concessions contain today**, computed with `get_quiet_concessions` on
   the fixtures:

   | Move | Concessions | Feature deltas |
   |---|---|---|
   | 13.b4 (Scandinavian) | `new_weak_squares [a3]`, `new_pawn_unsupported [c3]` | weak_squares −2, activity −1 |
   | 10...b5 (Carlsbad) | `new_weak_squares [c5, a6]`, `new_backward_pawns [c6]`, `new_pawn_unsupported [c6]` | weak_squares −3, structure −0.5, activity +1 |
   | 12...Ne7, 4.Ke3 | none | activity only |

5. **When a pawn is both backward and unsupported, the narrator sees only
   "backward".** `format_flag_for_llm` drops the unsupported entry. Claims keep
   this rule.
6. **Five of the nine narration eval cases are synthetic.** They have no
   game, no FEN, and sometimes no `concessions` key. Any claim path therefore has
   to work from the payload alone. Board evidence is an extra, available only
   when the game is.
7. **Session state holds plain dicts.** ADK state must be JSON, and flags are
   mutated in place: `f["narration"] = ...`. Typed models are used in memory
   and converted at the edges. Session state keeps its dicts until M15.
8. **pydantic 2.13.4 is already installed** as a dependency of google-adk.
   jsonschema is installed too, and `tests/payload_schema.py` already describes
   the server contract.

## Principles for all four milestones

- **New modules sit beside the existing path; they don't replace it.** Each
  milestone ends with the repo working, the fast suite green and the golden
  suite unchanged.
- **The server's JSON contract is unchanged.** `tests/payload_schema.py` stays
  the source of truth for it.
- **Old paths are removed only after an operator decision backed by numbers**
  (M13).
- **One new package, `domain/`.** It holds the models, the conversion and the
  claim builder. `AGENTS.md` says deterministic work lives in `scripts/`. I read
  that as "code, not skill prose", and a typed domain package is clearer than
  more loose scripts. Prompt text formatting stays in
  `scripts/format_narration.py`.

---

## M11 — Canonical domain models

### M11.0 (proposed prerequisite): FEN move numbering

This known limit blocks M11. The converter has to map each flag to its ply, and
for games that start with Black to move the numbering gives the wrong ply. Fix
it once, with the numbering taken from the start board:

- `mcp_server/server.py`:
  - `move_number = board_before.fullmove_number`
  - `side` from `board_before.turn`, as today.
  - The fixtures, which start from the standard position, give identical
    output.
- **Ply lookup:** a new shared helper,
  `scripts/move_reference.py: ply_of(start_board, move_number, side)`. It
  replaces the formula in `app/agent.py` (`ask_about_move`) and in
  `evals/validate_narration.py` (`check_narration_moves_legality`).
- **Tests first:**
  - A server test on a Black-to-move FEN game with fullmove 30 (fast suite,
    through `handle_line` at 5k nodes, skipped without Stockfish).
  - A validator test that `30...Kd5 31.Kd3` is legal in that game.
  - `ply_of` unit tests.
  - All three fail today.

M11.0 is scoped to move numbering only (D6). No other FEN work goes in it.

### New types (`domain/models.py`, pydantic v2, frozen, `extra="forbid"`)

Pydantic (D5) is used only for types that cross the loose-dict boundary or get
persisted later: the containers and records below. Small internal values stay
plain Python: squares as `str`, SAN lists as `list[str]`, sides as a `Literal`.
`pydantic` gets pinned in `pyproject.toml` at the installed 2.13.4.

The four layers the architect asked for stay separate:

| Layer | Types | Source |
|---|---|---|
| Raw observation | `EngineEvaluation` (win prob before/after, delta), `EngineLine` (SAN list plus start ply), `PositionFeatures` (the `analyze_position` features block) | engine and features, straight from the payload |
| Deterministic fact | `FeatureDelta` (name, value), `Concessions` (new_weak_squares, new_backward_pawns, new_fixed_backward_pawns, new_pawn_unsupported: sorted square lists) | server-computed facts from the payload |
| Coaching claim | `CoachingClaim`, `ClaimEvidence` | M12. The types are defined in M12, where they are first used |
| Narration | `str` | the narrator. It is never stored inside a model |

The containers:

- **`MoveAnalysis`**, one per ply:
  - `ply`, `move_number`, `side`, `san`, `phase` and `fen_before` (from
    replaying the sanitized movetext).
  - `evaluation: EngineEvaluation`.
  - `best_move: str`.
  - `detail: FlagDetail | None`, set only for flagged moves. It holds `pv`,
    `refutation`, `feature_deltas`, `concessions`, `channel` and `rank`, the
    server's ranking order.
- **`GameAnalysis`**: `game_id`, `start_fen`, `moves: list[MoveAnalysis]`,
  `summary`, and `flagged()` for the flagged moves in the server's rank order.
  - `game_id` is a hash of the start FEN plus the UCI moves, for the M12 claim
    IDs.
- **`PositionAnalysis`**: `fen`, `lines: list[EngineLine]` and `features:
  PositionFeatures`, for the deep dive.

### Conversion boundary (`domain/convert.py`)

- `game_analysis_from_payload(payload, movetext) -> GameAnalysis` and
  `position_analysis_from_payload(payload, fen) -> PositionAnalysis`.
- **Loud failure** (a typed error, which the agent maps to the generic user
  message):
  - unknown or missing fields, or an unknown concession kind
  - a flag whose SAN doesn't match the replayed move at its ply
  - `move_evals` whose length doesn't equal the number of plies
  - a flag with no matching `move_evals` entry
- `to_flag_dict(move) -> dict` rebuilds today's flag dict exactly, so the old
  prompt and validator code keeps working unchanged.

### Existing code reused

- `tests/payload_schema.py`: schema tests stay; the models must accept every
  payload the schema accepts.
- The replay logic in `ask_about_move`, which moves into the converter.
- The fixture PGNs.

### Tests added first (`tests/test_domain_models.py`)

- **Round trip:**
  - The M9 recorded mock payloads (`test_agent_structure.FLAGS` and the
    others) convert, and `to_flag_dict` returns the original dicts.
  - With Stockfish set, the real server's Carlsbad output at 5k nodes converts
    too.
- **Loud failures:** a missing `phase`, an extra key, an unknown concession
  kind, a SAN/ply mismatch and a truncated `move_evals` each raise.
- **Layering:** a `MoveAnalysis` has no narration field; claims can't be set on
  it.
- **Behaviour unchanged:** the agent tests that snapshot prompts
  (`test_prompt_injection`, `test_agent_structure`) pass unchanged.

### Migration (3 commits)

1. M11.0: the numbering fix.
2. Models, converter and tests. Nothing in `app/` imports them yet.
3. `analyze_game` and `ask_about_move` convert the server result on arrival and
   fail loudly on a bad payload. The narration input is still the flag dict,
   produced by `to_flag_dict`, so prompts are byte-identical. A test compares
   the prompts with and without conversion.

### Invariants

- Server JSON unchanged, except M11.0's `move_number` for FEN games.
- Golden suite: 6 passed, bands untouched.
- No prompt text changes.
- No LLM calls in any new test.

---

## M12 — Structured coaching claim engine

### New types (`domain/models.py`)

Provenance (D7) has three levels, and each is visible on the object:

| Level | What | Type |
|---|---|---|
| 1. Engine payload | values that came back from the MCP server: Stockfish numbers and lines (`engine`), and the server's own deterministic features: concessions and feature deltas (`server_features`) | `ClaimEvidence` with `provenance="engine"` or `"server_features"` |
| 2. Board-derived | recomputed here from `fen_before` and the move with python-chess and the pure functions in `features.py`; no search, no evaluation, no LLM | `ClaimEvidence` with `provenance="board"` |
| 3. Coaching claim | a statement built from level 1–2 evidence | `CoachingClaim` (composites reference other claims through `supports`) |

```python
class ClaimEvidence(BaseModel):   # frozen, extra="forbid"
    provenance: Literal["engine", "server_features", "board"]
    fact: str                 # e.g. "enemy_pawn_attackers"
    value: str | int | float | bool | list[str]

class CoachingClaim(BaseModel):
    claim_id: str            # f"{game_id}-ply{ply}-c{n}", stable ordering
    type: ClaimType          # Literal of the 8 types below
    ply: int
    move_san: str
    side: Literal["white", "black"]
    subject: str | None      # a square, or None for whole-move claims
    evidence: list[ClaimEvidence]
    supports: list[str] = [] # claim_ids this claim is built from (composites)
```

A test checks that no `board` evidence is produced when `board_before` is
`None`, and that the module producing `board` evidence imports nothing from the
engine path (`search_*`, `engine_session`).

### Claim types: each maps to one existing signal, with no new semantics

| Type | Emitted when (existing signal) | Subject | Payload evidence | Board evidence (only with `fen_before`) |
|---|---|---|---|---|
| `wdl_loss` | `wdl_delta < 0` | — | before, after, delta, phase, `channel` | — |
| `engine_best_move` | `best_move_san != move_san` | best-move target square | best move, `pv` | — |
| `engine_refutation` | `refutation_pv` non-empty | — | `refutation_pv` | — |
| `weak_square_created` | square in `concessions.new_weak_squares` | the square | square | colour complex; which M6 branch applied (`was_guarded_by_moved_pawn` / `enemy_pawn_attacks`); enemy pawns attacking it |
| `backward_pawn_created` | square in `new_backward_pawns` | the pawn | square; fixed (in `new_fixed_backward_pawns`) | front square; enemy and friendly pawns attacking the front square |
| `pawn_support_lost` | square in `new_pawn_unsupported` **and not** in `new_backward_pawns` (rule from fact 5) | the pawn | square | friendly pawns that could support it before (now none); enemy pawns attacking it (D7) |
| `king_safety_reduced` | `feature_deltas.king_safety_delta < 0` | mover's king square | delta | before/after scores |
| `quiet_structural_concession` | `channel == "quiet_inaccuracy"` | — | channel | — (`supports` lists its weak-square and backward-pawn claims) |

Notes on the table:

- **`new_pawn_unsupported` stays neutral.** It only produces a claim when the
  server already reported it, and the server's Channel 2 condition is not
  touched. A test pins it: a move with only `new_pawn_unsupported` still gets
  no flag.
- **Not claims:** `pawn_structure_delta`, `piece_activity_delta` and the raw
  `weak_squares` count. They are scores, not statements (D8). They stay
  everywhere else: in the server payload, in the M11 models as `FeatureDelta`s,
  in `format_flag_for_llm` while the flag path exists, and in future telemetry.
  They are only left out of the claims prompt.
- **Board evidence** is recomputed with the existing pure functions in
  `mcp_server/features.py` (`compute_weak_squares`, `compute_pawn_structure`,
  `pawn_support_impossible`, `compute_king_safety`) plus python-chess
  attackers. There is no engine call and no new chess rule.
  - **Consistency check:** if recomputing contradicts the payload, the builder
    raises instead of emitting. For example, the server says c6 is backward but
    the recomputed structure disagrees.
- **Ordering:** claims are ordered deterministically by a fixed type priority
  (the table order), then by square. The narrator may still reprioritize.

### Module (`domain/claims.py`)

`build_claims(move: MoveAnalysis, board_before: chess.Board | None) ->
list[CoachingClaim]`.

- **Pure function:** no I/O, no LLM.
- **`board_before=None`** gives payload-only evidence. This is the path for the
  synthetic eval cases.
- **Symmetry:** everything is expressed through `side` and mover/opponent,
  never through colour constants.

`scripts/format_narration.py` gains `claim_sentence(claim) -> str`, one
deterministic English template per type. For example, `pawn_support_lost` on c3
after 13.b4 gives "After 13.b4, no pawn can ever support the c3 pawn." This is
the text of the "VERIFIED CLAIMS" block in M13. It isn't wired to narration yet.

### Tests added first (`tests/test_claims.py`, table-driven, no engine, no LLM)

- **One table row per claim type,** with positive and negative cases, from
  hand-set flag dicts. The fixture moves (13.b4, 10...b5, 12...Ne7, 4.Ke3) are
  included with their board evidence.
- **Mirror test:** `board.mirror()` plus the mirrored flag must give the same
  claim types and mirrored subjects, for every row that has a board.
- **Fact 5 rule:** a pawn that is both backward and unsupported gives only
  `backward_pawn_created`.
- **Guard:** a move whose only concession is `new_pawn_unsupported` is not a
  Channel 2 flag. This runs through `handle_analyze_pgn` with the fake engine.
- **Consistency guard:** an inconsistent payload plus board raises.
- **Templates:** `claim_sentence` gives the exact sentence for every type, and
  every SAN and square in it comes from the claim.

### Migration (2 commits)

1. Claim types, payload-only builder and templates, with their tests.
2. Board evidence, the consistency check and the mirror tests.

Nothing in `app/` changes.

### Invariants

- Server, thresholds, Channel 1/2 logic and `features.py` unchanged.
- Prompts unchanged.
- Golden unchanged.

---

## M13 — Claim-grounded narration

### Files

- `scripts/format_narration.py`: `format_claims_for_llm(move, claims, depth,
  rating)`. It contains the header, phase, audience, a numbered VERIFIED CLAIMS
  block of `claim_sentence`s, and the pre-rendered PV and refutation lines.
  There are no raw feature deltas and no internal numbers.
- `.agents/skills/narration_contract.md`: a new "Verified claims" section.
  - Board-specific statements must come from the claims; general chess
    explanation is fine.
  - Claim IDs are never cited in prose.
- `evals/validate_narration.py`:
  - `claims_ground(claims) -> flag-like dict`, the moves and squares from the
    claims plus their evidence, for the existing whitelist.
  - `concept_violation(text, claims)`, see below.
  - The existing checks stay unchanged and always run.
- `app/agent.py`: `_narrate` picks the input by
  `PROPHYLAX_NARRATION_INPUT=flag|claims`, defaulting to `flag`. Retry and
  fallback are unchanged. The fallback text is built from `claim_sentence`s
  when the claims path is used.
- `evals/compare_narration.py` (new) runs both inputs on the 9 existing cases.
  - **Per input it records:** first-pass rate, validator rejection rate with
    reasons, retry rate, fallback rate, and judge pass rate (existing
    `evals/judge.py`, unchanged rubric).
  - **Output:** `artifacts/narration_compare.json`, plus junit.
  - **Eval cases are not edited.** Synthetic cases use payload-only claims; the
    4 real-game cases get board evidence through `get_matching_game`.

**Claim-backed terminology check (D9).** This covers only terms that have a
precise Prophylax definition:

| Term (case-insensitive, singular or plural) | Needs a claim of type |
|---|---|
| "backward pawn" | `backward_pawn_created` |
| "weak square", "hole", "outpost" | `weak_square_created` |

- **A term with no matching claim is a violation.** It goes through the same
  retry and fallback.
- **Ordinary vocabulary is allowed:** "weakness", "target", "king activity",
  "exposed", "structure" and so on.
- **"king safety" and "pawn support" are left out.** They are everyday words
  as much as Prophylax terms. Adding them later is a table edit.
- **Payload-only claims work the same way.** The check needs only claim types,
  not board evidence, so it runs identically on the synthetic cases.

**Deep dive and conversation stay on the flag path in M13.** The deep dive's
`position_facts` don't map to claim types yet, and moving them is a separate
decision once the comparison is in.

### Tests added first

- **`tests/test_claim_prompt.py`:**
  - The claims prompt for 13.b4 contains exactly its claim sentences.
  - It contains no `feature_deltas` and no `WDL Delta:`.
  - The header rule still holds.
  - A payload-only synthetic case produces a prompt.
- **`tests/test_validator.py` additions:**
  - "backward pawn" with no backward claim is rejected.
  - An evidence square (such as the enemy pawn on d4) is allowed.
  - All existing validator tests pass unchanged.
- **`tests/test_agent_structure.py`:** with `PROPHYLAX_NARRATION_INPUT=claims`
  the narrator gets the claims prompt. The default path's prompt is
  byte-identical to today's.

### Decision rule for the switch (D12, fixed before any run)

**Protocol (a controlled A/B test):**
- Both paths run the same 9 cases, **3 runs per case** (27 runs per path).
- **Pinned models.** One certified narrator (`gemini-3.1-flash-lite`) and one
  judge (`groq/openai/gpt-oss-120b`) are used throughout. Narrator fallback is
  disabled for the whole experiment, the same way a certification run disables
  it.
- **Pinned generation settings.** Every setting the provider exposes is fixed:
  temperature, top_p, top_k, max output tokens and seed where supported. They
  are written into the artifact.
- **Paired, interleaved runs.** Each case is run under both prompts back to
  back, alternating which goes first: case1 flag → claims, case2 claims →
  flag, and so on. This limits drift from provider load and time of day.
- **Infrastructure failures are not prompt failures.** A 429 or 503 that stops
  the pinned model from answering is recorded separately, with its count, and
  the trial is rerun with the same configuration. It never falls back and is
  never counted in M-a to M-f.
- **The artifact records** the narrator model, the judge model, the generation
  settings, the prompt version (a hash of the contract plus the prompt
  builder's source), and every trial's result and infrastructure retries.
- The terminology check (D9) runs on both paths' first attempts **as a
  measurement only**. On the flag path it uses the same claims, built but not
  shown to the model. This makes the concept-violation rate comparable.
- The existing narration-gate checks (sentence count, forbidden claims,
  expected theme) are applied to both paths' final narrations. Their logic
  moves into a shared function that `tests/test_narration.py` calls. That is a
  refactor with no assertion changed, and it gets its own commit with a test
  showing identical verdicts on recorded narrations.
- Estimated quota: ~54 narrator runs plus retries, and 54 judge calls. That is
  about 130 calls and 15–20 min because of Groq's per-minute token cap. Free
  tier only.

**Metrics per path**, each reported as a count over 27:

| # | Metric |
|---|---|
| M-a | validator rejections on the first attempt |
| M-b | retries |
| M-c | fallbacks |
| M-d | judge passes, on the final narration |
| M-e | concept-term violations on the first attempt |
| M-f | narration-gate passes, on the final narration |

**Rule.** The tolerance is 1 run in 27.
1. **Hard requirements.** The claims path ships only if all of these hold:
   - Every final claims narration passes the existing validator and the
     terminology check. By construction this means passing or falling back.
   - M-c (fallbacks) is no worse than the flag path by more than the
     tolerance.
   - M-f (gate passes) is no worse by more than the tolerance.
2. **No material regression.** None of M-a, M-b, M-d or M-e is worse than on
   the flag path by more than the tolerance.
3. **Outcome:**
   - **Recommend switching** if (1) and (2) hold and at least one of M-a, M-d
     or M-e improves by more than the tolerance, which means by at least 2 runs
     in 27.
   - **Report as mixed and stop for operator review** if (1) and (2) hold but
     nothing improves beyond the tolerance, or if one metric improves beyond
     the tolerance while another regresses beyond it.
   - **Recommend not switching** if (1) fails.

No aggregate score is computed. The numbers are reported per metric and per
case, with the artifact paths and wall-clock time.

### Migration (4 commits, one gate)

1. Claims prompt, contract section and claim-backed terminology check, behind
   the switch (default `flag`).
2. Gate-check refactor in `tests/test_narration.py`: logic moved to a shared
   function, assertions unchanged.
3. Comparison script. I run it once under the rule above and report.
4. **Gate D12:** the operator decides from the report.
   - If approved, the default flips to `claims` and one later commit deletes
     the flag path.
   - If not, it stays behind the switch.

### Invariants

- The existing validator and retry/fallback are always on.
- Eval fixtures and assertions are not changed.
- The judge rubric is unchanged.
- Rule 7: claims are built from the sanitized movetext only.

---

## M14 — Claim benchmark

### Format (`evals/claim_benchmark/*.json`)

```json
{
  "id": "scandinavian_13b4",
  "source": {"pgn_fixture": "scandinavian_blitz", "move_number": 13, "side": "white", "san": "b4"},
  "expected_claims": [
    {"type": "pawn_support_lost", "subject": "c3"},
    {"type": "weak_square_created", "subject": "a3"}
  ],
  "forbidden_claims": [
    {"type": "backward_pawn_created", "subject": "c3"},
    {"type": "weak_square_created", "subject": "a4"}
  ],
  "evidence": {
    "board": {"c3_supporters_before": ["b2"], "c3_supporters_after": [], "a3_guarded_before_by": ["b2"]},
    "engine": null
  },
  "label_rationale": "c3 has no pawn on b2/d2 behind it after b4; a3 was guarded by b2",
  "status": "draft",
  "question": null
}
```

`status` (D11):
- **`draft`:** I wrote it; it isn't ground truth yet.
- **`approved`:** the operator confirmed it. Only the operator changes a label
  to this.
- **`ambiguous`:** I couldn't decide. `question` states what is unclear, and
  no expected claim is forced.

`evidence` holds the exact facts the label rests on, with the same
provenance split as the claims: board facts, and engine facts where relevant.

- `source` may instead be `{"fen": ..., "san": ...}` for positions that aren't
  in a fixture.
- **The benchmark covers board-derived claim types only.** Engine claims
  (`wdl_loss`, best move, refutation) are already pinned by the golden bands at
  1M nodes. This keeps the benchmark offline and in the fast suite.

### Metrics (`evals/claim_metrics.py`)

- **Deterministic metrics:**
  - per-type precision
  - per-type recall
  - false-positive rate by type (forbidden claims emitted)
  - unsupported-claim rate: emitted claims whose recomputed board evidence
    fails the consistency check. This should be 0.
- **From M13's comparison artifact:** narration retry, fallback and judge
  rates, reported next to the deterministic metrics but not mixed with them.
- **`tests/test_claim_benchmark.py`** runs in two tiers:
  - **Every case, whatever its status:** the `evidence.board` facts are checked
    mechanically against the board, so a label can't rest on a wrong fact.
  - **Only `approved` cases:** expected claims must be present and forbidden
    ones absent. These are the only cases that can fail the suite.
  - **`draft` and `ambiguous` cases:** the builder's output is reported next to
    the label, never asserted.
  - It prints the metrics table, computed over approved cases only.
- **A mismatch is reported, never auto-fixed.** It means either a label is
  wrong or the builder is. That follows the AGENTS.md testing rule.

### Cases: small, each with a reason

1. **Existing fixtures:**
   - 13.b4 (c3 unsupported, a3)
   - 10...b5 (c5, a6, c6 backward; c6 not double-reported as unsupported)
   - negatives: 12...Ne7, 21.h4, 22.g4, 2.g4 (g4 creates h3 only),
     kp_opposition 1.Kd3 / 2.Ke3 / 4.Ke3 / 5.d3
2. **Mirrors:** every positive case mirrored (`board.mirror()`).
3. **Targeted positives/negatives for the M6 branches:**
   - a far hole with no enemy pawn: must not be reported (the a4-after-b4
     case)
   - a square holding the mover's own pawn: never a hole
4. **Tactical examples:** Fool's Mate g4 and Scandinavian 22.g4. They are
   mostly negatives, showing structural claims don't fire on tactical blunders
   beyond what the board supports.
5. **Real endgame games from `games/my_games.pgn` (D10).** 3–5 moves.
   - **Selection is reproducible.** A throwaway scratch script (not committed)
     lists every endgame-phase move in those games, using the server's own
     `get_game_phase`. For each it records `get_quiet_concessions` and the
     king-safety change. No engine is needed to find candidates.
   - **What I pick:** positives, where a structural claim fires, and clear
     negatives, such as king moves and piece trades that shouldn't produce
     structural claims.
   - **Each case records** the game (by its index in the file) and why it was
     chosen.
   - **Engine evidence** comes from one 1M-node `analyze_pgn` run per chosen
     game, and only where a label depends on it.
   - **Coverage is limited, and the benchmark says so.** There is no opposition
     or key-square claim type, so endgame cases will mostly be negatives plus
     whatever pawn-structure claims do occur.

The target is about 25–35 cases.

### Labels must not come from the builder

Labels are written from the definitions in `ARCHITECTURE.md` and the board.
They are never produced by running `build_claims`, which would make the
benchmark a tautology.

- **Every label is committed as `draft`** (or `ambiguous`).
- **Review list:** I send the operator every draft case, with the position (a
  Lichess link), the expected and forbidden claims, the evidence and the
  reason.
- **Approval:** the operator marks each case approved in a separate commit.
  Ambiguous cases stay out of the metrics until resolved (D11).

### Migration (3 commits)

1. Format, loader, the two-tier test, metrics, and the fixture-derived cases
   (as `draft`).
2. Mirrors, targeted cases and tactical negatives (as `draft`).
3. Real endgame cases from `games/my_games.pgn` (as `draft`), then the review
   list goes to the operator.
4. The operator's approvals, as a separate commit.

### Invariants

- No existing fixture, band or eval case is changed.
- The benchmark is additive.
- The fast suite stays offline.

---

## Operator decisions (recorded)

| # | Decision |
|---|---|
| D5 | **Pydantic** for the domain models at the dict boundary; plain Python types for small internal values. |
| D6 | **Fix FEN numbering first**, as M11.0, narrowly: numbering and its regression tests only. |
| D7 | **Board-derived facts are allowed, with provenance**: engine payload / board-derived / coaching claim, visible on every piece of evidence. Board facts use no search, evaluation or LLM. |
| D8 | **Scores are dropped from the claims prompt only.** They stay in the payload, the models and telemetry. |
| D9 | **Narrow terminology check**: "backward pawn", "weak square", "hole", "outpost". Ordinary vocabulary is allowed. |
| D10 | **Real endgame cases come from `games/my_games.pgn`**, with the selection documented per case. Public master games may be added later. |
| D11 | **I draft labels** (expected/forbidden claims, exact evidence, reason); **the operator approves**. Ambiguous cases are flagged, not forced. Only approved cases are asserted. |
| D12 | **The switch is decided by the rule in M13**, fixed before the comparison runs. Mixed results stop for operator review. |

## Explicitly not in this plan

- No changes to detection, thresholds, Channel 2, `features.py` semantics,
  golden bands, the judge rubric or the MCP transport.
- Nothing from Phase C, and nothing from the "not yet" list (vector DB,
  embeddings, extra agents, runtime judge, SDK migration).
- Known limits other than FEN numbering stay in the backlog. The claims mode
  inherits two of them: king safety still ignores kings on the d/e files, and
  good-move narration is still unbuilt.
