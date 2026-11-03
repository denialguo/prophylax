"""Claim extraction from QUEEN's prose, kept apart from chess truth: extraction reports what
a sentence says, the verifier decides whether it holds. No QUEEN, no engine, no remote API."""
import ast
import hashlib
import inspect
import json
import os

import chess
import pytest

import domain.extract as extract_module
from domain.extract import ExtractedFact, extract, extract_variation
from domain.verify import Fact, verify_variation
from evals.queen_extraction import BENCH, evaluate, judge_fact, load_cases, load_gold

KINGS = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
BISHOP_C5 = "4k3/8/8/2B3n1/8/8/8/4K3 w - - 0 1"
KNIGHT_F7 = "k4r2/5Np1/8/8/8/8/8/4K3 w - - 0 1"


def facts(text, fen=KINGS):
    return [f for f in extract(text, fen).facts if f.status == "EXTRACTED"]


def only(text, fen=KINGS) -> ExtractedFact:
    found = facts(text, fen)
    assert len(found) == 1, [(f.fact.predicate, f.fact.args) for f in found]
    return found[0]


def statuses(text, fen=KINGS):
    return {f.status for f in extract(text, fen).facts}


def verdict(candidate, fen=KINGS):
    return judge_fact(fen, candidate)["status"]


# ── the key architecture test ────────────────────────────────────────────────
def test_a_false_claim_is_extracted_as_written_and_only_the_verifier_rejects_it():
    c = only("The bishop on c5 attacks g5.", BISHOP_C5)
    assert c.status == "EXTRACTED"
    assert (c.fact.predicate, c.fact.args) == ("attacks", {"source": "c5", "target": "g5", "piece": "bishop"})
    assert verdict(c, BISHOP_C5) == "FALSE"


def test_the_extractor_gives_the_same_reading_whatever_the_board_says():
    # the same sentence, one position where it is true and one where it is not
    text = "The bishop on c5 attacks e7."
    true_board, false_board = BISHOP_C5, "4k3/8/8/8/8/8/8/2B1K3 w - - 0 1"
    assert only(text, true_board).fact == only(text, false_board).fact
    assert verdict(only(text, true_board), true_board) == "VERIFIED"
    assert verdict(only(text, false_board), false_board) == "FALSE"


def test_the_extractor_never_consults_the_verifier():
    tree = ast.parse(inspect.getsource(extract_module))
    from_verify = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "domain.verify" for a in n.names}
    assert from_verify == {"Fact", "Ply"}  # data types, no predicate


def test_nothing_in_production_imports_extraction_or_the_benchmark():
    root = os.path.dirname(os.path.dirname(__file__))
    for folder in ("app", "mcp_server", "history", "config", "hooks", "scripts"):
        for base, _, names in os.walk(os.path.join(root, folder)):
            for name in names:
                if name.endswith(".py"):
                    with open(os.path.join(base, name), encoding="utf-8") as f:
                        source = f.read()
                    assert "domain.extract" not in source and "queen_extraction" not in source, name


# ── facts ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,fen,predicate,args,want", [
    ("The knight on f7 attacks the rook on f8.", KNIGHT_F7, "attacks", {"source": "f7", "target": "f8", "piece": "knight"}, "FALSE"),
    ("The knight on f7 attacks g5.", KNIGHT_F7, "attacks", {"source": "f7", "target": "g5", "piece": "knight"}, "VERIFIED"),
    ("The pawn on c6 is one move from promotion.", "4k3/8/2P5/8/8/8/8/4K3 w - - 0 1", "promotion_distance_is", {"square": "c6", "moves": 1}, "FALSE"),
    ("The pawn on c6 is two moves from promotion.", "4k3/8/2P5/8/8/8/8/4K3 w - - 0 1", "promotion_distance_is", {"square": "c6", "moves": 2}, "VERIFIED"),
    ("The pawn on f3 blocks the bishop on f1.", "4k3/8/8/8/8/5P2/8/4KB2 w - - 0 1", "blocks", {"slider": "f1", "blocker": "f3"}, "FALSE"),
    ("The c-file is open.", KINGS, "is_open_file", {"file": "c"}, "VERIFIED"),
    ("The c-file is open.", "4k3/2p5/8/8/8/8/8/4K3 w - - 0 1", "is_open_file", {"file": "c"}, "FALSE"),
    ("The white pawn on b6 is passed.", "4k3/p7/1P6/8/8/8/8/4K3 w - - 0 1", "is_passed_pawn", {"square": "b6"}, "FALSE"),
    ("The white pawn on b6 is passed.", "4k3/8/1P6/8/8/8/8/4K3 w - - 0 1", "is_passed_pawn", {"square": "b6"}, "VERIFIED"),
    ("The open c5-f2 diagonal decides.", "4k3/8/8/2b5/8/4P3/5P2/4K3 b - - 0 1", "is_open_diagonal", {"source": "c5", "target": "f2"}, "FALSE"),
    ("The black king sits on e8.", KINGS, "piece_at", {"square": "e8", "color": "black", "piece": "king"}, "VERIFIED"),
    ("The white queen sits on e1.", KINGS, "piece_at", {"square": "e1", "color": "white", "piece": "queen"}, "FALSE"),
    ("Material equality is restored.", KINGS, "material_balance_is", {"points": 0}, "VERIFIED"),
])
def test_supported_claims(text, fen, predicate, args, want):
    c = only(text, fen)
    assert (c.fact.predicate, c.fact.args) == (predicate, args)
    assert verdict(c, fen) == want


def test_a_stated_pin_is_read_and_a_blocker_disproves_it():
    fen = "3qk3/3b4/3n4/8/8/8/8/3RK3 w - - 0 1"
    c = only("The white rook on d1 pins the black knight on d6 against the black queen on d8.", fen)
    assert (c.fact.predicate, c.fact.args) == ("pinned_against", {"pinner": "d1", "pinned": "d6", "target": "d8"})
    assert verdict(c, fen) == "FALSE"
    clear = "3qk3/8/3n4/8/8/8/8/3RK3 w - - 0 1"
    assert verdict(only("The white rook on d1 pins the black knight on d6 against the black queen on d8.", clear), clear) == "VERIFIED"


def test_terminal_language_binds_to_the_move_that_was_mentioned():
    fen = "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1"
    found = facts("I. white rook d1-d8 — checkmate. The rook delivers check along the eighth rank. "
                  "The black king on g8 has no legal reply.", fen)
    assert [f.fact.predicate for f in found] == ["is_checkmate", "is_check", "legal_move_count_is"]
    assert all(f.prefix == ("d1d8",) and verdict(f, fen) == "VERIFIED" for f in found)


def test_several_claims_in_one_sentence():
    found = facts("The white rook on a1 attacks the black pawn on a7 and the black knight on g1.",
                  "4k3/p7/8/8/8/8/8/R3K1n1 w - - 0 1")
    assert [f.fact.args["target"] for f in found] == ["a7", "g1"]
    multi = facts("c6 and e6 are controlled by the white knight on e5.")
    assert [f.fact.args["target"] for f in multi] == ["c6", "e6"]


def test_a_literal_fact_beside_a_plan_word_is_read_and_a_bare_plan_is_not():
    c = only("The bishop reroutes to f2 and attacks b6.")
    assert (c.fact.predicate, c.fact.args["source"], c.fact.args["target"]) == ("attacks", "f2", "b6")
    assert not facts("The bishop reroutes to improve coordination.")


# ── safety fields ────────────────────────────────────────────────────────────
def test_a_stated_target_stops_an_attack_on_an_empty_square_from_verifying():
    fen = "4k3/8/8/2B5/8/8/8/4K3 w - - 0 1"  # e7 is empty, and a bishop does bear on it
    c = only("The bishop on c5 attacks the rook on e7.", fen)
    assert c.fact.args["target"] == "e7" and c.guards[0].args == {"square": "e7", "piece": "rook"}
    assert verdict(c, fen) == "FALSE"


def test_a_stated_colour_is_part_of_the_claim():
    fen = "4k3/8/8/2b5/8/8/8/4K3 w - - 0 1"
    assert verdict(only("The white bishop on c5 attacks e7.", fen), fen) == "FALSE"


def test_creating_a_passed_pawn_means_it_was_not_passed_before():
    fen = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
    c = only("I. white pawn e2-e4 — immediately creating a passed pawn.", fen)
    assert c.change_from.args == {"square": "e2"} and verdict(c, fen) == "FALSE"  # it already was one


def test_a_pawn_that_really_becomes_passed_is_accepted():
    fen = "4k3/8/8/2p5/1P6/8/8/4K3 w - - 0 1"  # after bxc5 no Black pawn is left ahead of it
    assert verdict(only("I. white pawn b4 takes black pawn c5 — creating a passed pawn.", fen), fen) == "VERIFIED"


def test_opening_a_file_needs_it_to_become_open():
    fen = "4k3/8/8/8/2p5/1P6/8/4K3 w - - 0 1"
    c = only("I. white pawn b3 takes black pawn c4 — opening the a-file.", fen)
    assert c.change_from.args == {"file": "a"} and verdict(c, fen) == "FALSE"  # already open: nothing opened


@pytest.mark.parametrize("text", [
    "The knight on e4 is not attacked by the king on d7.",
    "The bishop on c5 cannot attack the pawn on g5.",
    "The rook on a1 could attack the pawn on a7.",
    "The bishop on c5 would attack g5.",
    "This creates a potential passed pawn on c6.",
    "The threat is to open the a-file.",
])
def test_negated_modal_and_future_statements_are_never_positive_facts(text):
    assert not facts(text)


def test_an_unresolved_board_is_unknown_never_judged_on_the_root():
    fen = "4k3/8/8/2B5/8/8/8/4K3 w - - 0 1"
    c = ExtractedFact(sentence="x", span_start=0, span_end=1, status="EXTRACTED", prefix=None,
                      fact=Fact(predicate="attacks", args={"source": "c5", "target": "e7"}))
    assert judge_fact(fen, c)["status"] == "UNKNOWN"  # true on the root, but this claim is about another board


def test_a_non_extracted_row_is_not_judged():
    c = ExtractedFact(sentence="x", span_start=0, span_end=1, status="UNSUPPORTED")
    assert judge_fact(KINGS, c) is None


# ── unsupported and ambiguous ────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "White gains lasting pressure on the queenside.", "This move improves coordination.",
    "Black has excellent practical chances.", "...h6 traps the knight.", "Be1 is prophylactic.",
    "White seizes the initiative.", "The knight dominates the board.", "This is the only move.",
    "White has a better endgame.", "White has dangerous compensation.", "White has a space advantage.",
])
def test_strategic_language_is_unsupported_not_forced_into_a_fact(text):
    assert statuses(text) == {"UNSUPPORTED"}


@pytest.mark.parametrize("text", [
    "The bishop is pinned to the queen.", "The rook is attacked.", "Black attacks the bishop.",
    "White targets the pawn on b5.", "The pawn is passed.",
])
def test_concrete_words_without_resolved_terms_are_ambiguous(text):
    assert statuses(text) == {"AMBIGUOUS"}


# ── provenance ───────────────────────────────────────────────────────────────
def test_a_candidate_points_back_to_its_exact_text():
    text = "ANALYSIS:\nThe position is quiet. The bishop on c5 attacks g5. More follows."
    c = only(text, BISHOP_C5)
    assert c.sentence.strip() == "The bishop on c5 attacks g5."
    assert text[c.span_start:c.span_end] == "attacks g5"
    assert extract(text, BISHOP_C5, "run-7").source_id == "run-7"


def test_extraction_is_deterministic():
    case = load_cases()["10_carlsbad_after"]
    assert extract(case["text"], case["fen"], "x").model_dump_json() == extract(case["text"], case["fen"], "x").model_dump_json()


# ── branches ─────────────────────────────────────────────────────────────────
def test_a_claim_after_a_line_is_about_that_line():
    fen = "4k3/8/8/8/8/8/P7/4K3 w - - 0 1"
    c = only("I. white pawn a2-a4 black king e8-d7 white pawn a4-a5 — the white pawn on a5 is passed.", fen)
    assert c.prefix == ("a2a4", "e8d7", "a4a5") and verdict(c, fen) == "VERIFIED"


def test_a_piece_named_in_the_branch_is_read_in_the_branch():
    # no White pawn stands on e5 at the root: the claim is about the board after the capture
    fen = "4k3/8/8/4p3/3P4/8/8/4K3 w - - 0 1"
    c = only("I. white pawn d4 takes black pawn e5 — the white pawn on e5 is passed.", fen)
    assert c.prefix == ("d4e5",) and verdict(c, fen) == "VERIFIED"


def test_a_sub_variation_branches_by_its_move_number():
    case = load_cases()["10_carlsbad_after"]
    lines = [v.ucis for v in extract(case["text"], case["fen"]).variations if v.kind == "prose"]
    assert ("e2f4", "h7h6", "g5h4", "g7g5", "h4g3", "b5b4") in lines  # 2. Bh4, from the main line after 1...h6


def test_an_unstated_branch_point_is_ambiguous_and_leaves_its_claims_unknown():
    case = load_cases()["07_noahs_ark"]
    result = extract(case["text"], case["fen"])
    assert any(v.status == "AMBIGUOUS" for v in result.variations)
    unresolved = [f for f in result.facts if f.status == "EXTRACTED" and f.prefix is None]
    assert unresolved and all(judge_fact(case["fen"], f)["status"] == "UNKNOWN" for f in unresolved)


def test_a_move_after_an_idea_word_is_not_in_the_line():
    fen = "4k3/8/8/8/8/8/P7/4K2R w K - 0 1"
    result = extract("I. white pawn a2-a4 — preparing white rook h1-h8.", fen)
    assert [v.ucis for v in result.variations] == [("a2a4",)]


def test_claims_after_an_illegal_ply_have_no_board():
    case = load_cases()["02_legal_trap"]
    result = extract(case["text"], case["fen"])
    mate = next(f for f in result.facts if f.status == "EXTRACTED" and f.fact.predicate == "is_checkmate")
    assert mate.prefix is None and judge_fact(case["fen"], mate)["status"] == "UNKNOWN"


# ── variations ───────────────────────────────────────────────────────────────
ITALIAN = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 4 5"


def test_san_variation():
    v = extract_variation("Bxf7+ Kxf7 Nd5", ITALIAN)
    assert v.status == "EXTRACTED" and v.ucis == ("c4f7", "e8f7", "c3d5")
    assert verify_variation(ITALIAN, list(v.plies)).status == "VERIFIED"


def test_verbose_queen_variation():
    v = extract_variation("white bishop c4 takes black pawn f7 black king e8 takes white bishop f7 white knight c3-d5", ITALIAN)
    assert v.status == "EXTRACTED" and v.ucis == ("c4f7", "e8f7", "c3d5")
    assert [p.captures for p in v.plies] == ["pawn", "bishop", "none"]
    assert verify_variation(ITALIAN, list(v.plies)).status == "VERIFIED"


def test_a_false_check_in_a_line_is_kept_and_rejected_by_the_verifier():
    v = extract_variation("Bxf7+ Kxf7 Nd5+", ITALIAN)
    assert v.status == "EXTRACTED" and v.plies[-1].san == "Nd5+"
    assert verify_variation(ITALIAN, list(v.plies)).status == "FALSE"


def test_a_false_capture_description_is_kept_and_rejected():
    v = extract_variation("white bishop c4 takes black knight f7", ITALIAN)
    assert v.plies[0].captures == "knight"
    assert verify_variation(ITALIAN, list(v.plies)).status == "FALSE"


def test_an_illegal_line_is_extracted_and_the_verifier_rejects_it():
    v = extract_variation("white pawn c2-c5", ITALIAN)
    assert v.status == "EXTRACTED" and v.ucis == ("c2c5",)
    assert verify_variation(ITALIAN, list(v.plies)).status == "FALSE"


def test_an_ambiguous_san_is_reported_not_guessed():
    fen = "4k3/8/8/8/8/8/8/N1N1K3 w - - 0 1"
    assert extract_variation("Nb3", fen).status == "AMBIGUOUS"
    assert extract_variation("white knight a1-b3", fen).ucis == ("a1b3",)  # the source square settles it


def test_queen_promotion_and_castling_notation():
    v = extract_variation("white pawn c7-c8white queen", "7k/2P5/8/8/8/8/8/K7 w - - 0 1")
    assert v.ucis == ("c7c8q",) and v.plies[0].san == "c7c8=Q"
    fen = "4k3/8/8/8/8/8/8/4K2R w K - 0 1"
    castle = extract_variation("white king e1-g1", fen)
    assert castle.plies[0].san == "O-O" and verify_variation(fen, list(castle.plies)).status == "VERIFIED"


def test_structured_lines_become_their_own_variations():
    case = load_cases()["15_c6_promotion_distance"]
    result = extract(case["text"], case["fen"])
    assert [v.ucis for v in result.variations if v.kind == "critical_line"] == [("c6c7", "h8g7", "c7c8q")]
    assert [v.ucis for v in result.variations if v.kind == "best_move"] == [("c6c7",)]
    assert [v.ucis for v in result.variations if v.kind == "promising_move"] == [("c6c7",), ("a1b1",), ("a1b2",)]


# ── malformed ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", ["", "   \n "])
def test_empty_text_is_a_parse_error(text):
    assert statuses(text) == {"PARSE_ERROR"}
    assert extract_variation(text, KINGS).status == "PARSE_ERROR"


def test_a_broken_move_is_a_parse_error_not_a_move():
    assert "PARSE_ERROR" in statuses("I. white knight z9-f4 — a strong move.")
    assert extract("I. white knight z9-f4 — a strong move.", KINGS).variations == ()
    assert extract_variation("Bxf7+ then something", ITALIAN).status == "PARSE_ERROR"


def test_non_chess_text_produces_nothing_verifiable():
    result = extract("The quick brown fox. <b>12345</b> ... !!! ???", KINGS)
    assert all(f.status != "EXTRACTED" for f in result.facts) and result.variations == ()


# ── the benchmark ────────────────────────────────────────────────────────────
def test_the_stored_outputs_are_the_audited_ones():
    with open(os.path.join(BENCH, "cases.json"), encoding="utf-8") as f:
        manifest = json.load(f)["cases"]
    assert len(manifest) == 18
    for c in manifest:
        with open(os.path.join(BENCH, "outputs", c["id"] + ".txt"), "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest() == c["sha256"], c["id"]


def test_every_label_quotes_the_output_exactly_once():
    cases = load_cases()
    for g in load_gold():
        assert cases[g["case"]]["text"].count(g["quote"]) == 1, g["quote"]


def test_every_labelled_board_is_legal_from_the_case_position():
    cases = load_cases()
    for g in load_gold():
        board = chess.Board(cases[g["case"]]["fen"])
        for uci in g.get("board") or []:
            board.push_uci(uci)  # raises on a label that does not replay


def test_no_false_verified_on_the_benchmark():
    result = evaluate()
    assert result["verification"].get("false_verification_acceptance", 0) == 0, result["details"].get("FALSE_ACCEPTANCE")


def test_no_candidate_outside_the_labels_verifies_a_wrong_reading():
    """The hand-reviewed VERIFIED candidates the labels do not cover (all correct reads)."""
    result = evaluate()
    verified = sorted((r[3], json.dumps(r[4], sort_keys=True)) for r in result["unlabelled_candidates"] if r[5] == "VERIFIED")
    assert verified == sorted([
        ("piece_at", json.dumps({"square": "e1", "piece": "king", "color": "white"}, sort_keys=True)),
        ("is_passed_pawn", json.dumps({"square": "c3"}, sort_keys=True)),
        ("blocks", json.dumps({"slider": "a1", "blocker": "c1"}, sort_keys=True)),
        ("piece_at", json.dumps({"square": "a8", "piece": "king"}, sort_keys=True)),
    ])


def test_extraction_scores_on_the_audited_labels():
    """Pins the development/tuning scores after the label audit (label_review.jsonl), which
    moved labels that described the grammar's limit rather than the text. The missed claims and
    lines are real misses of the frozen extractor, left unfixed for the held-out evaluation."""
    e = evaluate()["extraction"]
    assert (e["exact_match"], e["facts_labelled"], e["board_match"], e["missed"]) == (83, 106, 83, 23)
    assert (e["unsupported_detected"], e["unsupported_labelled"]) == (37, 38)
    assert (e["ambiguous_detected"], e["ambiguous_labelled"]) == (4, 4)
    assert (e["variations_exact"], e["variations_labelled"], e["variations_missed"]) == (45, 48, 3)
    assert e.get("hallucinated_on_non_claim", 0) == 0


def test_every_label_correction_is_recorded_and_applied():
    with open(os.path.join(BENCH, "label_review.jsonl"), encoding="utf-8") as f:
        review = [json.loads(line) for line in f]
    gold = load_gold()
    assert len(review) == 19
    for r in review:
        assert r["reason"] and r["old"] != r["new"] and isinstance(r["changes_metrics"], bool)
        assert r["new"] in gold and r["old"] not in gold
