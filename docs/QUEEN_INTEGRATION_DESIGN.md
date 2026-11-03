# Design: using QUEEN in production, after the held-out evaluation

**Status: proposal, nothing built.** No integration work starts until the held-out report exists and
the go/no-go below has been decided. The thresholds are proposals for review, and should be agreed
**before** the results are seen.

## The idea

QUEEN writes richer explanations than our narrator but states false chess facts. Prophylax can check
simple facts. So QUEEN could propose, and only what survives checking could reach the user:

```
position, flagged move
  │
  ├─ today's path: Stockfish → features → claims ────────────────┐
  │                                                              ▼
  └─ QUEEN text → extraction → verifier → VERIFIED facts ──▶ claims with their own provenance
                                  │                              │
                                  ├─ FALSE: dropped, logged      ▼
                                  └─ UNKNOWN: dropped      narrator → validator → report
```

The current claims path keeps working with QUEEN off or down. QUEEN only ever adds claims.

## Rules that stay fixed

1. **QUEEN's prose never reaches the user.** Only verified facts do, and the narrator phrases them.
2. **UNKNOWN is not a pass.** It is dropped. So are FALSE and every extraction status except EXTRACTED.
3. **Stockfish stays the authority on evaluation.** QUEEN's moves, lines and scores are never engine evidence.
4. **Fail closed.** QUEEN failing or slow means a report without its facts, not a failed report.
   `ClaimConsistencyError` keeps stopping the report, and is not caught here.
5. **The rule on model output stays.** A claim with no deterministic evidence behind it does not exist.

## Go / no-go (proposed)

From the held-out report, on the frozen `046bb19` code:

| Metric | Go | Why |
|---|---|---|
| False VERIFIED | 0, or every one traced to a fixable cause | One false fact stated as verified is the failure this design exists to stop |
| Verified-claim precision | ≥ 98% | The user cannot tell a verified fact from a wrong one |
| New verified facts per flagged move | Enough to change a typical report | Tiny coverage isn't worth a GPU dependency |

Capture rate is **not** a gate for production: facts that are dropped never reach the user, so a
low capture rate costs coverage rather than correctness. It matters for QUEEN as a research finding.

If the verdict is no-go, keep the verifier and extractor as an evaluation tool and stop here. That
is a useful outcome, and still a good story.

## Steps if the verdict is go

1. **Unfreeze deliberately.** Record the evaluation as complete, delete the frozen-code guard test,
   and fix `docs/SEMANTIC_ISSUES.md`.
2. **Decide what counts as a useful fact.** Many verified facts are trivially true ("the king sits
   on e8"). Passing them all on would produce long, dull reports. Needs a filter, ideally tied to
   the flagged move and engine line. This is the main open design question.
3. **New claim type.** `verified_model_fact`, with a `verifier` provenance alongside `engine`,
   `server_features` and `board`, and the source text kept for audit. Bump `CLAIMS_VERSION`.
   Stored history is unaffected unless QUEEN is also run over it, which should be separate.
4. **A hypothesis source interface.** `hypotheses(fen) -> text`, called off the narration path, with
   a timeout and a cache keyed by FEN, model revision and settings. Behind
   `PROPHYLAX_HYPOTHESIS_SOURCE=off|queen`, default `off`.
5. **Compare before switching.** A pre-registered, paired comparison with a rule written first, as
   with D12: reports with and without QUEEN facts, on the same flagged moves. It must not reduce gate
   passes or judge passes beyond tolerance, and it has to add verified concrete facts.
6. **Switch the default only if that rule says so.**

## Serving

The public Space is rate-limited, and is not a production dependency. The realistic options:

| Option | For | Against |
|---|---|---|
| Stay offline, precompute per game in batches | No live GPU; suits the history feature | Not usable for an arbitrary new position right away |
| Paid GPU endpoint | Interactive | Cost, and a new external dependency, which `AGENTS.md` rule 3 would have to allow |
| Local model | No external call, satisfying rule 3 | Needs a capable GPU; the release ships custom model code |

Start offline. It needs no change to the external-call rule for the interactive path.

## Risks

- **Grammar brittleness.** Extraction is tied to one output format, so pin the model revision and rerun
  the benchmark when it changes.
- **Selection effects.** Verified facts are the easily checked ones. Reports could drift toward
  trivia rather than toward what actually mattered.
- **Maintenance.** Each predicate or phrasing added needs labels, and a fresh held-out measure when
  the change is large.
- **Licensing.** Confirm the QUEEN model's terms before any production use. Not yet checked.
