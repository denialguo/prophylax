# Extractor V2

**Status: experimental, post-hoc, not production.** No V2.x extractor is frozen or wired into the coach.
Every held-out number for V2 and V2.1 comes from a set that had already been inspected. V2.2 was evaluated
on the development set only.

## Why V1 fell short

The frozen held-out experiment showed the verifier is strong once it has the right claim: every verdict on a correctly extracted fact was right, and nothing false was verified. The bottleneck was reading. V1's grammar matched only 91 of 468 labelled facts (19.4%), so most false claims never reached the verifier. Hand-written patterns miss ordinary phrasing, and one unsupported clause could drop the concrete facts beside it. V1's move parser did well, with 143 of 175 lines exact, so V2 keeps it.

## The separation

```
QUEEN response
    |
    +--> V1 move parser ---------------> variations ---> verifier
    |         |
    |         +--> catalogue of positions
    |                   |
    +--> structured LLM (reads prose) <-+
              |
              v
         deterministic checks  --rejected--> PARSE_ERROR or AMBIGUOUS (never judged)
              |
              v
         typed fact candidates ---------------------> verifier
```

Parsing and truth are separate because they fail differently. Reading prose is a judgement about language, which a model does well and rules do badly. Truth on a chess board is computable, so nothing should be left to judgement. A model allowed to decide truth could also tell a convincing wrong answer, and nothing would catch it.

## What the LLM may do

It answers one question: what concrete claim does this text assert, and about which position? Its answer is a proposal.

It has no field for truth, and an answer containing one is rejected. It never sees a verifier result. It is told to transcribe false claims as written, and QUEEN's text reaches it only as JSON string values, marked as untrusted.

## What deterministic code decides

- **The verifier (`domain/verify.py`, frozen) alone decides** VERIFIED, FALSE or UNKNOWN, and whether a line is legal.
- **Spans** come from finding the model's quote in QUEEN's text. A quote that isn't there is rejected.
- **Predicates and arguments** are checked against the verifier's own function signatures.
- **Branches** must replay legally, and may not join moves from separate catalogued lines.
- **Referents:** a square the text never wrote must be reached by a move in the branch or be the only piece of that kind on the board. Otherwise the claim is AMBIGUOUS. A written square can't be swapped for another, which stops a false claim being quietly corrected.
- **Change claims:** words like "creates" and "opens" force the change check even if the model forgets it. This can only make a claim harder to verify. A pawn that just moved is compared with itself on its origin square.
- **Negative claims** are the opposite of the predicate, but only about a piece that is there. A denial about an absent piece is UNKNOWN.
- **Bare moves** are dropped as facts, because the line parser owns them.
- **A sentence the model skipped** is UNSUPPORTED and marked `residual`, matching V1's behaviour. These are counted separately so they can't pass as the model's own reading.

A failed or truncated request stops the run instead of being scored as a gap.

## Reproducibility

Every model answer is recorded under `evals/queen_v2/recordings/` with its model, provider, settings and prompt version. Scores replay from those recordings with no model and no key. A changed prompt or model can't reuse an old answer, because the request is part of the lookup key.

```bash
uv run python -m evals.queen_extraction_v2 score --set dev
uv run python -m evals.queen_extraction_v2 compare
```

Variations and negative claims go through the unmodified V1 scoring code, so V1 and V2 are compared on one ruler. A test confirms V1's predictions still give V1's numbers on that path. Negative claims are carried through it under a separate predicate name, so a claim only matches a label of the same polarity.

## Why the held-out comparison is post-hoc

The 24 held-out outputs, their labels and their failures were all read before V2 existed, and they shaped its requirements. V2's prompt was tuned only on the development set, and the held-out set was run once with the frozen prompt, but that doesn't make the set blind again. `COMPARISON.md` is labelled accordingly. It can show whether V2 is worth taking further. It can't show how V2 generalizes.

## What needs a fresh blind benchmark

Any claim about how V2 performs on text it hasn't seen, including any figure for a résumé or a release. That needs V2 frozen first (code, prompt, model, settings), then new positions and QUEEN outputs, labelled blind before predictions are opened. The parent/child label hierarchy should be defined in advance too ([checklist](POST_EXPERIMENT_CHECKLIST.md)).

## V2.1: a conservative hybrid

V1 and V2 caught different false claims, so V2.1 (`domain/extract_v2_1.py`) runs V1's grammar, V2's recorded proposals and a small set of phrase rules, then merges them deterministically. Its prompt didn't change: it replays V2's recordings, so its numbers show what the architecture adds without better model output.

- **Duplicates** merge when spans overlap and predicate, arguments, polarity and branch agree, keeping provenance and the stricter requirements.
- **Conflicts** are never settled by a source's name. Two sources agreeing settles one, then squares written in the quote, or for a board, the moves written before the claim. Otherwise the claim becomes AMBIGUOUS and keeps both readings for audit.
- **Phrase rules** cover counts, material, explicit negatives and explicit attacks. Without a stated side or a unique referent they report AMBIGUOUS. "Eyes", "targets" and "pressure" never become attacks.
- **"No longer"** means the denial holds now and the fact held before the last move.
- **Quotes** align exact, then normalized, then strictly fuzzy, and only if unique. Each claim records how.

Provenance never reaches a verdict. See `evals/queen_v2_1/COMPARISON.md` for the post-hoc comparison.

**Known risk:** material rules read "White is a queen down" as a balance of exactly -9. If QUEEN is speaking loosely, the verifier calls a fair statement FALSE. The report counts FALSE verdicts that no label confirms, because no other metric sees them. V2.2 addresses this.

## V2.2: tighter semantics (development only)

`domain/extract_v2_2.py` keeps V2.1's merger unchanged and adds a gate over every source:

- **Material:** a balance needs a written figure ("nine points down", "material balance is +3"). Relative
  wording ("a queen down", "down the exchange") is UNSUPPORTED.
- **Counts:** "has no X", "has two X", "only one X remains" and inventories. "Has a rook" is AMBIGUOUS, because
  it says that one exists, not that exactly one does.
- **Negation is clause-scoped.** "The rook does not attack e7 but the bishop does" gives a negative rook claim
  and a positive bishop claim.

V2.2 was not run on the held-out set. On the 18 development cases it matches V2.1 (96/106 exact, 35/35
labelled VERIFIED precision, 0 false acceptances) and produces fewer unconfirmed FALSE verdicts (11 against
15). Its count and negation rules are supported by synthetic tests only, because the development set has
few such labels. `evals/queen_v2_2/FALSE_ALARM_ANALYSIS.md` classifies V2.1's 31 unconfirmed FALSE verdicts.
A short stronger-model comparison, covering 5 of the 18 development cases, is recorded in
`evals/queen_v2_2/` and found no clear gain.

## What this does not claim

This does not verify QUEEN's explanations end to end. It checks the concrete claims it manages to extract. Strategic judgement is out of scope, and a claim the extractor misses is never checked at all. A VERIFIED result means the verifier confirmed that reading of the claim on that board, and the reading itself can still be wrong.
