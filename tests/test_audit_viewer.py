"""The held-out audit viewer: SAN replay, board selection, span context, board-binding differences,
and that it reads the real frozen artifacts without changing them."""
import collections
import hashlib
import json
import os
import shutil
import subprocess

import chess
import pytest

from evals.queen_heldout import audit_viewer as av
from evals.queen_heldout import score as sc

START = chess.STARTING_FEN
BLACK_TO_MOVE = "4k3/8/8/8/8/3p4/4P3/4K3 b - - 3 30"


# ── SAN replay ───────────────────────────────────────────────────────────────
def test_replay_gives_san_and_the_resulting_board():
    r = av.replay(START, ["e2e4", "e7e5", "g1f3"])
    assert r["sans"] == ["e4", "e5", "Nf3"] and r["failed_at"] is None
    assert r["board"].piece_at(chess.F3).symbol() == "N" and r["board"].turn == chess.BLACK


def test_replay_stops_at_the_first_illegal_move_and_keeps_the_last_legal_board():
    r = av.replay(START, ["e2e4", "e7e4", "g1f3"])
    assert r["sans"] == ["e4"] and r["failed_at"] == 1
    assert r["board"].fen() == av.replay(START, ["e2e4"])["board"].fen()


def test_replay_of_no_moves_is_the_root():
    assert av.replay(START, [])["board"].fen() == START and av.replay(START, None)["sans"] == []


def test_san_line_numbers_moves_from_the_fen_including_black_to_move():
    assert av.san_line(START, ["e2e4", "e7e5", "g1f3"]) == "1. e4 e5 2. Nf3"
    assert av.san_line(BLACK_TO_MOVE, ["e8e7", "e1d1"]) == "30... Ke7 31. Kd1"


def test_tokens_mark_the_failing_ply_and_leave_unplayable_moves_as_uci():
    tokens = av.line_tokens(START, ["e2e4", "e7e4", "g1f3"])
    assert [t["t"] for t in tokens] == ["1. e4", "e7e4", "2. g1f3"]
    assert [t["c"] for t in tokens] == ["", "fail", "unplayed"]


def test_tokens_mark_where_a_second_line_differs():
    tokens = av.line_tokens(START, ["e2e4", "e7e5"], other=["e2e4", "c7c5"])
    assert [t["c"] for t in tokens] == ["", "diff"]


# ── source context ───────────────────────────────────────────────────────────
TEXT = "First sentence here. After 2... black king e8-e7 the pawn is passed. Last one here.\nA new line."


def test_context_finds_the_containing_sentence_and_keeps_every_character():
    span = "the pawn is passed"
    start = TEXT.index(span)
    c = av.source_context(TEXT, start, start + len(span))
    assert c["span"] == span
    assert c["sentence_before"] == " After 2... black king e8-e7 "  # "2..." is not the end of a sentence
    assert c["sentence_after"] == "."
    assert "".join(c[k] for k in ("nearby_before", "sentence_before", "span", "sentence_after", "nearby_after")) == TEXT


def test_context_is_bounded_by_a_newline_and_by_the_text_ends():
    start = TEXT.index("A new")
    c = av.source_context(TEXT, start, len(TEXT))
    assert c["sentence_before"] == "" and c["sentence_after"] == "" and c["nearby_after"] == ""
    assert av.source_context(TEXT, 0, 5)["nearby_before"] == ""


def test_nearby_context_is_limited():
    assert len(av.source_context("x" * 900 + ". Then y. " + "z" * 900, 902, 908, pad=50)["nearby_before"]) == 50


# ── board selection and board binding ────────────────────────────────────────
GOLD = {"kind": "fact", "predicate": "attacks", "args": {"source": "e4", "target": "f5"}, "branch": ["e2e4"]}


def candidate(moves, **kw):
    return {"predicate": "attacks", "args": {"source": "e4", "target": "f5"}, "board_moves": moves, **kw}


def test_a_candidate_on_the_gold_board_is_drawn_on_it():
    panels = av.select_fact_boards(GOLD, candidate(["e2e4"]))
    assert [p["role"] for p in panels] == ["gold"]
    assert [m[2] for m in panels[0]["marks"]] == [av.GOLD, av.EXTRACTOR]


def test_a_board_binding_difference_shows_a_second_labelled_board():
    panels = av.select_fact_boards(GOLD, candidate(["e2e4", "e7e5"]))
    assert [p["role"] for p in panels] == ["gold", "extractor"]
    assert panels[1]["title"] == "Extractor board" and panels[0]["moves"] == ["e2e4"] and panels[1]["moves"] == ["e2e4", "e7e5"]
    assert panels[0]["marks"][0][2] == av.GOLD and panels[1]["marks"][0][2] == av.EXTRACTOR
    assert [p["role"] for p in av.select_fact_boards(GOLD, candidate([]))] == ["gold", "extractor"]  # the root is a board too


def test_a_candidate_with_no_board_adds_no_second_board_and_says_so():
    panels = av.select_fact_boards(GOLD, candidate(None))
    assert [p["role"] for p in panels] == ["gold"] and "no board" in panels[0]["notes"][0]
    assert len(panels[0]["marks"]) == 1  # only the gold claim is drawn
    rendered = av.render_panel(START, panels[0], {})
    assert any("no board" in n for n in rendered["notes"])


@pytest.mark.parametrize("truth,polarity,status,expected", [
    ("TRUE", "positive", "VERIFIED", False), ("TRUE", "positive", "FALSE", True), ("FALSE", "positive", "FALSE", False), ("FALSE", "positive", "VERIFIED", True),
    ("TRUE", "negative", "FALSE", False), ("TRUE", "negative", "VERIFIED", True), ("FALSE", "negative", "VERIFIED", False), ("TRUE", "positive", "UNKNOWN", False),
])
def test_verifier_disagreement_respects_polarity(truth, polarity, status, expected):
    assert av.verifier_disagrees({"chess_truth": truth, "polarity": polarity}, {"status": status}) is expected


def test_a_missed_fact_shows_the_gold_board_alone():
    panels = av.select_fact_boards(GOLD, None)
    assert [p["role"] for p in panels] == ["gold"] and len(panels[0]["marks"]) == 1


def test_an_undetermined_gold_claim_does_not_invent_a_board():
    undetermined = {**GOLD, "branch": None}
    assert [p["role"] for p in av.select_fact_boards(undetermined, None)] == ["root"]
    assert av.select_fact_boards(undetermined, None)[0]["marks"] == []
    assert [p["role"] for p in av.select_fact_boards(undetermined, candidate(["e2e4"]))] == ["root", "extractor"]


def test_a_non_fact_uses_the_extractor_board_or_the_root():
    non_fact = {"kind": "non_fact"}
    assert [p["role"] for p in av.select_fact_boards(non_fact, candidate(["e2e4"]))] == ["extractor"]
    assert [p["role"] for p in av.select_fact_boards(non_fact, None)] == ["root"]
    assert [p["role"] for p in av.select_fact_boards(None, candidate(["e2e4"]))] == ["extractor"]


def test_a_variation_failure_shows_the_board_before_it_with_the_move_highlighted():
    gold = {"moves": ["e2e4", "e7e5", "d1h8"], "failure_ply": 2}
    panels = av.select_variation_boards(gold, None)
    assert panels[0]["moves"] == ["e2e4", "e7e5"] and panels[0]["arrows"] == [("d1h8", av.FAILING)]
    assert "ply 3" in panels[0]["title"]


def test_a_variation_with_two_different_failures_shows_both():
    gold = {"moves": ["e2e4", "e7e5", "d1h8"], "failure_ply": 2}
    cand = {"moves": ["e2e4", "e7e4"], "verifier_first_false_ply": 1}
    assert [p["title"].split(":")[0] for p in av.select_variation_boards(gold, cand)] == ["Gold", "Extractor"]
    same = {"moves": gold["moves"], "verifier_first_false_ply": 2}
    assert len(av.select_variation_boards(gold, same)) == 1


def test_lines_that_differ_without_failing_show_the_board_before_the_difference():
    panels = av.select_variation_boards({"moves": ["e2e4", "e7e5"], "failure_ply": None}, {"moves": ["e2e4", "c7c5"], "verifier_first_false_ply": None})
    assert panels[0]["moves"] == ["e2e4"] and panels[0]["arrows"] == [("e7e5", av.GOLD), ("c7c5", av.EXTRACTOR)]
    shorter = av.select_variation_boards({"moves": ["e2e4", "e7e5"], "failure_ply": None}, {"moves": ["e2e4"], "verifier_first_false_ply": None})
    assert shorter[0]["moves"] == ["e2e4"] and shorter[0]["arrows"] == [("e7e5", av.GOLD)]


def test_a_line_with_nothing_to_compare_shows_its_end():
    panels = av.select_variation_boards(None, {"moves": ["e2e4"], "verifier_first_false_ply": None})
    assert panels[0]["moves"] == ["e2e4"] and panels[0]["arrows"] == []


# ── why a board_binding_wrong item is flagged ────────────────────────────────
def why(branch, moves, truth="FALSE", verdict="FALSE"):
    return av.board_binding_summary(START, {"branch": branch, "chess_truth": truth}, {"board_moves": moves, "verifier_status": verdict})


def test_different_branches_name_both_positions_and_the_timing():
    w = why(["e2e4", "e7e5"], ["e2e4", "e7e5", "g1f3"])
    assert w["gold_position"] == "after 1. e4 e5" and w["extractor_position"] == "after 1. e4 e5 2. Nf3"
    assert w["comparison"] == "DIFFERENT BOARD" and w["warning"] is None
    assert "one move too late" in w["explanation"] and "the claim is false" in w["explanation"]
    assert w["consequence"] == "The extractor parsed the claim, but checked it at a different point in the move sequence."


@pytest.mark.parametrize("branch,moves,expected", [
    (["e2e4"], ["e2e4", "e7e5", "g1f3"], "2 moves too late"),
    (["e2e4", "e7e5"], ["e2e4"], "one move too early"),
    (["e2e4", "e7e5", "g1f3"], [], "3 moves too early"),
    (["e2e4"], ["d2d4"], "different sequence of moves"),
])
def test_the_timing_is_stated_in_plain_words(branch, moves, expected):
    assert expected in why(branch, moves)["explanation"]


def test_a_null_extractor_branch_is_unresolved_and_never_a_position():
    w = why(["e2e4"], None, verdict="UNKNOWN")
    assert w["extractor_position"] == "NONE / unresolved" and w["comparison"] == "EXTRACTOR HAS NO RESOLVED BOARD"
    assert w["extractor_has_board"] is False and w["extractor_line"] == []
    assert w["consequence"].startswith("The extractor parsed the claim, but did not resolve which position")
    assert w["explanation"] == "The claim itself was parsed correctly, but the extractor did not bind it to the position after 1. e4. On the correct gold board, the claim is false."


def test_the_root_is_a_real_position_and_not_the_same_as_no_board():
    assert why([], ["e2e4"])["gold_position"] == "root position"
    w = why(["e2e4"], [])
    assert w["extractor_position"] == "root position" and w["comparison"] == "DIFFERENT BOARD" and "one move too early" in w["explanation"]
    assert why([], None, verdict="UNKNOWN")["explanation"].startswith("The claim itself was parsed correctly, but the extractor did not bind it to the root position.")
    assert why([], [])["comparison"] == "SAME BOARD"


def test_positions_are_rendered_in_san_with_the_right_move_numbers():
    w = av.board_binding_summary(BLACK_TO_MOVE, {"branch": ["e8e7"], "chess_truth": "TRUE"}, {"board_moves": ["e8e7", "e1d1"], "verifier_status": "VERIFIED"})
    assert w["gold_position"] == "after 30... Ke7" and w["extractor_position"] == "after 30... Ke7 31. Kd1"
    assert [t["t"] for t in w["extractor_line"]] == ["30... Ke7", "31. Kd1"]
    assert [t["c"] for t in w["extractor_line"]] == ["", "diff"]  # the extra move is picked out


def test_identical_branches_warn_that_the_category_looks_wrong():
    w = why(["e2e4"], ["e2e4"])
    assert w["comparison"] == "SAME BOARD" and w["warning"] == "WARNING: This item is categorized as board_binding_wrong, but both branches are identical."
    assert w["consequence"] is None and w["verdict_note"] is None
    assert why(["e2e4"], ["d2d4"])["warning"] is None and why(["e2e4"], None)["warning"] is None


@pytest.mark.parametrize("moves,verdict", [(["e2e4", "e7e5"], "VERIFIED"), (["e2e4", "e7e5"], "FALSE"), (["e2e4", "e7e5"], "UNKNOWN"), (None, "UNKNOWN")])
def test_the_verifier_result_is_the_one_for_the_extractor_board_and_separate_from_gold(moves, verdict):
    w = why(["e2e4"], moves, truth="TRUE", verdict=verdict)
    assert w["verifier_result"] == verdict and w["gold_truth"] == "TRUE"


def test_a_matching_verdict_on_the_wrong_board_is_not_presented_as_a_catch():
    assert "does not count as a correct result" in why(["e2e4"], ["d2d4"], truth="FALSE", verdict="FALSE")["verdict_note"]
    assert "says nothing about the gold position" in why(["e2e4"], ["d2d4"], truth="FALSE", verdict="VERIFIED")["verdict_note"]
    assert why(["e2e4"], ["d2d4"], verdict="UNKNOWN")["verdict_note"] is None


def test_an_undetermined_gold_claim_does_not_claim_a_position():
    w = why(None, ["e2e4"], truth="UNDETERMINED", verdict="UNKNOWN")
    assert w["gold_position"].startswith("not a single position") and "root" not in w["gold_position"]
    assert w["comparison"] == "DIFFERENT BOARD" and "too late" not in w["explanation"]


# ── annotations ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("predicate,args,arrows,fills", [
    ("attacks", {"source": "e4", "target": "f5"}, [(chess.E4, chess.F5)], []),
    ("defends", {"source": "h8", "target": "h6"}, [(chess.H8, chess.H6)], []),
    ("piece_at", {"square": "d4", "color": "white"}, [], [chess.D4]),
    ("is_passed_pawn", {"square": "b7"}, [], [chess.B7]),
    ("pinned_against", {"pinner": "e3", "pinned": "d2", "target": "d1"}, [(chess.E3, chess.D1)], [chess.D2]),
    ("material_balance_is", {"points": -10}, [], []),
    ("piece_count_is", {"color": "white", "piece": "queen", "count": 0}, [], []),
    ("attacks", {"source": "nonsense", "target": "f5"}, [], []),
])
def test_claim_marks(predicate, args, arrows, fills):
    assert av.claim_marks(predicate, args, av.GOLD) == (arrows, fills)


@pytest.mark.parametrize("predicate", ["is_open_file", "is_semi_open_file", "is_file_clear", "has_passed_pawn"])
def test_file_predicates_highlight_the_whole_file(predicate):
    _, fills = av.claim_marks(predicate, {"file": "c", "color": "white"}, av.GOLD)
    assert len(fills) == 8 and all(chess.square_file(s) == 2 for s in fills)


def test_rays_highlight_their_endpoints_and_the_squares_between():
    arrows, fills = av.claim_marks("ray_clear", {"source": "a2", "target": "d5"}, av.GOLD)
    assert arrows == [(chess.A2, chess.D5)] and set(fills) == {chess.A2, chess.B3, chess.C4, chess.D5}


def test_a_check_highlights_the_king_in_check():
    board = chess.Board("4k3/8/8/8/8/8/8/4RK2 b - - 0 1")
    assert av.claim_marks("is_check", {}, av.GOLD, board) == ([], [chess.E8])


# ── sampling and safety ──────────────────────────────────────────────────────
def test_the_output_is_refused_inside_the_experiment():
    for bad in (os.path.join(sc.HERE, "labels.jsonl"), os.path.join(sc.HERE, "predictions", "x.html"), os.path.join(av.ROOT, "domain", "x.html")):
        with pytest.raises(ValueError):
            av.check_output_path(bad)
    assert av.check_output_path(av.DEFAULT_OUT) == os.path.realpath(av.DEFAULT_OUT)


def test_model_text_cannot_end_the_data_script():
    item = {"id": "a", "context": {"span": "</script><script>alert(1)</script>"}}
    html = av.render_html([item], [], {})
    assert "</script><script>alert(1)" not in html and json.loads(html.split('type="application/json">')[1].split("</script>")[0].replace("<\\/", "</"))["items"] == [item]


def test_the_page_loads_nothing_from_the_network():
    html = av.render_html([], [], {})
    assert "http://" not in html.replace("http://www.w3.org", "").replace("http://www.w3.org/1999/xlink", "") and "https://" not in html


# ── stable item ids ──────────────────────────────────────────────────────────
NO_LABEL = {"case": "h01", "predicate": "attacks", "args": {"source": "e4"}, "text": "attacks e4", "verifier": "FALSE"}


def test_a_labelled_item_is_named_by_its_category_and_label():
    assert av.stable_item_id("variation_missed", {"case": "h01", "label_id": "l0025"}, collections.Counter()) == "variation_missed/l0025"


def test_an_unlabelled_item_id_depends_on_its_content_not_its_position():
    alone = av.stable_item_id("extraction_with_no_label", NO_LABEL, collections.Counter())
    seen = collections.Counter()
    av.stable_item_id("extraction_with_no_label", {**NO_LABEL, "text": "another row before it"}, seen)
    assert av.stable_item_id("extraction_with_no_label", NO_LABEL, seen) == alone
    assert av.stable_item_id("extraction_with_no_label", {**NO_LABEL, "text": "changed"}, collections.Counter()) != alone


def test_identical_unlabelled_rows_get_distinct_ids():
    seen = collections.Counter()
    first, second = (av.stable_item_id("extraction_with_no_label", NO_LABEL, seen) for _ in range(2))
    assert second == first + "-2"


# ── review logic, run in Node ────────────────────────────────────────────────
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="review logic is JavaScript; needs node")
ITEMS = [{"id": "c/l1", "case": "h01", "label_id": "l1", "category": "c"}, {"id": "c/l2", "case": "h01", "label_id": "l2", "category": "c"},
         {"id": "d/h02/abc", "case": "h02", "label_id": None, "category": "d"}]
IDS = [i["id"] for i in ITEMS]
EXPERIMENT = {"manifest_sha256": "m", "labels_sha256": "l", "scores_sha256": "s"}


def js(expression):
    """The value of an expression evaluated after the page's review functions, or the error it threw."""
    program = f"{av.REVIEW_JS}\nconst ITEMS = {json.dumps(ITEMS)}, IDS = {json.dumps(IDS)}, EXPERIMENT = {json.dumps(EXPERIMENT)};\ntry {{ console.log(JSON.stringify({{value: {expression}}})); }} catch (e) {{ console.log(JSON.stringify({{error: e.message}})); }}"
    result = subprocess.run([NODE, "-e", program], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def value(expression):
    return js(expression)["value"]


def export(reviews):
    return value(f"buildReviewExport(ITEMS, {json.dumps(reviews)}, EXPERIMENT, '2026-11-04T00:00:00.000Z')")


def agreed(note=""):
    return {"c/l1": {"decision": "agree", "note": note}}


@needs_node
def test_the_export_has_the_agreed_schema():
    data = export({**agreed("looks right"), "d/h02/abc": {"decision": "unsure", "note": ""}, "c/l2": {"decision": None, "note": "undecided"}})
    assert (data["schema"], data["version"], data["generated_utc"]) == ("prophylax.queen_heldout.review", 1, "2026-11-04T00:00:00.000Z")
    assert data["experiment"] == EXPERIMENT
    assert data["reviews"] == [{"item_id": "c/l1", "case": "h01", "label_id": "l1", "category": "c", "decision": "agree", "note": "looks right"},
                               {"item_id": "d/h02/abc", "case": "h02", "label_id": None, "category": "d", "decision": "unsure", "note": None}]


@needs_node
def test_the_category_comes_from_the_item_and_cannot_be_chosen():
    reviews = value("applyDecision({}, 'c/l1', 'disagree')")
    assert reviews == {"c/l1": {"decision": "disagree", "note": ""}}
    assert export(reviews)["reviews"][0]["category"] == "c"


@needs_node
def test_an_export_imports_back_exactly():
    data = export({**agreed("a note"), "d/h02/abc": {"decision": "disagree", "note": ""}})
    assert value(f"parseReviewImport({json.dumps(json.dumps(data))}, ITEMS, EXPERIMENT)") == {**agreed("a note"), "d/h02/abc": {"decision": "disagree", "note": ""}}


def _broken(change):
    data = {"schema": "prophylax.queen_heldout.review", "version": 1, "generated_utc": "t", "experiment": dict(EXPERIMENT),
            "reviews": [{"item_id": "c/l1", "case": "h01", "label_id": "l1", "category": "c", "decision": "agree", "note": None}]}
    change(data)
    return json.dumps(data)


@needs_node
@pytest.mark.parametrize("text,expect", [
    ("{not json", "not valid JSON"),
    ("", "not valid JSON"),
    ("null", "not a review export"),
    ("[]", "not a review export"),
    ("{}", "not a review export"),
    (_broken(lambda d: d.update(schema="something else")), "not a review export"),
    (_broken(lambda d: d.update(version=2)), "unsupported version"),
    (_broken(lambda d: d.pop("generated_utc")), "generated_utc"),
    (_broken(lambda d: d.update(experiment=None)), "experiment is missing"),
    (_broken(lambda d: d["experiment"].update(scores_sha256="other")), "different experiment state"),
    (_broken(lambda d: d["experiment"].update(manifest_sha256="other")), "different experiment state"),
    (_broken(lambda d: d.update(reviews={})), "reviews is missing"),
    (_broken(lambda d: d["reviews"].append(None)), "not an object"),
    (_broken(lambda d: d["reviews"][0].update(item_id="c/nope")), "unknown item"),
    (_broken(lambda d: d["reviews"][0].update(item_id="__proto__")), "unknown item"),
    (_broken(lambda d: d["reviews"].append(dict(d["reviews"][0]))), "repeats"),
    (_broken(lambda d: d["reviews"][0].update(category="WRONG_BOARD")), "does not match"),
    (_broken(lambda d: d["reviews"][0].update(case="h99")), "does not match"),
    (_broken(lambda d: d["reviews"][0].update(label_id="l9")), "does not match"),
    (_broken(lambda d: d["reviews"][0].update(decision="maybe")), "invalid decision"),
    (_broken(lambda d: d["reviews"][0].update(decision=None)), "invalid decision"),
    (_broken(lambda d: d["reviews"][0].update(note=7)), "invalid note"),
])
def test_an_invalid_or_corrupt_import_is_rejected_whole(text, expect):
    assert expect in js(f"parseReviewImport({json.dumps(text)}, ITEMS, EXPERIMENT)")["error"]


@needs_node
def test_one_bad_row_rejects_the_good_rows_with_it():
    text = _broken(lambda d: d["reviews"].append({"item_id": "c/l2", "case": "h01", "label_id": "l2", "category": "c", "decision": "maybe"}))
    assert "error" in js(f"parseReviewImport({json.dumps(text)}, ITEMS, EXPERIMENT)")


@needs_node
def test_an_import_may_omit_a_hash_the_page_or_file_does_not_have():
    text = _broken(lambda d: d["experiment"].pop("scores_sha256"))
    assert value(f"parseReviewImport({json.dumps(text)}, ITEMS, EXPERIMENT)") == agreed()


@needs_node
def test_unknown_or_malformed_saved_browser_data_is_dropped():
    stored = {"c/l1": {"decision": "agree", "note": "kept"}, "c/nope": {"decision": "agree", "note": ""}, "c/l2": {"decision": "maybe", "note": ""}, "d/h02/abc": "junk"}
    assert value(f"cleanStoredReviews({json.dumps(stored)}, ITEMS)") == agreed("kept")
    assert value("cleanStoredReviews([], ITEMS)") == {} and value("cleanStoredReviews(null, ITEMS)") == {}


@needs_node
def test_a_decision_leaves_the_items_and_the_earlier_reviews_untouched():
    result = value("(() => { const before = JSON.stringify(ITEMS), start = {}; const after = applyNote(applyDecision(start, 'c/l1', 'agree'), 'c/l1', 'n'); buildReviewExport(ITEMS, after, EXPERIMENT, 't'); return {same: JSON.stringify(ITEMS) === before, start, after}; })()")
    assert result == {"same": True, "start": {}, "after": agreed("n")}


@needs_node
def test_repeating_a_decision_takes_it_back_but_keeps_the_note():
    assert value("applyDecision(applyDecision({}, 'c/l1', 'agree'), 'c/l1', 'agree')") == {}
    assert value("applyDecision({'c/l1': {decision: 'agree', note: 'n'}}, 'c/l1', 'agree')") == {"c/l1": {"decision": None, "note": "n"}}
    assert value("applyDecision({'c/l1': {decision: 'agree', note: 'n'}}, 'c/l1', 'unsure')") == {"c/l1": {"decision": "unsure", "note": "n"}}
    assert "unknown decision" in js("applyDecision({}, 'c/l1', 'WRONG_BOARD')")["error"]


@needs_node
def test_a_note_without_a_decision_is_kept_but_not_reviewed_or_exported():
    reviews = value("applyNote({}, 'c/l1', 'thinking')")
    assert reviews == {"c/l1": {"decision": None, "note": "thinking"}}
    assert value(f"reviewCounts(IDS, {json.dumps(reviews)})")["unreviewed"] == 3 and export(reviews)["reviews"] == []
    assert value(f"notesWithoutDecision({json.dumps(reviews)})") == 1
    assert value(f"applyNote({json.dumps(reviews)}, 'c/l1', '')") == {}


@needs_node
def test_counts_cover_only_the_ids_asked_about():
    reviews = json.dumps({"c/l1": {"decision": "agree", "note": ""}, "c/l2": {"decision": "disagree", "note": ""}, "d/h02/abc": {"decision": "unsure", "note": ""}})
    assert value(f"reviewCounts(IDS, {reviews})") == {"total": 3, "reviewed": 3, "agree": 1, "disagree": 1, "unsure": 1, "unreviewed": 0}
    assert value(f"reviewCounts(['c/l1', 'x/y'], {reviews})") == {"total": 2, "reviewed": 1, "agree": 1, "disagree": 0, "unsure": 0, "unreviewed": 1}


@needs_node
@pytest.mark.parametrize("decision,status,expected", [
    ("agree", "", True), (None, "", True), (None, "unreviewed", True), ("agree", "unreviewed", False),
    ("agree", "agree", True), ("disagree", "agree", False), (None, "agree", False), ("unsure", "unsure", True), ("disagree", "disagree", True),
])
def test_review_status_filtering(decision, status, expected):
    assert value(f"matchesReviewStatus({json.dumps(decision)}, {json.dumps(status)})") is expected


@needs_node
@pytest.mark.parametrize("reviewed,at,expected", [
    ([], 0, 1),                         # the next one along
    (["c/l2"], 0, 2),                   # skips a reviewed item
    (["d/h02/abc"], 1, 0),              # wraps around
    (["c/l1", "d/h02/abc"], 1, -1),     # the current item is not its own next
    (["c/l1", "c/l2", "d/h02/abc"], 0, -1),
])
def test_next_unreviewed(reviewed, at, expected):
    reviews = {id_: {"decision": "agree", "note": ""} for id_ in reviewed}
    assert value(f"nextUnreviewed(IDS, {at}, {json.dumps(reviews)})") == expected


@needs_node
def test_a_note_alone_does_not_count_as_reviewed_for_next_unreviewed():
    assert value("nextUnreviewed(IDS, 0, {'c/l2': {decision: null, note: 'n'}})") == 1


def test_the_page_has_no_way_to_send_or_write_anything_but_a_download():
    html = av.render_html([], [], {}, experiment=EXPERIMENT)
    assert '"experiment":{"manifest_sha256":"m"' in html
    for forbidden in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "showSaveFilePicker", "<form"):
        assert forbidden not in html


def test_every_category_says_in_plain_words_what_the_finding_is(real):
    _, items, _, _ = real
    assert all(i["finding"] != i["category"] for i in items)


# ── the real frozen artifacts ────────────────────────────────────────────────
def experiment_hashes():
    hashes = {}
    for folder, _, names in os.walk(sc.HERE):
        if "__pycache__" not in folder:
            for name in names:
                path = os.path.join(folder, name)
                with open(path, "rb") as f:
                    hashes[path] = hashlib.sha256(f.read()).hexdigest()
    return hashes


@pytest.fixture(scope="module")
def real():
    scores, predictions, labels, texts = av.load()
    items, svgs = av.build_items(scores, predictions, labels, texts)
    return scores, items, svgs, labels


def test_every_details_row_becomes_an_item(real):
    scores, items, _, _ = real
    for category, rows in scores["details"].items():
        assert sum(i["category"] == category for i in items) == len(rows)
    assert len({i["id"] for i in items}) == len(items)


def test_caught_false_claims_agree_with_the_scores(real):
    scores, items, _, _ = real
    assert sum(i["category"] == av.CAUGHT_FACT for i in items) == scores["end_to_end_facts"]["false_caught"]
    assert sum(i["category"] == av.CAUGHT_VARIATION for i in items) == scores["variations"]["false_caught"]
    assert all(i["is_false"] and i["candidate"]["verifier_status"] == "FALSE" for i in items if i["caught"])


def test_every_real_item_has_a_board_and_its_span(real):
    _, items, svgs, labels = real
    by_id = {r["label_id"]: r for r in labels}
    for i in items:
        assert i["panels"] and all(0 <= p["svg"] < len(svgs) for p in i["panels"])
        if i["label_id"]:
            assert i["context"]["span"] == by_id[i["label_id"]]["source_text"]
    assert all(i["candidate"] for i in items if i["category"] in ("extraction_with_no_label", "variation_with_no_label"))


def test_real_board_binding_failures_show_both_boards_whenever_the_extractor_named_one(real):
    _, items, _, _ = real
    wrong = [i for i in items if i["category"] == "board_binding_wrong"]
    assert len(wrong) == 10
    for i in wrong:
        expected = ["gold"] if i["candidate"]["board_moves"] is None else ["gold", "extractor"]
        assert [p["role"] for p in i["panels"]] == expected
    assert sum(i["candidate"]["board_moves"] is not None for i in wrong) == 5


def test_every_real_board_binding_item_explains_itself_and_no_other_item_does(real):
    _, items, _, _ = real
    wrong = [i for i in items if i["category"] == "board_binding_wrong"]
    assert all("why_flagged" in i and i["review_hints"] for i in wrong)
    assert not any("why_flagged" in i for i in items if i["category"] != "board_binding_wrong")
    assert not any(i["why_flagged"]["warning"] for i in wrong)  # the scorer flagged no identical branches
    assert {i["why_flagged"]["comparison"] for i in wrong} == {"DIFFERENT BOARD", "EXTRACTOR HAS NO RESOLVED BOARD"}
    for i in wrong:
        w, c = i["why_flagged"], i["candidate"]
        assert w["verifier_result"] == c["verifier_status"] and w["gold_truth"] == i["gold"]["chess_truth"]
        assert (w["extractor_position"] == "NONE / unresolved") == (c["board_moves"] is None)
        assert len(i["panels"]) == (1 if c["board_moves"] is None else 2)  # the boards themselves are unchanged


def test_real_examples_read_as_intended(real):
    _, items, _, _ = real
    by_id = {i["id"]: i["why_flagged"] for i in items if "why_flagged" in i}
    late = by_id["board_binding_wrong/l0033"]
    assert late["gold_position"] == "after 25. cxd4 Rd8 26. d5" and late["extractor_position"] == "after 25. cxd4 Rd8 26. d5 exd3"
    assert "one move too late" in late["explanation"] and late["verifier_result"] == "FALSE" and late["gold_truth"] == "FALSE"
    assert "does not count as a correct result" in late["verdict_note"]
    unresolved = by_id["board_binding_wrong/l1018"]
    assert unresolved["extractor_position"] == "NONE / unresolved" and unresolved["verifier_result"] == "UNKNOWN"
    root = by_id["board_binding_wrong/l0057"]
    assert root["extractor_position"] == "root position" and "one move too early" in root["explanation"]


def test_the_page_shows_the_section_and_a_mislabelled_item_gets_the_warning():
    item = {"id": "board_binding_wrong/x", "why_flagged": why(["e2e4"], ["e2e4"])}
    html = av.render_html([item], [], {})
    assert "WHY THIS IS FLAGGED" in html and "both branches are identical" in html
    gold = {"kind": "fact", "predicate": "attacks", "args": {"source": "e4", "target": "f5"}, "branch": ["e2e4"], "chess_truth": "TRUE", "label_id": "x", "polarity": "positive", "span_start": 0, "span_end": 1, "source_text": "x", "extraction_class": "EXTRACTED"}
    made = av._make_item("board_binding_wrong/x", "board_binding_wrong", "h", gold, candidate(["e2e4"], status="EXTRACTED", verifier_status="VERIFIED"), {}, {"fen": START, "facts": []}, "text", {})
    assert made["why_flagged"]["warning"] and len(made["panels"]) == 1  # identical branches keep one board


def test_a_caught_change_claim_shows_what_it_changed_from(real):
    _, items, _, _ = real
    item = next(i for i in items if i["id"] == f"{av.CAUGHT_FACT}/l0355")
    assert item["verifier_disagrees"] and item["candidate"]["change_from"] and item["candidate"]["verifier_status"] == "FALSE"


def test_the_sample_is_the_requested_composition_and_deterministic(real):
    _, items, _, _ = real
    sample = av.error_analysis_sample(items)
    assert {k: len(v) for k, v in sample.items()} == {"extractable_claim_missed": 15, "board_binding_wrong": 10, "predicate_right_args_wrong": 4,
                                                     "hallucinated_on_non_fact": 5, "variation_missed": 10, av.CAUGHT_FACT: 5}
    assert sample == av.error_analysis_sample(list(reversed(items)))
    assert sample != av.error_analysis_sample(items, seed=1)


def test_the_fixed_sample_still_selects_the_same_items(real):
    _, items, _, _ = real  # the fingerprint of the sample as it was first reviewed
    sample = av.error_analysis_sample(items)
    assert hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest() == "a005deb1ded74f5366882b7d6ad342e78a59282b76776a726f2a9b8d6a9df2f0"


def test_real_item_ids_survive_a_rebuild(real):
    scores, items, _, labels = real
    again = av.build_items(scores, *av.load()[1:])[0]
    assert [i["id"] for i in again] == [i["id"] for i in items]
    assert all("/" in i["id"] for i in items) and not any(i["id"].split("/")[-1].isdigit() for i in items)


def test_building_the_page_changes_no_experiment_file(tmp_path):
    before = experiment_hashes()
    out = tmp_path / "audit.html"
    assert av.main(["--out", str(out)]) == 0
    assert experiment_hashes() == before
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_stale_scores_are_refused(tmp_path):
    for path, content in (("scores.json", json.dumps({"meta": {"manifest_sha256": "old", "labels_sha256": "old"}})), ("labels.jsonl", "")):
        (tmp_path / path).write_text(content)
    (tmp_path / "predictions").mkdir()
    (tmp_path / "predictions" / "MANIFEST.json").write_text("{}")
    with pytest.raises(sc.ScoreError):
        av.load(str(tmp_path))
