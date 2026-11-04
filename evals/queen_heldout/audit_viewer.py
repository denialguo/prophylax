"""Offline visual audit of the frozen held-out experiment: every item in scores.json["details"],
plus the false claims the system caught, with QUEEN's text, the gold label, the frozen candidate,
SAN lines and annotated boards. One static HTML file; nothing loads from the network.

Read-only. Experiment files are only ever opened for reading, and the output is refused anywhere
under evals/queen_heldout or domain. It also refuses scores that do not name the current labels
and predictions, since the page would then describe something else.

A reviewer can agree, disagree or be unsure about each finding. Those decisions stay in the browser
and in JSON files the reviewer exports. They never change a label or a score.

Usage: uv run python -m evals.queen_heldout.audit_viewer [--out FILE]"""
import argparse
import collections
import hashlib
import json
import os
import random
import re

import chess
import chess.svg

from domain.verify import PREDICATES, material_inventory
from evals.queen_heldout import score as sc

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_OUT = os.path.join(ROOT, "artifacts", "queen_heldout_audit.html")
PROTECTED = (os.path.join(ROOT, "evals", "queen_heldout"), os.path.join(ROOT, "domain"))
GOLD, EXTRACTOR, FAILING = "#2563eb", "#ea580c", "#dc2626"
FILL_ALPHA, ARROW_ALPHA = "59", "cc"
CAUGHT_FACT, CAUGHT_VARIATION = "caught_false_fact", "caught_false_variation"
SAMPLE_SEED = 20261104
ERROR_ANALYSIS = (("extractable_claim_missed", 15), ("board_binding_wrong", None), ("predicate_right_args_wrong", None),
                  ("hallucinated_on_non_fact", 5), ("variation_missed", 10), (CAUGHT_FACT, 5))
FINDINGS = {
    "extractable_claim_missed": "The extractor missed a concrete claim it should have read.",
    "negative_claim_missed": "The extractor missed a concrete negative claim.",
    "predicate_right_args_wrong": "The extractor read the right predicate with the wrong arguments.",
    "board_binding_wrong": "The extractor read the right claim but bound it to the wrong board, or to none.",
    "hallucinated_on_non_fact": "The extractor produced a claim from text labelled as not a checkable fact.",
    "extraction_with_no_label": "The extractor produced a claim that no label covers.",
    "unsupported_misclassified": "The text is unsupported, but the extractor did not classify it as unsupported.",
    "ambiguous_misclassified": "The text is ambiguous, but the extractor did not classify it as ambiguous.",
    "variation_missed": "The extractor did not read this line exactly.",
    "variation_with_no_label": "The extractor read a line that no label covers.",
    CAUGHT_FACT: "The system caught this false claim.",
    CAUGHT_VARIATION: "The system caught this false line.",
}
SENTENCE_END = re.compile(r"[.!?](?=\s+[A-Z])|\n")
PIECE_LETTERS = (("king", "K"), ("queen", "Q"), ("rook", "R"), ("bishop", "B"), ("knight", "N"), ("pawn", "P"))


# ── replay ───────────────────────────────────────────────────────────────────
def replay(fen, moves):
    """Plays UCI moves from fen until one cannot be played. The board is the last legal position."""
    board = chess.Board(fen)
    sans, failed_at = [], None
    for i, uci in enumerate(moves or []):
        try:
            move = board.parse_uci(uci)
        except ValueError:
            failed_at = i
            break
        sans.append(board.san(move))
        board.push(move)
    return {"board": board, "sans": sans, "failed_at": failed_at}


def line_tokens(fen, moves, fail=None, other=None):
    """A line as numbered SAN. A move that cannot be played stays as UCI and so does all that follows,
    since no position exists to read it on. fail marks a ply; other marks where a second line differs."""
    moves = moves or []
    played = replay(fen, moves)
    start = chess.Board(fen)
    number, white = start.fullmove_number, start.turn == chess.WHITE
    tokens = []
    for i, uci in enumerate(moves):
        prefix = f"{number}. " if white else (f"{number}... " if i == 0 else "")
        text = played["sans"][i] if i < len(played["sans"]) else uci
        cls = "fail" if i == fail or i == played["failed_at"] else ("unplayed" if i >= len(played["sans"]) else "")
        if other is not None and not cls and (i >= len(other) or other[i] != uci):
            cls = "diff"
        tokens.append({"t": prefix + text, "c": cls})
        number += not white
        white = not white
    return tokens


def san_line(fen, moves):
    return " ".join(t["t"] for t in line_tokens(fen, moves))


# ── QUEEN's text ─────────────────────────────────────────────────────────────
def source_context(text, start, end, pad=160):
    """Five consecutive pieces of text around a span: nearby, sentence start, span, sentence end, nearby."""
    sentence_start = 0
    for m in SENTENCE_END.finditer(text):  # the whole text: a boundary's lookahead may reach the span
        if m.end() > start:
            break
        sentence_start = m.end()
    m = SENTENCE_END.search(text, end)
    sentence_end = m.end() if m else len(text)
    return {"nearby_before": text[max(0, sentence_start - pad):sentence_start], "sentence_before": text[sentence_start:start],
            "span": text[start:end], "sentence_after": text[end:sentence_end], "nearby_after": text[sentence_end:sentence_end + pad]}


# ── which boards to show ─────────────────────────────────────────────────────
def _marks(claim, color):
    return [(claim["predicate"], claim.get("args") or {}, color)]


def select_fact_boards(gold, candidate):
    """Boards for a fact or non-fact, as specs. The gold claim's own board comes first. A candidate on
    that board is drawn on it; on any other board it gets a second one, labelled as the extractor's."""
    has_claim = candidate is not None and candidate.get("predicate")
    if gold is not None and gold["kind"] == "fact":
        branch = gold["branch"]
        if branch is None:
            panels = [{"role": "root", "title": "Root position (gold branch undetermined)", "moves": [], "marks": [], "arrows": []}]
        else:
            panels = [{"role": "gold", "title": "Gold board", "moves": branch, "marks": _marks(gold, GOLD), "arrows": []}]
        if has_claim and candidate.get("board_moves") is not None:
            if candidate["board_moves"] == branch:
                panels[0]["marks"] += _marks(candidate, EXTRACTOR)
            else:
                panels.append({"role": "extractor", "title": "Extractor board", "moves": candidate["board_moves"],
                               "marks": _marks(candidate, EXTRACTOR), "arrows": []})
        elif has_claim:
            panels[0]["notes"] = ["The extractor bound this claim to no board, so there is no extractor board to show."]
        return panels
    if has_claim and candidate.get("board_moves") is not None:
        return [{"role": "extractor", "title": "Extractor board", "moves": candidate["board_moves"], "marks": _marks(candidate, EXTRACTOR), "arrows": []}]
    return [{"role": "root", "title": "Root position", "moves": [], "marks": [], "arrows": []}]


def _failure_panel(moves, ply, who, color=FAILING):
    return {"role": who.lower(), "title": f"{who}: board before the failure at ply {ply + 1}", "moves": moves[:ply], "marks": [],
            "arrows": [(moves[ply], color)] if ply < len(moves) else []}


def select_variation_boards(gold, candidate):
    """Boards for a variation: the position just before each failure with the attempted move drawn,
    otherwise the position before the two lines first differ, otherwise the end of the line."""
    gold_moves = (gold or {}).get("moves") or []
    cand_moves = (candidate or {}).get("moves") or []
    gold_ply = (gold or {}).get("failure_ply")
    cand_ply = (candidate or {}).get("verifier_first_false_ply")
    panels = []
    if gold_ply is not None:
        panels.append(_failure_panel(gold_moves, gold_ply, "Gold"))
    if cand_ply is not None and (cand_ply != gold_ply or cand_moves != gold_moves):
        panels.append(_failure_panel(cand_moves, cand_ply, "Extractor"))
    if panels:
        return panels
    if gold_moves and cand_moves and gold_moves != cand_moves:
        first = next((i for i, (a, b) in enumerate(zip(gold_moves, cand_moves)) if a != b), min(len(gold_moves), len(cand_moves)))
        arrows = [(moves[first], color) for moves, color in ((gold_moves, GOLD), (cand_moves, EXTRACTOR)) if first < len(moves)]
        return [{"role": "difference", "title": f"Board before the lines first differ, at ply {first + 1}", "moves": gold_moves[:first], "marks": [], "arrows": arrows}]
    moves = gold_moves or cand_moves
    return [{"role": "line", "title": "End of the line", "moves": moves, "marks": [], "arrows": []}]


# ── drawing ──────────────────────────────────────────────────────────────────
def _sq(name):
    try:
        return chess.parse_square(name)
    except (ValueError, TypeError):
        return None


def _file_squares(name):
    return list(chess.SquareSet(chess.BB_FILES[chess.FILE_NAMES.index(name)])) if name in chess.FILE_NAMES else []


def claim_marks(predicate, args, color, board=None):
    """(arrows, filled squares) showing a claim. Counts and balances draw nothing: their evidence is text."""
    arrows, fills = [], []

    def arrow(a, b):
        tail, head = _sq(args.get(a)), _sq(args.get(b))
        if tail is not None and head is not None:
            arrows.append((tail, head))
            return tail, head
        return None

    def fill(*names):
        fills.extend(s for s in (_sq(args.get(n)) for n in names) if s is not None)

    if predicate in ("attacks", "defends"):
        arrow("source", "target")
    elif predicate in ("piece_at", "square_empty", "is_passed_pawn", "promotion_distance_is"):
        fill("square")
    elif predicate in ("is_open_file", "is_semi_open_file", "is_file_clear", "has_passed_pawn"):
        fills.extend(_file_squares(args.get("file")))
    elif predicate in ("ray_clear", "is_open_diagonal"):
        ends = arrow("source", "target")
        fill("source", "target")
        if ends:
            fills.extend(chess.SquareSet.between(*ends))
    elif predicate == "blocks":
        arrow("slider", "blocker")
        fill("blocker")
    elif predicate == "pinned_against":
        arrow("pinner", "target")
        fill("pinned")
    elif predicate == "is_check" and board is not None and board.king(board.turn) is not None:
        fills.append(board.king(board.turn))
    return arrows, fills


def _uci_squares(uci):
    return _sq(uci[:2]), _sq(uci[2:4])


def draw_board(board, panel):
    arrows, fills = [], {}
    for predicate, args, color in panel["marks"]:
        a, f = claim_marks(predicate, args, color, board)
        arrows += [chess.svg.Arrow(t, h, color=color + ARROW_ALPHA) for t, h in a]
        fills.update({s: color + FILL_ALPHA for s in f})
    for uci, color in panel["arrows"]:
        tail, head = _uci_squares(uci)
        if tail is not None and head is not None:
            arrows.append(chess.svg.Arrow(tail, head, color=color + ARROW_ALPHA))
            fills[tail] = fills[head] = color + FILL_ALPHA
    svg = chess.svg.board(board, arrows=arrows, fill=fills, lastmove=board.peek() if board.move_stack else None,
                          check=board.king(board.turn) if board.is_check() else None)
    return re.sub(r"<defs>.*?</defs>", "", svg, flags=re.S)  # one shared copy goes in the page


def shared_defs():
    return re.search(r"<defs>.*?</defs>", chess.svg.board(chess.Board(), check=chess.E1), re.S).group(0)


def material_text(board):
    inv = material_inventory(board)

    def side(counts):
        return " ".join(f"{letter}{counts[name]}" for name, letter in PIECE_LETTERS[1:] if counts[name]) or "no pieces"
    return f"White {side(inv.white)} ({inv.white_points}), Black {side(inv.black)} ({inv.black_points}); White minus Black {inv.balance:+d}"


def render_panel(fen, panel, store):
    """A spec with its board drawn. Identical boards share one stored SVG."""
    played = replay(fen, panel["moves"])
    board = played["board"]
    svg = draw_board(board, panel)
    index = store.setdefault(svg, len(store))
    notes = panel.get("notes", []) + [f"{'White' if board.turn == chess.WHITE else 'Black'} to move. {material_text(board)}.", f"Legal moves here: {board.legal_moves.count()}."]
    if played["failed_at"] is not None:
        notes.append(f"Stopped early: {panel['moves'][played['failed_at']]} cannot be played, so this is the last legal position.")
    for uci, _ in panel["arrows"]:
        try:
            notes.append(f"Attempted move {uci} ({board.san(board.parse_uci(uci))}) is legal here.")
        except ValueError:
            notes.append(f"Attempted move {uci} is illegal here.")
    return {"role": panel["role"], "title": panel["title"], "svg": index, "line": san_line(fen, panel["moves"]) or "root position", "notes": notes}


def verifier_on_gold(fen, gold):
    """The frozen verifier asked the gold claim on the gold board, to help judge the label itself."""
    if gold is None or gold["kind"] != "fact" or gold["branch"] is None or gold["predicate"] not in PREDICATES:
        return None
    board = replay(fen, gold["branch"])["board"]
    try:
        result = PREDICATES[gold["predicate"]](board, **gold["args"])
    except (ValueError, TypeError) as e:
        return {"status": "UNKNOWN", "reason": str(e)}
    return {"status": result.status, "reason": result.reason}


def verifier_disagrees(gold, asked):
    """The gold truth and the bare predicate differ on the gold board: worth a look, not a label error.
    A change claim ("opening the c-file") is rightly FALSE when the file was already open, though the
    predicate holds. A negative label's truth is about the denial, so TRUE there expects FALSE."""
    if asked is None or asked["status"] == "UNKNOWN" or gold["chess_truth"] == "UNDETERMINED":
        return False
    holds = (gold["chess_truth"] == "TRUE") == (gold.get("polarity", "positive") == "positive")
    return asked["status"] != ("VERIFIED" if holds else "FALSE")


# ── items ────────────────────────────────────────────────────────────────────
def _overlaps(row, c):
    return sc._overlap(row["span_start"], row["span_end"], c["span_start"], c["span_end"])


def _candidate_view(c):
    keys = ("status", "predicate", "args", "board_moves", "moves", "change_from", "guards", "verifier_status", "verifier_reason", "verifier_first_false_ply", "source_text", "reason")
    return {k: c[k] for k in keys if k in c} if c else None


def _find_fact(facts, got, label):
    """The frozen candidate behind a details row, which holds only some of its fields."""
    for c in facts:
        if (c.get("predicate"), c.get("args"), c.get("board_moves"), c.get("status")) == (got.get("predicate"), got.get("args"), got.get("board_moves"), got.get("status")) and _overlaps(label, c):
            return c
    return got


def _common_prefix(a, b):
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))


def _find_variation(variations, label):
    """The closest extracted reading of a gold line: its exact moves, else the longest shared start."""
    moves = label.get("moves") or []
    options = [v for v in variations if v["kind"] not in sc.STRUCTURED and (_overlaps(label, v) or (v["moves"] == moves and v["status"] == "EXTRACTED"))]
    return max(options, key=lambda v: (v["moves"] == moves and v["status"] == "EXTRACTED", _common_prefix(v["moves"] or [], moves)), default=None)


def _overlapping_candidates(facts, span):
    keys = ("status", "predicate", "args", "verifier_status", "reason", "source_text")
    return [{k: c.get(k) for k in keys} for c in facts if _overlaps(span, c)]


def _gold_view(label, fen):
    if label is None:
        return None
    keys = ("kind", "extraction_class", "predicate", "args", "polarity", "chess_truth", "branch", "moves", "legal", "annotations_hold", "failure_ply", "false_concrete", "notes", "source_text")
    view = {k: label[k] for k in keys if k in label}
    if label["kind"] == "fact":
        view["polarity"] = label.get("polarity", "positive")
        if label["branch"] is not None:
            view["branch_san"] = san_line(fen, label["branch"]) or "root position"
    return view


def _truth(label):
    if label is None:
        return "no label"
    if label["kind"] == "fact":
        return label["chess_truth"]
    if label["kind"] == "non_fact":
        return "FALSE" if label["false_concrete"] else "not a fact"
    return "FALSE" if _line_fails(label) else "TRUE"


def _line_fails(label):
    return label["extraction_class"] == "EXTRACTED" and not (label["legal"] and label["annotations_hold"])


def _is_false(label):
    return _truth(label) == "FALSE"


def _make_item(item_id, category, case, label, candidate, row, prediction, text, store):
    fen = prediction["fen"]
    span = label or (candidate if candidate and "span_start" in candidate else None)
    is_variation = (label or {}).get("kind") == "variation" or category.startswith("variation")
    if is_variation:
        panels = select_variation_boards(label, candidate)
    else:
        panels = select_fact_boards(label, candidate)
    gold_moves = (label or {}).get("moves")
    cand_moves = (candidate or {}).get("moves")
    asked = None if is_variation else verifier_on_gold(fen, label)
    item = {"id": item_id, "category": category, "finding": FINDINGS.get(category, category), "case": case, "label_id": (label or {}).get("label_id"), "fen": fen, "truth": _truth(label),
            "is_false": _is_false(label), "caught": category in (CAUGHT_FACT, CAUGHT_VARIATION), "is_variation": is_variation,
            "gold": _gold_view(label, fen), "candidate": _candidate_view(candidate), "verifier_on_gold": asked, "verifier_disagrees": verifier_disagrees(label, asked),
            "context": source_context(text, span["span_start"], span["span_end"]) if span else {"span": row.get("text", "")},
            "got_statuses": row.get("got_statuses"), "overlapping": _overlapping_candidates(prediction["facts"], span) if span and not is_variation else [],
            "panels": [render_panel(fen, p, store) for p in panels]}
    if candidate and candidate.get("board_moves") is not None:
        item["candidate_board_san"] = san_line(fen, candidate["board_moves"]) or "root position"
    if is_variation:
        item["gold_line"] = line_tokens(fen, gold_moves, (label or {}).get("failure_ply"), cand_moves)
        item["extractor_line"] = line_tokens(fen, cand_moves, (candidate or {}).get("verifier_first_false_ply"), gold_moves)
    return item


def caught_false_claims(predictions, labels):
    """False claims the system caught, which details does not list because they are not failures.
    Same rules as the scorer: fact on the right board with verdict FALSE; variation by its exact moves."""
    rows = {case: p["facts"] for case, p in predictions.items()}
    owner = sc.assign_candidates(rows, labels)
    facts = [(label, rows[case][i]) for (case, i), label in owner.items()
             if label["kind"] == "fact" and label.get("polarity", "positive") == "positive" and label["chess_truth"] == "FALSE"
             and rows[case][i]["board_moves"] == label["branch"] and rows[case][i]["verifier_status"] == "FALSE"]
    variations, used = [], set()
    for label in sorted(labels, key=sc._content_key):
        if label["kind"] != "variation" or label["extraction_class"] != "EXTRACTED" or label["case"] not in predictions:
            continue
        for i, v in enumerate(predictions[label["case"]]["variations"]):
            if v["kind"] not in sc.STRUCTURED and v["status"] == "EXTRACTED" and v["moves"] == label["moves"] and (label["case"], i) not in used:
                used.add((label["case"], i))
                if _line_fails(label) and v["verifier_status"] == "FALSE":
                    variations.append((label, v))
                break
    return facts, variations


def stable_item_id(category, row, seen):
    """The same finding keeps its id across rebuilds, so a saved review still applies. A row with no
    label is named by its content; seen numbers any identical rows, which are interchangeable."""
    if row.get("label_id"):
        return f"{category}/{row['label_id']}"
    content = json.dumps({k: row.get(k) for k in ("case", "predicate", "args", "moves", "text")}, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    seen[(category, digest)] += 1
    n = seen[(category, digest)]
    return f"{category}/{row['case']}/{digest}" + (f"-{n}" if n > 1 else "")


def build_items(scores, predictions, labels, texts):
    """(items, stored SVGs): one item per details row, then the caught false claims."""
    by_id = {r["label_id"]: r for r in labels}
    store, items = {}, []
    unlabelled_used = set()
    seen = collections.Counter()
    for category in sorted(scores["details"]):
        for row in scores["details"][category]:
            case = row["case"]
            prediction = predictions[case]
            label = by_id.get(row.get("label_id"))
            candidate = None
            if label is not None and (label["kind"] == "variation"):
                candidate = _find_variation(prediction["variations"], label)
            elif label is not None and row.get("got"):
                candidate = _find_fact(prediction["facts"], row["got"], label)
            elif label is None and "moves" in row:
                candidate = next((v for i, v in enumerate(prediction["variations"]) if (case, "v", i) not in unlabelled_used and v["moves"] == row["moves"] and v["kind"] not in sc.STRUCTURED and v["status"] == "EXTRACTED"), None)
                if candidate is not None:
                    unlabelled_used.add((case, "v", prediction["variations"].index(candidate)))
            elif label is None:
                candidate = next((c for i, c in enumerate(prediction["facts"]) if (case, "f", i) not in unlabelled_used and c["status"] == "EXTRACTED" and c["predicate"] == row.get("predicate") and c["args"] == row.get("args") and c["source_text"] == row.get("text")), None)
                if candidate is not None:
                    unlabelled_used.add((case, "f", prediction["facts"].index(candidate)))
            items.append(_make_item(stable_item_id(category, row, seen), category, case, label, candidate, row, prediction, texts[case], store))
    facts, variations = caught_false_claims(predictions, labels)
    for category, pairs in ((CAUGHT_FACT, facts), (CAUGHT_VARIATION, variations)):
        for label, candidate in sorted(pairs, key=lambda p: p[0]["label_id"]):
            items.append(_make_item(f"{category}/{label['label_id']}", category, label["case"], label, candidate, {}, predictions[label["case"]], texts[label["case"]], store))
    return items, list(store)


def error_analysis_sample(items, seed=SAMPLE_SEED):
    """Fixed sample for manual error analysis: {category: [item ids]}. None takes the whole category."""
    sample = {}
    for category, size in ERROR_ANALYSIS:
        ids = sorted(i["id"] for i in items if i["category"] == category)
        sample[category] = ids if size is None or size >= len(ids) else sorted(random.Random(f"{seed}:{category}").sample(ids, size))
    return sample


# ── the page ─────────────────────────────────────────────────────────────────
# Review logic, with no page access so tests can run it alone. Reviews live only in this browser
# and in files the reviewer exports: nothing here can reach an experiment file.
REVIEW_JS = r"""
const REVIEW_SCHEMA = "prophylax.queen_heldout.review", REVIEW_VERSION = 1, DECISIONS = ["agree", "disagree", "unsure"];
function decisionOf(reviews, id) { return (Object.hasOwn(reviews, id) && reviews[id].decision) || null; }
function reviewCounts(ids, reviews) {
  const c = {total: ids.length, reviewed: 0, agree: 0, disagree: 0, unsure: 0, unreviewed: 0};
  for (const id of ids) { const d = decisionOf(reviews, id); if (d) { c[d]++; c.reviewed++; } else c.unreviewed++; }
  return c;
}
function matchesReviewStatus(decision, status) { return !status || (status === "unreviewed" ? !decision : decision === status); }
function nextUnreviewed(ids, at, reviews) {
  for (let step = 1; step < ids.length; step++) { const i = (at + step) % ids.length; if (!decisionOf(reviews, ids[i])) return i; }
  return -1;
}
function withEntry(reviews, id, entry) {
  const next = {...reviews};
  if (entry.decision || entry.note) next[id] = entry; else delete next[id];
  return next;
}
function applyDecision(reviews, id, decision) {  // the same decision again takes it back
  if (!DECISIONS.includes(decision)) throw new Error("unknown decision: " + decision);
  return withEntry(reviews, id, {decision: decisionOf(reviews, id) === decision ? null : decision, note: Object.hasOwn(reviews, id) ? reviews[id].note : ""});
}
function applyNote(reviews, id, note) { return withEntry(reviews, id, {decision: decisionOf(reviews, id), note}); }
function buildReviewExport(items, reviews, experiment, now) {
  return {schema: REVIEW_SCHEMA, version: REVIEW_VERSION, generated_utc: now, experiment,
          reviews: items.filter(i => decisionOf(reviews, i.id)).map(i => ({item_id: i.id, case: i.case, label_id: i.label_id ?? null, category: i.category,
                                                                         decision: reviews[i.id].decision, note: reviews[i.id].note || null}))};
}
function notesWithoutDecision(reviews) { return Object.keys(reviews).filter(id => !reviews[id].decision && reviews[id].note).length; }
function parseReviewImport(text, items, experiment) {  // every problem rejects the whole file
  let data;
  try { data = JSON.parse(text); } catch (e) { throw new Error("the file is not valid JSON"); }
  if (data === null || typeof data !== "object" || Array.isArray(data)) throw new Error("the file is not a review export");
  if (data.schema !== REVIEW_SCHEMA) throw new Error("the file is not a review export");
  if (data.version !== REVIEW_VERSION) throw new Error(`unsupported version ${data.version}`);
  if (typeof data.generated_utc !== "string") throw new Error("generated_utc is missing");
  if (data.experiment === null || typeof data.experiment !== "object") throw new Error("experiment is missing");
  for (const [key, value] of Object.entries(experiment)) {
    if (value && data.experiment[key] && value !== data.experiment[key]) throw new Error(`this review belongs to a different experiment state: ${key} differs`);
  }
  if (!Array.isArray(data.reviews)) throw new Error("reviews is missing");
  const known = new Map(items.map(i => [i.id, i])), reviews = {};
  data.reviews.forEach((r, n) => {
    const where = `review ${n + 1}`;
    if (r === null || typeof r !== "object") throw new Error(`${where} is not an object`);
    const item = known.get(r.item_id);
    if (!item) throw new Error(`${where} names an unknown item`);
    if (Object.hasOwn(reviews, r.item_id)) throw new Error(`${where} repeats ${r.item_id}`);
    if (r.case !== item.case || (r.label_id ?? null) !== (item.label_id ?? null) || r.category !== item.category) throw new Error(`${where} does not match ${r.item_id}`);
    if (!DECISIONS.includes(r.decision)) throw new Error(`${where} has an invalid decision`);
    if (r.note !== undefined && r.note !== null && typeof r.note !== "string") throw new Error(`${where} has an invalid note`);
    reviews[r.item_id] = {decision: r.decision, note: r.note || ""};
  });
  return reviews;
}
function cleanStoredReviews(value, items) {  // saved browser data is checked too; anything odd is dropped
  const known = new Set(items.map(i => i.id)), reviews = {};
  if (value === null || typeof value !== "object" || Array.isArray(value)) return reviews;
  for (const [id, r] of Object.entries(value)) {
    if (known.has(id) && r && typeof r === "object" && (r.decision === null || DECISIONS.includes(r.decision)) && typeof r.note === "string" && (r.decision || r.note)) reviews[id] = {decision: r.decision, note: r.note};
  }
  return reviews;
}
"""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>QUEEN held-out audit</title>
<style>
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1c1c1a;--muted:#62625c;--line:#dcdcd6;--mark:#fde68a;--gold:#2563eb;--ext:#ea580c;--bad:#dc2626;--ok:#15803d;--unsure:#a16207;--on:#fff;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#171715;--panel:#21211e;--ink:#ececE6;--muted:#a3a39b;--line:#3a3a35;--mark:#854d0e;--gold:#60a5fa;--ext:#fb923c;--bad:#f87171;--ok:#4ade80;--unsure:#facc15;--on:#171715;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--panel);border-bottom:1px solid var(--line);padding:10px 16px}
h1{font-size:16px;margin:0 8px 0 0;display:inline}.row{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin-top:8px}
button,select{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px 10px;min-height:32px}
button{cursor:pointer}button[aria-pressed=true]{background:var(--gold);border-color:var(--gold);color:#fff}
label{display:inline-flex;gap:5px;align-items:center;color:var(--muted);font-size:13px}label select{max-width:240px}
:focus-visible{outline:2px solid var(--gold);outline-offset:1px}
#status{font-weight:600}#sampleinfo{color:var(--muted);font-size:13px;width:100%}#sampleinfo:empty{display:none}
main{padding:16px;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;max-width:1500px;margin:auto}
@media (max-width:980px){main{grid-template-columns:minmax(0,1fr)}}
section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:14px;min-width:0}
h2{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
.title{font-size:18px;font-weight:600;overflow-wrap:anywhere}.badge{display:inline-block;font-size:12px;font-weight:600;border:1px solid var(--line);border-radius:999px;padding:1px 9px;margin:2px 4px 2px 0}
.badge.bad{color:var(--bad);border-color:var(--bad)}.badge.ok{color:var(--ok);border-color:var(--ok)}
.context{white-space:pre-wrap;overflow-wrap:anywhere}.context .near{color:var(--muted);font-size:13px}.context mark{background:var(--mark);color:inherit;border-radius:3px;padding:0 2px}
table{border-collapse:collapse;width:100%}td,th{text-align:left;vertical-align:top;padding:3px 8px 3px 0;overflow-wrap:anywhere}th{color:var(--muted);font-weight:500;width:9.5em;font-size:13px}
.panels{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:14px}
.board svg{width:100%;height:auto;display:block;max-width:560px}.board h3{font-size:14px;margin:0 0 6px}.board.gold h3{color:var(--gold)}.board.extractor h3{color:var(--ext)}
.note{font-size:13px;color:var(--muted);margin:6px 0 0}.line{font-size:14px;margin:4px 0 0;overflow-wrap:anywhere}
.lines{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:12px}.tok{display:inline-block;margin-right:6px}
.tok.fail{color:var(--bad);font-weight:700;text-decoration:underline}.tok.diff{color:var(--ext);font-weight:600}.tok.unplayed{color:var(--muted);font-style:italic}
.legend span{white-space:nowrap;margin-right:12px;font-size:13px;color:var(--muted)}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px}
.empty{padding:40px;text-align:center;color:var(--muted);grid-column:1/-1}ul{margin:0;padding-left:18px}li{overflow-wrap:anywhere}
@media (max-width:980px){header{position:static}}
#counts{font-size:14px}#counts b{font-weight:600}#counts span{margin-right:12px;white-space:nowrap}
#review-msg{font-size:13px;color:var(--muted);width:100%}#review-msg:empty{display:none}#review-msg.error{color:var(--bad);font-weight:600}
.review{border-width:2px}.review .finding{font-size:15px;margin:0 0 10px}.review .state{font-weight:600;margin-left:auto;font-size:14px}.decide{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.decide button{min-height:44px;padding:8px 14px;font-weight:600;border-width:2px;background:var(--panel)}
.decide .agree{color:var(--ok);border-color:var(--ok)}.decide .disagree{color:var(--bad);border-color:var(--bad)}.decide .unsure{color:var(--unsure);border-color:var(--unsure)}
.decide .agree[aria-pressed=true]{background:var(--ok);border-color:var(--ok);color:var(--on)}.decide .disagree[aria-pressed=true]{background:var(--bad);border-color:var(--bad);color:var(--on)}.decide .unsure[aria-pressed=true]{background:var(--unsure);border-color:var(--unsure);color:var(--on)}
kbd{font:600 11px ui-monospace,monospace;border:1px solid currentColor;border-radius:3px;padding:0 4px;margin-left:6px;opacity:.75}
.review textarea{display:block;width:100%;min-height:58px;margin-top:10px;font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;resize:vertical}
.review .notelabel{display:block;margin-top:10px}
</style></head><body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">__DEFS__</svg>
<header>
<h1>QUEEN held-out audit</h1><span id="status" aria-live="polite"></span>
<div class="row">
<button id="prev" title="Left arrow">&larr; Previous</button><button id="next" title="Right arrow">Next &rarr;</button><button id="random">Random</button>
<button id="mode" aria-pressed="false">Error analysis sample</button>
<label>Failure <select id="f-category"></select></label><label>Case <select id="f-case"></select></label><label>Gold truth <select id="f-truth"></select></label>
<label><input type="checkbox" id="f-false"> False claims only</label><label><input type="checkbox" id="f-caught" checked> Show successful catches</label>
<span class="legend"><span><i class="dot" style="background:#2563eb"></i>gold</span><span><i class="dot" style="background:#ea580c"></i>extractor</span><span><i class="dot" style="background:#dc2626"></i>failing move</span></span>
</div><div class="row" id="sampleinfo"></div>
<div class="row"><span id="counts" aria-live="polite"></span></div>
<div class="row">
<button id="next-unreviewed">Next unreviewed</button>
<label>Review status <select id="f-review"><option value="">All</option><option value="unreviewed">Unreviewed</option><option value="agree">Agree</option><option value="disagree">Disagree</option><option value="unsure">Unsure</option></select></label>
<label><input type="checkbox" id="auto-advance" checked> Auto-advance after review</label>
<button id="export">Export review JSON</button><button id="import">Import review JSON</button><input type="file" id="import-file" accept=".json,application/json" hidden><button id="clear">Clear review data</button>
</div><div class="row" id="review-msg" role="status"></div>
</header>
<main id="main"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
__REVIEW_JS__
const {items, svgs, sample, seed, experiment} = JSON.parse(document.getElementById("data").textContent);
const $ = id => document.getElementById(id);
const byId = new Map(items.map(i => [i.id, i]));
const sampleIds = new Set(Object.values(sample).flat());
let inSample = false, shown = [], at = 0;
const STORE_KEY = "queen-heldout-review:v1:" + (experiment.scores_sha256 || "unknown"), AUTO_KEY = "queen-heldout-review:auto-advance";
let reviews = {}, storageWorks = true;

function say(text, isError) { const m = $("review-msg"); m.textContent = text; m.className = "row" + (isError ? " error" : ""); }
function loadReviews() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) reviews = cleanStoredReviews(JSON.parse(raw), items);
    const auto = localStorage.getItem(AUTO_KEY);
    if (auto !== null) $("auto-advance").checked = auto === "true";
  } catch (e) {
    if (e instanceof SyntaxError) say("Saved review data was unreadable and was ignored.", true);
    else { storageWorks = false; say("This browser is not saving reviews. Export before you close this page.", true); }
  }
}
function saveReviews() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(reviews)); }
  catch (e) { storageWorks = false; say("This browser could not save that review. Export before you close this page.", true); }
}

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
function fmt(v) { return v === null || v === undefined ? "none" : typeof v === "object" ? JSON.stringify(v) : String(v); }
function table(rows) {
  const t = el("table");
  for (const [k, v] of rows) { if (v === undefined) continue; const r = t.insertRow(); r.appendChild(el("th", "", k)); r.appendChild(el("td", "", fmt(v))); }
  return t;
}
function section(title, ...children) { const s = el("section"); s.appendChild(el("h2", "", title)); children.forEach(c => s.appendChild(c)); return s; }
function options(select, values, labels) {
  select.appendChild(new Option("All", ""));
  for (const v of values) select.appendChild(new Option(labels ? labels(v) : v, v));
}
function counted(key) { const c = {}; items.forEach(i => c[i[key]] = (c[i[key]] || 0) + 1); return v => `${v} (${c[v]})`; }
const uniq = key => [...new Set(items.map(i => i[key]))].sort();
options($("f-category"), uniq("category"), counted("category"));
options($("f-case"), uniq("case"), counted("case"));
options($("f-truth"), uniq("truth"), counted("truth"));

function applyFilters(keep) {
  const cat = $("f-category").value, cs = $("f-case").value, tr = $("f-truth").value, falseOnly = $("f-false").checked, caught = $("f-caught").checked, status = $("f-review").value;
  const current = keep ?? shown[at]?.id;
  shown = items.filter(i => (inSample ? sampleIds.has(i.id) : (caught || !i.caught)) && (!cat || i.category === cat) && (!cs || i.case === cs) && (!tr || i.truth === tr) && (!falseOnly || i.is_false) && matchesReviewStatus(decisionOf(reviews, i.id), status));
  at = Math.max(0, shown.findIndex(i => i.id === current));
  draw();
}
function move(step) { if (shown.length) { at = (at + step + shown.length) % shown.length; say(""); draw(); window.scrollTo(0, 0); } }
function goNextUnreviewed() {
  const i = nextUnreviewed(shown.map(s => s.id), at, reviews);
  if (i < 0) { say("No other unreviewed items in this view."); return false; }
  at = i; say(""); draw(); window.scrollTo(0, 0); return true;
}
function decide(decision) {
  if (!shown.length) return;
  const id = shown[at].id;
  reviews = applyDecision(reviews, id, decision);
  saveReviews();
  if (!decisionOf(reviews, id) || !$("auto-advance").checked) { draw(); return; }  // the item stays until you move on
  const ids = shown.map(s => s.id), next = nextUnreviewed(ids, at, reviews);
  if (next < 0) { say("That was the last unreviewed item in this view."); draw(); return; }
  applyFilters(ids[next]); window.scrollTo(0, 0);
}
function pool() { return inSample ? items.filter(i => sampleIds.has(i.id)) : items; }
function drawCounts() {
  const c = reviewCounts(pool().map(i => i.id), reviews), box = $("counts"); box.replaceChildren();
  for (const [name, n] of [[inSample ? "Sample reviewed" : "Reviewed", `${c.reviewed} / ${c.total}`], ["Agree", c.agree], ["Disagree", c.disagree], ["Unsure", c.unsure], ["Unreviewed", c.unreviewed]]) {
    const s = el("span"); s.append(name + " "); s.appendChild(el("b", "", n)); box.appendChild(s);
  }
}
function reviewBlock(i) {
  const s = el("section", "review"); s.appendChild(el("h2", "", "Your review: does this finding look correct?"));
  s.appendChild(el("p", "finding", i.finding));
  const row = el("div", "decide"), current = decisionOf(reviews, i.id);
  for (const [decision, label, key] of [["agree", "✓ Agree with finding", "A"], ["disagree", "✗ Disagree", "D"], ["unsure", "? Unsure", "U"]]) {
    const b = el("button", decision, label); b.appendChild(el("kbd", "", key));
    b.setAttribute("aria-pressed", current === decision); b.onclick = () => decide(decision); row.appendChild(b);
  }
  const state = el("span", "state", current ? "Reviewed: " + current[0].toUpperCase() + current.slice(1) : "Not reviewed"); state.id = "review-state"; row.appendChild(state);
  s.appendChild(row);
  const label = el("label", "notelabel", "Note (optional)"); const note = el("textarea"); note.id = "review-note"; note.value = Object.hasOwn(reviews, i.id) ? reviews[i.id].note : "";
  note.oninput = () => { reviews = applyNote(reviews, i.id, note.value); saveReviews(); };
  label.appendChild(note); s.appendChild(label);
  return s;
}

function contextBlock(c) {
  const p = el("div", "context");
  if (c.nearby_before) p.appendChild(el("span", "near", c.nearby_before));
  if (c.sentence_before) p.appendChild(document.createTextNode(c.sentence_before));
  p.appendChild(el("mark", "", c.span));
  if (c.sentence_after) p.appendChild(document.createTextNode(c.sentence_after));
  if (c.nearby_after) p.appendChild(el("span", "near", c.nearby_after));
  return p;
}
function lineBlock(title, tokens) {
  const d = el("div"); d.appendChild(el("strong", "", title));
  const p = el("div", "line");
  if (!tokens.length) p.textContent = "no line";
  tokens.forEach(t => p.appendChild(el("span", "tok " + t.c, t.t)));
  d.appendChild(p); return d;
}
function boardBlock(p) {
  const d = el("div", "board " + p.role); d.appendChild(el("h3", "", p.title));
  const holder = el("div"); holder.innerHTML = svgs[p.svg]; d.appendChild(holder); // SVG from python-chess, holding no model text
  d.appendChild(el("p", "line", p.line));
  p.notes.forEach(n => d.appendChild(el("p", "note", n)));
  return d;
}
function draw() {
  const main = $("main"); main.replaceChildren();
  $("status").textContent = shown.length ? `Item ${at + 1} of ${shown.length}` : "No items match";
  $("sampleinfo").textContent = inSample ? `Error analysis sample, seed ${seed}: ${sampleIds.size} selected (` + Object.entries(sample).map(([k, v]) => `${v.length} ${k}`).join(", ") + ")" : "";
  drawCounts();
  if (!shown.length) { main.appendChild(el("div", "empty", "Nothing matches these filters.")); return; }
  const i = shown[at];
  try { history.replaceState(null, "", "#" + encodeURIComponent(i.id)); } catch (e) {}
  const left = el("div"), right = el("div");
  const head = el("section");
  head.appendChild(el("div", "title", i.category));
  head.appendChild(el("span", "badge", i.case));
  head.appendChild(el("span", "badge", i.label_id || "no label"));
  head.appendChild(el("span", "badge" + (i.truth === "FALSE" ? " bad" : i.truth === "TRUE" ? " ok" : ""), "gold truth: " + i.truth));
  if (i.caught) head.appendChild(el("span", "badge ok", "caught"));
  if (i.verifier_disagrees) head.appendChild(el("span", "badge bad", "bare predicate differs from gold: check for a change claim"));
  left.appendChild(head);
  left.appendChild(reviewBlock(i));
  left.appendChild(section("QUEEN's text, with the " + (i.gold ? "gold" : "candidate") + " span marked", contextBlock(i.context)));
  if (i.gold) {
    const g = i.gold;
    left.appendChild(section("Gold label", table(i.is_variation
      ? [["class", g.extraction_class], ["legal", g.legal], ["annotations hold", g.annotations_hold], ["failure ply", g.failure_ply === null ? "none" : `${g.failure_ply + 1} (index ${g.failure_ply})`], ["notes", g.notes]]
      : [["kind", g.kind], ["class", g.extraction_class], ["predicate", g.predicate], ["args", g.args], ["polarity", g.polarity], ["chess truth", g.chess_truth], ["false concrete", g.false_concrete], ["branch (SAN)", g.branch_san], ["branch (UCI)", g.branch], ["notes", g.notes]])));
  } else left.appendChild(section("Gold label", el("div", "", "No label covers this.")));
  if (i.candidate) {
    const c = i.candidate;
    left.appendChild(section("Frozen extractor candidate", table(i.is_variation
      ? [["status", c.status], ["verifier", c.verifier_status], ["first false ply", c.verifier_first_false_ply === null || c.verifier_first_false_ply === undefined ? "none" : `${c.verifier_first_false_ply + 1} (index ${c.verifier_first_false_ply})`], ["reason", c.reason]]
      : [["status", c.status], ["predicate", c.predicate], ["args", c.args], ["board (SAN)", i.candidate_board_san], ["board moves", c.board_moves], ["change from", c.change_from || undefined], ["guards", c.guards && c.guards.length ? c.guards : undefined], ["verifier", c.verifier_status], ["verifier reason", c.verifier_reason], ["text", c.source_text], ["reason", c.reason]])));
  } else left.appendChild(section("Frozen extractor candidate", el("div", "", "None.")));
  if (i.verifier_on_gold) left.appendChild(section("Bare predicate on the gold board (ignores any change the text claims)", table([["result", i.verifier_on_gold.status], ["reason", i.verifier_on_gold.reason]])));
  if (i.got_statuses) left.appendChild(section("Extractor statuses on this span", el("div", "", i.got_statuses.join(", ") || "none")));
  if (i.overlapping.length) {
    const u = el("ul");
    i.overlapping.forEach(o => u.appendChild(el("li", "", [o.status, o.predicate, o.args && JSON.stringify(o.args), o.verifier_status, o.reason, "«" + o.source_text + "»"].filter(Boolean).join(" · "))));
    left.appendChild(section("Everything the extractor produced on this span", u));
  }
  if (i.is_variation) { const d = el("div", "lines"); d.appendChild(lineBlock("Gold line", i.gold_line)); d.appendChild(lineBlock("Extracted line", i.extractor_line)); right.appendChild(section("Lines", d)); }
  const boards = el("div", "panels"); i.panels.forEach(p => boards.appendChild(boardBlock(p)));
  right.appendChild(section(i.panels.length > 1 ? "Boards differ" : "Board", boards));
  main.append(left, right);
}
$("prev").onclick = () => move(-1); $("next").onclick = () => move(1);
$("random").onclick = () => { if (shown.length) { at = Math.floor(Math.random() * shown.length); draw(); window.scrollTo(0, 0); } };
$("mode").onclick = () => { inSample = !inSample; $("mode").setAttribute("aria-pressed", inSample); applyFilters(); };
for (const id of ["f-category", "f-case", "f-truth", "f-false", "f-caught", "f-review"]) $(id).onchange = e => { applyFilters(); e.target.blur(); };
$("next-unreviewed").onclick = goNextUnreviewed;
$("auto-advance").onchange = e => { try { localStorage.setItem(AUTO_KEY, e.target.checked); } catch (err) {} };
$("export").onclick = () => {
  const data = buildReviewExport(items, reviews, experiment, new Date().toISOString());
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 1)], {type: "application/json"}));
  const a = document.createElement("a"); a.href = url; a.download = `queen_heldout_review_${data.generated_utc.replace(/[:.]/g, "-")}.json`; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  const loose = notesWithoutDecision(reviews);
  say(`Exported ${data.reviews.length} reviews.` + (loose ? ` ${loose} notes have no decision yet, so they were left out.` : ""));
};
$("import").onclick = () => $("import-file").click();
$("import-file").onchange = async e => {
  const file = e.target.files[0]; e.target.value = "";
  if (!file) return;
  try {
    const imported = parseReviewImport(await file.text(), items, experiment), existing = Object.keys(reviews).length;
    if (existing && !confirm(`Replace your ${existing} saved reviews with the ${Object.keys(imported).length} in this file?`)) return;
    reviews = imported; saveReviews(); applyFilters();
    if (storageWorks) say(`Imported ${Object.keys(imported).length} reviews.`);
  } catch (err) { say(`Import rejected, nothing changed: ${err.message}.`, true); }
};
$("clear").onclick = () => {
  const n = Object.keys(reviews).length;
  if (!n) { say("There is no review data to clear."); return; }
  if (!confirm(`Delete all ${n} saved reviews from this browser? Export first if you want to keep them.`)) return;
  reviews = {};
  try { localStorage.removeItem(STORE_KEY); } catch (e) {}
  applyFilters(); say("Review data cleared.");
};
document.addEventListener("keydown", e => {
  if (e.altKey || e.ctrlKey || e.metaKey || ["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName)) return;
  if (e.key === "ArrowLeft") move(-1);
  if (e.key === "ArrowRight") move(1);
  const decision = {a: "agree", d: "disagree", u: "unsure"}[e.key.toLowerCase()];
  if (decision) decide(decision);
});
loadReviews();
applyFilters(decodeURIComponent(location.hash.slice(1)) || undefined);
</script></body></html>
"""


def render_html(items, svgs, sample, seed=SAMPLE_SEED, experiment=None):
    data = json.dumps({"items": items, "svgs": svgs, "sample": sample, "seed": seed, "experiment": experiment or {}}, ensure_ascii=False, separators=(",", ":"))
    return PAGE.replace("__DEFS__", shared_defs()).replace("__REVIEW_JS__", REVIEW_JS).replace("__DATA__", data.replace("</", "<\\/"))


# ── command ──────────────────────────────────────────────────────────────────
def load(base=sc.HERE):
    with open(os.path.join(base, "scores.json"), encoding="utf-8") as f:
        scores = json.load(f)
    manifest_sha = sc.sha256_file(os.path.join(base, "predictions", "MANIFEST.json"))
    if scores["meta"]["manifest_sha256"] != manifest_sha or scores["meta"]["labels_sha256"] != sc.sha256_file(os.path.join(base, "labels.jsonl")):
        raise sc.ScoreError("scores.json does not name the current labels and predictions: re-score first")
    _, predictions = sc.load_predictions(os.path.join(base, "predictions"))
    labels = sc.load_jsonl(os.path.join(base, "labels.jsonl"))
    texts = {}
    for case in predictions:
        with open(os.path.join(base, "raw", case, "explanation.txt"), encoding="utf-8", newline="") as f:
            texts[case] = f.read()
    return scores, predictions, labels, texts


def experiment_state(scores, base=sc.HERE):
    """What a review was made against; it travels in the export and an import must agree with it."""
    return {"manifest_sha256": scores["meta"]["manifest_sha256"], "labels_sha256": scores["meta"]["labels_sha256"],
            "scores_sha256": sc.sha256_file(os.path.join(base, "scores.json"))}


def check_output_path(path, protected=PROTECTED):
    real = os.path.realpath(path)
    for folder in protected:
        if real == os.path.realpath(folder) or real.startswith(os.path.realpath(folder) + os.sep):
            raise ValueError(f"refusing to write inside {folder}: the viewer is read-only there")
    return real


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        out = check_output_path(args.out)
        loaded = load()
        items, svgs = build_items(*loaded)
    except (ValueError, sc.ScoreError) as e:
        print(f"REFUSED: {e}")
        return 2
    sample = error_analysis_sample(items)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_html(items, svgs, sample, experiment=experiment_state(loaded[0])))
    counts = collections.Counter(i["category"] for i in items)
    print(f"{len(items)} items, {len(svgs)} distinct boards, {sum(map(len, sample.values()))} in the error analysis sample; {sum(counts[c] for c in (CAUGHT_FACT, CAUGHT_VARIATION))} are caught false claims")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
