"""The held-out audit viewer: SAN replay, board selection, span context, board-binding differences,
and that it reads the real frozen artifacts without changing them."""
import hashlib
import json
import os

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
