# Prophylax

**An AI chess coach and reliability system that turns language-model chess explanations into typed claims, then checks them against the board and legal moves.**

[![CI](https://github.com/denialguo/prophylax/actions/workflows/ci.yml/badge.svg)](https://github.com/denialguo/prophylax/actions/workflows/ci.yml)

## Why it exists

A language model's chess explanation can sound convincing and still contain concrete mistakes: attacks that
don't exist, files called open with pawns on them, lines with illegal moves. Prophylax separates the two jobs:

- **Interpretation is semantic.** Reading what a sentence claims is a language problem.
- **Verification is deterministic.** Whether that claim holds is computed, never judged by a model.
- **Stockfish and python-chess are the authority** on every chess fact. The model only phrases, or proposes
  claims for checking.

## Architecture

```
Chess position / game
        │
        ▼
QUEEN (or another explanation model)       free-text explanation, untrusted
        │
        ▼
Semantic extractor                         V1: deterministic grammar + variation parser
        │                                  V2.x (experimental): LLM proposals + V1 + rules, merged deterministically
        ▼
Typed claims                               attacks(c5, g5) · is_open_file(d) · piece_count_is(white, queen, 0) …
        │                                  each bound to the board the prose has reached
        ▼
Deterministic verifier                     python-chess; replays every line for legality
        │
        ▼
VERIFIED / FALSE / UNKNOWN
```

- **Deterministic variation parser.** Binds each move in the prose to a legal line from the root.
- **Typed predicates.** Claims are checked against the verifier's own function signatures. A predicate it
  can't decide returns UNKNOWN, never a guess.
- **Hybrid extractor (experimental).** Combines V1's grammar with LLM proposals. The LLM can't give a
  verdict, and any quote, square or branch it invents is rejected.
- **Visual audit tooling.** An offline HTML viewer shows every label, extraction and verdict side by side.

## Blind evaluation

The V1 extractor and the verifier were frozen at commit `046bb19` ([now `0370ce1`](evals/queen_heldout/predictions/COMMIT_MAP.md) after a history rewrite), and their predictions were saved
read-only with SHA-256 hashes. Labels were then written blind to the predictions. Full report:
[`evals/queen_heldout/REPORT.md`](evals/queen_heldout/REPORT.md).

| Held-out result (24 unseen positions, 1,142 independently adjudicated labels) | |
|---|---|
| Correct verifier verdicts on correctly extracted claims | **81 / 81** |
| False VERIFIED among correctly extracted claims | **0** |
| VERIFIED fact precision | **86.5%** (45 / 52) |
| False factual claims caught | 32 / 92 |
| Exact variation extractions | 143 / 175 |
| False variations caught | 18 / 27 |

**The bottleneck was semantic extraction, not verification.** When a claim was read correctly, the verifier
was always right. But V1's grammar read only 91 of 468 labelled facts exactly (19.4%), so most false claims
never reached the verifier.

> **Scorer correction.** The first scoring had a candidate-matching bug, and it was fixed after the first scores
> had been read. Predictions, labels and code under test are unchanged. The numbers above are the corrected
> ones. The original scores and report are kept for transparency, and the report's first section explains the
> change.

## Experimental extractor work (post-hoc)

> **Not a generalization result.** The 24 held-out outputs had already been read when this work began, and they
> shaped it. These comparisons show whether the direction is promising, not how it performs on unseen text.

- **V2:** an LLM (Gemini Flash-Lite, recorded and replayable) reads the prose. Deterministic checks reject
  invented quotes, illegal branches and malformed predicates.
- **V2.1 hybrid:** merges V1, V2 and a few phrase rules. On the inspected held-out set:
  - Exact fact extraction rose from 19.4% to 52.4%.
  - False factual claims caught rose from 34.8% to 48.9%.
  - The cost: VERIFIED precision fell from 86.5% to 74.6%, and it produced more FALSE verdicts that no label
    supports.

  See [`evals/queen_v2_1/COMPARISON.md`](evals/queen_v2_1/COMPARISON.md).
- **V2.2:** tightens material, count and negation semantics. Evaluated on the development set only and not
  frozen. See [`evals/queen_v2_2/`](evals/queen_v2_2/).

Any generalization claim needs a frozen extractor and a new blind benchmark. The design and its limits are in
[`docs/EXTRACTOR_V2_DESIGN.md`](docs/EXTRACTOR_V2_DESIGN.md).

## Original coaching system

Before the verification work, Prophylax was a Stockfish-grounded positional coach:

- **Every narrated fact traces to a verified claim.** The language model phrases claims it's given.
  Deterministic validators reject illegal moves, unbacked squares and unsupported terms, then retry once, then
  fall back to the claims themselves.
- **Recurring weaknesses across 250 of my own games.** 18,232 plies analysed with pinned Stockfish produced
  10,642 structured claims and 1,553 MistakeEvents. A deterministic profile reports counts and medians, with
  no model involved.
- **The default narration path was chosen by a pre-registered A/B test** of 54 paired trials.

Pipeline, claim types and limits: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Reliability and evaluation infrastructure

- **Frozen predictions** with SHA-256 checks on raw outputs and predictions. A test fails if the frozen code
  changes.
- **Blind labels,** imported and validated separately from predictions. Corrections are logged and never
  silently applied.
- **Scorer regression tests,** including the matching bug above.
- **Recorded model answers** keyed by request hash, so every V2.x score replays offline with no API key.
- **An offline audit viewer** for the held-out results.
- **CI on Python 3.11 and 3.13.** The fast suite is 1,152 offline tests at the latest experimental checkpoint.
  Engine-backed golden tests and API-key narration tests run separately.

## Running locally

Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/). Nothing else for the offline commands.

```bash
git clone https://github.com/denialguo/prophylax && cd prophylax
uv sync --locked

uv run pytest                                      # fast suite, offline (~40s)
uv run python -m evals.queen_extraction            # V1 on the 18 development outputs
uv run python -m evals.queen_heldout.status        # state of the frozen blind evaluation
uv run python -m evals.queen_extraction_v2_2 dev   # experimental hybrid, replayed from recordings
uv run python -m evals.claim_metrics               # coaching-claim benchmark
```

The coach itself needs Stockfish 18 and a Gemini or Groq API key (both have free tiers):

```bash
cp app/.env.example app/.env          # fill in STOCKFISH_PATH and a key; git-ignored
set -a; . app/.env; set +a
uv run python -m app.agent --pgn tests/fixtures/scandinavian_blitz.pgn   # one-game report
uv run pytest -m golden               # engine-backed tests (~2 min)
```

## Audit viewer

```bash
uv run python -m evals.queen_heldout.audit_viewer
```

This generates `artifacts/queen_heldout_audit.html`, a self-contained page you open in a browser. It shows each
held-out case's QUEEN text, labels, extractions and verdicts, with why each one was flagged. The HTML is a
generated artifact and is intentionally not committed.

## Repository map

| Path | What |
|---|---|
| `domain/verify.py` | The deterministic verifier, frozen for the blind evaluation |
| `domain/extract.py` | V1 extractor (grammar and variation parser), frozen |
| `domain/extract_v2*.py` | Experimental V2, V2.1 and V2.2 extractors |
| `evals/queen_heldout/` | **Frozen blind V1 evaluation:** predictions, labels, corrected scores, report |
| `evals/queen_v2/`, `queen_v2_1/`, `queen_v2_2/` | **Post-hoc experiments:** recordings, comparisons, development scores |
| `evals/queen_benchmark/` | Development set: 18 QUEEN outputs and their labels |
| `app/`, `mcp_server/`, `history/` | The coach: agent and narration, the Stockfish server, player history |
| `docs/` | Design notes, known issues, the post-experiment checklist |
| [`AGENTS.md`](AGENTS.md) | The rules the project was built under |

## License

Prophylax's own code is under the [MIT license](LICENSE). That doesn't extend to third-party software, models
or data, which keep their own terms, including Stockfish and python-chess (both GPL). QUEEN is an external
model that this repository evaluates. Its weights aren't included, and nothing here grants rights to it. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
