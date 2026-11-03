# Semantic issues in the frozen code

Bugs found in the extractor and verifier frozen at `046bb19` for the held-out QUEEN evaluation.
**None are fixed**, because the evaluation measures that exact code. Fix them only after the
held-out report, as regression tests first, and rerun the development benchmark when you do
([checklist](POST_EXPERIMENT_CHECKLIST.md)).

## Open

### 1. A king move on the e-file to g or c is read as castling, whatever its rank (false VERIFIED)

`domain/extract.py`, `_Mention.text_san`. The check compares the two squares' ranks, but not that
the king starts on its home rank. The stated squares are then replaced by `O-O` or `O-O-O`.

| Text, in `r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1` | Result | Should be |
|---|---|---|
| `white king e1-g1` | VERIFIED | VERIFIED |
| `white king e4-g4` | VERIFIED, as `e1g1` | FALSE: no king on e4, and not a king move |
| `white king e7-c7` | VERIFIED, as `e1c1` | FALSE |

Only reachable when castling is legal and the text names such a move. Not seen in the 18 development
outputs; the held-out run will show whether it occurs.

### 2. The stated colour of a captured piece is dropped (false VERIFIED)

`domain/extract.py`, `MOVE` regular expression. `takes (?:white|black) piece` keeps the type only, and
`Ply` has no field for a captured piece's colour.

| Text, in `r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 4 5` | Result | Should be |
|---|---|---|
| `white bishop c4 takes black pawn f7` | VERIFIED | VERIFIED |
| `white bishop c4 takes white pawn f7` | VERIFIED | FALSE |

The move itself is legal, so only the description is wrong. It would matter if the model misnames colours.

## Known limits left in place for the evaluation

- "Controls" is read as `attacks`, so a piece controlling a square that holds its own piece judges FALSE.
- An unstated branch point stops the line, so facts in it stay UNKNOWN (safe, but it loses coverage).
- "Eyes", "targets", "en prise" and "wins a pawn" are not extracted.
- Negative claims are not extracted; the held-out scoring counts them as missed.
- `is_terminal` cannot see a fivefold repetition from a bare FEN.
