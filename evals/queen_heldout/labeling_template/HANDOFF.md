Please label the chess analysis outputs in this folder.

Read `LABELING_INSTRUCTIONS.md` first, then go through every `cases/<id>/response.txt`, labelling every concrete statement and every line of moves in `labels.jsonl`. Label what the text means and whether it is true on the board; a false statement is labelled as written.

Work only from this folder: please don't open any other project files or repositories, or look for anyone else's reading of these texts. You'll need Python with `chess` and `jsonschema` (`pip install chess jsonschema`).

Where you can't settle something, put it in `review.jsonl` rather than guessing.

When you're done, run `python validate_labels.py`, fill in `label_meta.json`, and return `labels.jsonl`, `review.jsonl` and `label_meta.json`.
