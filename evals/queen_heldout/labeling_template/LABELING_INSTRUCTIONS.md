# Labelling instructions

You are labelling what a chess model's written analysis **claims**, one output at a time. Your labels become the reference answer for an evaluation, so they need to reflect the text and the board, and nothing else.

Work only from this folder. Do not open any other project files or repository while labelling, and do not look for other people's or other systems' readings of these texts.

## What you have

| Path | Contents |
|---|---|
| `cases/<id>/case.json` | The case id and root FEN |
| `cases/<id>/response.txt` | The model's text, unaltered |
| `labels.schema.json` | The format of a label |
| `predicates.json` | The allowed predicates and what they mean |
| `labels.jsonl` | Your labels, one JSON object per line (starts empty) |
| `review.jsonl` | Questions you could not settle (starts empty) |
| `label_meta.json` | Fill this in when you finish |
| `label_tools.py` | Shows a board after a line; finds a quote's span |
| `validate_labels.py` | Checks your files |

## The job

Go through each `response.txt` from start to finish and label **every concrete statement and every line of moves**. Do not sample: a statement left out cannot be scored at all.

You are not grading the model's chess advice, and you are not guessing what any software would do with the text. Label the meaning.

Keep two questions apart:

1. **What does the text say?** This is the label's class, predicate and arguments. A false statement is labelled exactly as written, and never corrected.
2. **Is it true?** This is `chess_truth`, judged on the board the statement is about.

## The four kinds of label

### EXTRACTABLE concrete fact

`"kind": "fact"`, `"extraction_class": "EXTRACTED"`.

A statement that a careful chess reader can resolve **uniquely** to a predicate in `predicates.json`, with its arguments and its board. Exact square names need not appear in the same sentence. Use context and the board:

- "The pawn immediately attacks the knight." If the relevant branch has exactly one plausible pawn and one plausible knight, resolve it to `attacks`.
- "Rook and six pawns against a lone knight." This asserts piece inventory, so write one `piece_count_is` label per count.

One statement can yield several labels: one per fact, each with its own truth value.

Record:

- `predicate` and `args`. Include `piece` or `color` when the text states or clearly implies them.
- `branch`: the moves in UCI from the root to the board the statement is about. `[]` is the root position.
- `chess_truth`: `TRUE` or `FALSE` on that board. If the line reaching it has already gone illegal or passed the end of the game, there is no board: use `"branch": null` and `"chess_truth": "UNDETERMINED"`, and only in that case.

**How truth works for some statements:**

- A named piece is part of the claim. "Attacks the rook on f8" is false if f8 holds no rook, even if the square is covered.
- A statement that something *changes* ("creates a passed pawn", "opens the e-file"): use the predicate for the resulting state, on the board after the move, and judge the whole statement. If it already held before the move, nothing was created or opened, so `chess_truth` is `FALSE`. Say so in `notes`.
- A statement that a game has ended by rule still counts as a fact about that board.

### UNSUPPORTED

`"kind": "non_fact"`, `"extraction_class": "UNSUPPORTED"`.

Statements outside the predicate vocabulary: strategic evaluations, initiative, pressure, compensation, coordination, prophylaxis, zugzwang, plans, engine scores, and so on. The same applies to anything the vocabulary cannot express: negative statements ("is not attacked"), what could or would happen, net gains over a sequence, moves offered as an idea or threat rather than played in a line.

If a sentence also separately states a supported fact, label that fact too.

Set `false_concrete` to `true` when the statement is nevertheless concrete and false on the board, even though no predicate represents it. Otherwise `false`.

### AMBIGUOUS

`"kind": "non_fact"`, `"extraction_class": "AMBIGUOUS"`, or a variation with that class.

Only when a careful human **cannot** uniquely resolve the intended piece, square, branch or relation, for example two knights either of which could be meant, or a word that could mean either a direct attack or a longer-term aim with nothing to settle it.

Do not use AMBIGUOUS because a sentence is awkward to parse or leaves out square names. If you can tell what it means, resolve it. Explain the competing readings in `notes`. Set `false_concrete` as above if every reading is false.

### VARIATION

`"kind": "variation"`.

Every line of moves the text asserts as played out, including the `CRITICAL_LINE:` entry. You do not need to label the `BEST_MOVE:` or `PROMISING_MOVES:` entries.

- One label per complete line, from the **root** to its last asserted move. A line continuing over several sentences is one label. A sub-variation is its own label and includes the moves leading to its branch point.
- `moves`: UCI from the root, up to and including the first illegal move.
- `legal`: `true` if every move is legal in sequence. A move after the game has ended by rule (mate, stalemate, insufficient material) is illegal.
- `annotations_hold`: `true` if all the line's stated captures, checks and mates are correct, including which piece is captured. `x`/"takes" and `+`/`#` are claims. A `-` between squares states a move without a capture.
- `failure_ply`: index in `moves` of the first problem, or `null` if there is none. It must be set exactly when `legal` or `annotations_hold` is false.
- If a careful reader cannot tell where a line branches from, use `"extraction_class": "AMBIGUOUS"` with your best statement of the options in `notes`, and `legal: true`, `annotations_hold: true`, `failure_ply: null`, `moves: []`.

## Reading the model's notation

- Moves are verbose: `white knight e2-f4`, `white bishop g5 takes black knight f6`, sometimes with `+` or `#`. It may also use standard notation such as `Qxd4`.
- `white king e1-g1` is castling.
- `white pawn c7-c8white queen` is a promotion; in UCI, `c7c8q`.
- Lines are grouped as `I.`, `II.`, `I.a.` and so on. `I.a.` usually branches from line `I.`.
- Move numbers usually count from the root: the root side's first move is 1, however the FEN is numbered.

## Spans and provenance

Each label needs `case`, `span_start`, `span_end` and `source_text`, where `source_text` is exactly the text at that span. Use the smallest span containing the statement. Find it with:

```bash
python label_tools.py span h01_tactical "the exact quote"
```

If the referent is elsewhere ("the knight", "it"), say what you resolved it to in `notes`. Give each label a unique `label_id`. Add `notes` whenever you had to interpret.

To check a board after a line:

```bash
python label_tools.py board h01_tactical e2e4 e7e5
```

Check truth against the board, not your memory of the position. python-chess is available for this, and you may write your own checks with it.

## When you are not sure

Do not guess. Add a line to `review.jsonl` instead of a label:

```json
{"review_id": "r1", "case": "h01_tactical", "source_text": "the exact quote", "question": "what you could not settle", "options": ["reading A", "reading B"], "leaning": "optional"}
```

Use it sparingly, for genuine doubt about the meaning, the truth or how a rule applies. Also use it if a promotion count holds but a piece stands on the pawn's path.

## Finishing

```bash
python validate_labels.py
```

Fix anything it reports. It checks format, spans, branches, predicates and hashes only, and says nothing about whether your readings are right. Then fill in `label_meta.json` and return `labels.jsonl`, `review.jsonl` and `label_meta.json`.
