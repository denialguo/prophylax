"""Extractor V2.1 with scripted V2 answers, never a live call: quote alignment, the phrase rules, the
merger's conflict rules, and that no source's opinion reaches a verdict."""
import os

import chess
import pytest

from domain import extract_v2 as x
from domain import extract_v2_1 as h
from domain.extract_v2 import ScriptedBackend
from domain.verify import Fact
from evals import queen_extraction as qe
from evals import queen_extraction_v2 as q2
from evals import queen_extraction_v2_1 as q21
from evals.queen_heldout import score as sc

START = chess.STARTING_FEN


def claim(quote, predicate=None, status="EXTRACTED", branch=(), polarity="positive", change=False, identities=(), **args):
    return {"source_text": quote, "status": status, "predicate": predicate, "args": [{"name": k, "value": str(v)} for k, v in args.items()], "polarity": polarity, "is_change": change,
            "branch": list(branch) if branch is not None else None, "stated_identities": [{"square": s, "color": c, "piece": p} for s, c, p in identities], "interpretations": [], "reason": None}


def run(text, *claims_, fen=START):
    return h.extract_hybrid(text, fen, ScriptedBackend({"claims": list(claims_)}), "t").facts


def extracted(text, *claims_, fen=START):
    return [f for f in run(text, *claims_, fen=fen) if f.status == "EXTRACTED"]


def find(facts, predicate):
    return [f for f in facts if f.fact and f.fact.predicate == predicate]


def verdict(candidate, fen=START):
    return q21.judge_hybrid(fen, candidate)["status"]


# ── quote alignment ──────────────────────────────────────────────────────────
PARAGRAPH = "The rook on d1 — a strong piece — attacks the pawn on d7. White’s  king  stays home, and the knight is fine."


def align(quote, text=PARAGRAPH):
    return h.align_quote(quote, text, 0, len(text))


def test_an_exact_quote_aligns_exactly():
    a = align("attacks the pawn on d7")
    assert a["method"] == "exact" and PARAGRAPH[a["span"][0]:a["span"][1]] == "attacks the pawn on d7" and a["score"] == 1.0


def test_a_punctuation_difference_aligns():
    a = align("attacks the pawn on d7,")
    assert a["method"] == "normalized" and PARAGRAPH[a["span"][0]:a["span"][1]].startswith("attacks the pawn on d7")


def test_a_whitespace_difference_aligns():
    a = align("White’s king stays home")
    assert a["method"] == "normalized" and PARAGRAPH[a["span"][0]:a["span"][1]] == "White’s  king  stays home"


@pytest.mark.parametrize("quote", ["The rook on d1 - a strong piece", "White's  king  stays home"])
def test_a_unicode_dash_or_quote_difference_aligns(quote):
    assert align(quote)["method"] == "normalized"


def test_an_article_or_case_difference_aligns_when_unique():
    assert align("a rook on d1")["method"] == "normalized" and align("THE ROOK ON D1")["method"] == "normalized"


def test_a_unique_minor_mismatch_is_fuzzy_with_its_score():
    a = align("attacks the pawns on d7")
    assert a["method"] == "fuzzy" and a["score"] >= h.FUZZY_THRESHOLD and "attacks the pawn on d7" in PARAGRAPH[a["span"][0]:a["span"][1]]


def test_a_quote_fitting_two_places_is_ambiguous():
    text = "The  rook is here. Then the rook is here."
    assert align("the rook is  here", text)["method"] == "ambiguous"


def test_a_fuzzy_match_with_a_close_rival_is_ambiguous():
    text = "the white pawn on d4 is strong today. Later the white pawn on d5 is strong today."
    assert align("the white pawn on d6 is strong today", text)["method"] == "ambiguous"


def test_an_invented_quote_is_rejected():
    assert align("the queen delivers mate on h7 right now")["method"] == "invented" and align("")["method"] == "invented"


def test_a_short_near_match_is_not_trusted():
    assert align("the bishop")["method"] == "invented"


def test_alignment_stays_inside_the_chunk():
    text = "First part here. Second part here is unique."
    assert h.align_quote("Second part here is unique", text, 0, 16)["method"] == "invented"


def test_alignment_is_recorded_on_the_claim_with_both_texts():
    f = extracted("The king on e1 — safe.", claim("The king on e1 - safe", "piece_at", square="e1"))
    assert f[0].alignment["method"] == "normalized" and f[0].alignment["model_quote"] == "The king on e1 - safe"
    assert f[0].sentence == "The king on e1 — safe"


def test_an_invented_quote_makes_no_claim():
    assert [f.status for f in run("White is better.", claim("The queen on h5 mates", "piece_at", square="h5"))].count("PARSE_ERROR") == 1


def test_an_ambiguous_quote_makes_no_judged_claim():
    facts = run("The  king on e1 is here. And The king on e1 is here.", claim("the king on e1 is  here", "piece_at", square="e1"))
    assert not [f for f in facts if "v2" in f.produced_by and f.status == "EXTRACTED"]


# ── change claims ────────────────────────────────────────────────────────────
OPEN_ALREADY = "4k3/8/8/8/8/3P4/8/4K3 w - - 0 1"       # the e-file has no pawns at all
OPENS_REALLY = "4k3/8/8/8/4p3/3P4/8/4K3 w - - 0 1"     # dxe4 clears the d-file


def test_opens_a_file_requires_the_change_even_if_the_model_forgets():
    text = "1. dxe4 opens the d-file."
    f = find(extracted(text, claim("opens the d-file", "is_open_file", branch=["d3e4"], file="d"), fen=OPENS_REALLY), "is_open_file")[0]
    assert f.change_from is not None and verdict(f, OPENS_REALLY) == "VERIFIED"


def test_a_claim_already_true_before_the_move_is_false():
    text = "1. d4 opens the e-file."
    f = find(extracted(text, claim("opens the e-file", "is_open_file", branch=["d3d4"], file="e"), fen=OPEN_ALREADY), "is_open_file")[0]
    assert q21.judge_hybrid(OPEN_ALREADY, f)["reason"] == "it already held before the move" and verdict(f, OPEN_ALREADY) == "FALSE"


def test_becomes_open_requires_the_change():
    text = "1. d4 and the e-file becomes open."
    f = find(extracted(text, claim("the e-file becomes open", "is_open_file", branch=["d3d4"], file="e"), fen=OPEN_ALREADY), "is_open_file")[0]
    assert f.change_from is not None and verdict(f, OPEN_ALREADY) == "FALSE"


def test_creates_a_passed_pawn_compares_the_same_pawn():
    fen = "4k3/8/8/8/8/3P4/8/4K3 w - - 0 1"
    text = "1. d4 creates a passed pawn on d4."
    f = find(extracted(text, claim("a passed pawn on d4", "is_passed_pawn", branch=["d3d4"], square="d4"), fen=fen), "is_passed_pawn")[0]
    assert f.change_from == Fact(predicate="is_passed_pawn", args={"square": "d3"}) and verdict(f, fen) == "FALSE"


def test_a_v1_change_survives_a_v2_duplicate_that_lacks_it():
    a = _claim_for("is_open_file", {"file": "e"}, ("v1",), change=True)
    b = _claim_for("is_open_file", {"file": "e"}, ("v2",))
    merged = h.merge("x" * 40, [a, b])
    assert len(merged) == 1 and merged[0].change_from is not None and merged[0].produced_by == ("v1", "v2")


NO_LONGER_FEN = "4k3/8/8/4p3/8/8/8/4R1K1 w - - 0 1"  # the rook on e1 attacks e5; after Rd1 it does not


def test_no_longer_attacks_is_a_negative_that_must_have_held_before():
    text = "After 1. white rook e1-d1 the rook on d1 no longer attacks e5."
    f = find(run(text, fen=NO_LONGER_FEN), "attacks")
    assert f and f[0].polarity == "negative" and f[0].held_before == f[0].fact
    assert verdict(f[0], NO_LONGER_FEN) == "FALSE"  # the rook on d1 never attacked e5: it did not stand on d1 before


def test_no_longer_is_false_when_it_never_held():
    fen = "4k3/8/8/8/8/8/8/R5K1 w - - 0 1"
    text = "After 1. white king g1-g2 the rook on a1 no longer attacks h8."
    f = find(run(text, fen=fen), "attacks")[0]
    assert verdict(f, fen) == "FALSE" and "did not hold before" in q21.judge_hybrid(fen, f)["reason"]


def test_no_longer_holds_when_it_really_stopped():
    fen = "4k3/8/8/4p3/8/8/8/4R1K1 b - - 0 1"
    text = "After 1... black pawn e5-e4 2. white rook e1-d1 the rook on d1 no longer attacks e4."
    candidate = h.HybridFact(source_id="t", sentence="x", span_start=0, span_end=1, status="EXTRACTED", polarity="negative", fact=Fact(predicate="attacks", args={"source": "e1", "target": "e5"}), prefix=("g1g2",), held_before=Fact(predicate="attacks", args={"source": "e1", "target": "e5"}))
    assert text and q21.judge_hybrid("4k3/8/8/4p3/8/8/8/4R1K1 w - - 0 1", candidate)["status"] == "FALSE"  # it still attacks: the denial is false
    stopped = candidate.model_copy(update={"fact": Fact(predicate="attacks", args={"source": "d1", "target": "e5"}), "held_before": Fact(predicate="attacks", args={"source": "e1", "target": "e5"}), "prefix": ("e1d1",)})
    assert fen and q21.judge_hybrid("4k3/8/8/4p3/8/8/8/4R1K1 w - - 0 1", stopped)["status"] == "VERIFIED"


# ── counts and material ──────────────────────────────────────────────────────
def facts_of(text, fen=START):
    return [(f.fact.predicate, f.fact.args, f.polarity) for f in run(text, fen=fen) if f.status == "EXTRACTED" and "rules" in f.produced_by]


@pytest.mark.parametrize("text,points", [
    ("White is a full knight ahead.", 3), ("Black is a full knight ahead.", -3),
    ("White is a rook down.", -5), ("Black is a rook down.", 5),
    ("White is queen down.", -9), ("Black is down a queen.", 9),
    ("White is up a pawn.", 1), ("Black is up a pawn.", -1),
    ("White is down an exchange.", -2), ("Black is the exchange down.", 2),
    ("Black is two pawns up.", -2),
])
def test_material_language_maps_with_the_right_side(text, points):
    assert facts_of(text) == [("material_balance_is", {"points": points}, "positive")]


def test_material_is_equal_and_its_negative():
    assert facts_of("Material is equal.") == [("material_balance_is", {"points": 0}, "positive")]
    assert facts_of("Material is not equal.") == [("material_balance_is", {"points": 0}, "negative")]


def test_a_negative_material_statement_stays_negative():
    assert facts_of("White is not a pawn up.") == [("material_balance_is", {"points": 1}, "negative")]


def test_material_with_no_stated_side_is_not_guessed():
    facts = run("With a rook up the plan is simple.")
    assert not [f for f in facts if f.status == "EXTRACTED"] and [f for f in facts if f.status == "AMBIGUOUS" and "rules" in f.produced_by]


@pytest.mark.parametrize("text,color,piece,count", [
    ("Black has no queen.", "black", "queen", 0), ("White has no rooks.", "white", "rook", 0),
    ("White has two bishops.", "white", "bishop", 2), ("Black has two bishops.", "black", "bishop", 2),
    ("Only one white rook remains.", "white", "rook", 1), ("Only one black rook remains.", "black", "rook", 1),
    ("Black does not have a queen.", "black", "queen", 0),
])
def test_count_language_maps_with_the_right_side(text, color, piece, count):
    assert facts_of(text) == [("piece_count_is", {"color": color, "piece": piece, "count": count}, "positive")]


def test_a_denied_number_is_a_negative_count():
    assert facts_of("White does not have two bishops.") == [("piece_count_is", {"color": "white", "piece": "bishop", "count": 2}, "negative")]


def test_a_list_gives_one_count_each():
    assert {(a["piece"], a["count"]) for _, a, _ in facts_of("Black has a rook and six pawns.")} == {("rook", 1), ("pawn", 6)}


def test_a_list_against_a_list_counts_both_sides():
    got = {(a["color"], a["piece"], a["count"]) for _, a, _ in facts_of("White holds a decisive advantage—queen and knight against a lone rook and two pawns.")}
    assert got == {("white", "queen", 1), ("white", "knight", 1), ("black", "rook", 1), ("black", "pawn", 2)}


def test_no_pieces_expands_to_each_piece():
    assert {a["piece"] for _, a, _ in facts_of("Black has no pieces.")} == {"knight", "bishop", "rook", "queen"}


@pytest.mark.parametrize("text", ["There is no queen on the board.", "Only one rook remains."])
def test_a_count_with_no_side_is_ambiguous(text):
    facts = run(text)
    assert not [f for f in facts if f.status == "EXTRACTED"] and [f for f in facts if f.status == "AMBIGUOUS"]


@pytest.mark.parametrize("text", ["White has a rook on e4.", "Two bishops are strong.", "White has the bishop pair and a lasting edge.", "White gains the initiative."])
def test_a_phrase_that_is_not_a_count_produces_nothing(text):
    assert facts_of(text) == []


def test_a_count_reaches_the_verifier():
    f = find(run("Black has no queen."), "piece_count_is")[0]
    assert f.fact.args["count"] == 0 and verdict(f) == "FALSE"  # the start position has a black queen


# ── negatives ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,predicate,args", [
    ("The pawn on d5 is not passed.", "is_passed_pawn", {"square": "d5"}),
    ("The d5 pawn is not a passed pawn.", "is_passed_pawn", {"square": "d5"}),
    ("The file is open and the e-file is not open.", "is_open_file", {"file": "e"}),
    ("The rook on a1 does not attack a7.", "attacks", {"source": "a1", "target": "a7", "piece": "rook"}),
    ("The rook on a1 no longer attacks a7.", "attacks", {"source": "a1", "target": "a7", "piece": "rook"}),
    ("The rook on a1 does not defend a2.", "defends", {"source": "a1", "target": "a2", "piece": "rook"}),
])
def test_explicit_negatives_keep_the_underlying_positive_fact(text, predicate, args):
    assert (predicate, args, "negative") in facts_of(text)


def test_there_is_no_white_queen_is_a_direct_positive_count():
    assert facts_of("There is no white queen.") == [("piece_count_is", {"color": "white", "piece": "queen", "count": 0}, "positive")]


def test_a_bare_negative_without_a_referent_is_ambiguous():
    assert [f.status for f in run("The pawn is not passed.")].count("AMBIGUOUS") >= 1 and not facts_of("The pawn is not passed.")


def test_a_bare_negative_with_a_unique_piece_is_resolved_from_the_board():
    fen = "4k3/8/8/8/8/8/8/R3K3 w - - 0 1"
    assert ("attacks", {"source": "a1", "target": "e7", "piece": "rook"}, "negative") in facts_of("The rook no longer attacks e7.", fen)


def test_a_bare_negative_with_two_candidates_is_ambiguous():
    fen = "4k3/8/8/8/8/8/8/R3K2R w - - 0 1"
    assert not facts_of("The rook no longer attacks e7.", fen)


def test_a_positive_proposal_on_negated_words_is_not_trusted():
    facts = run("The pawn on d5 is not passed.", claim("The pawn on d5 is not passed", "is_passed_pawn", square="d5"), fen="4k3/8/8/3P4/8/8/8/4K3 w - - 0 1")
    assert not [f for f in facts if f.status == "EXTRACTED" and f.polarity == "positive"]
    assert [f for f in facts if f.status == "EXTRACTED" and f.polarity == "negative"]  # the rule read it correctly


def test_a_negative_verdict_inverts_only_in_the_verifier():
    fen = "4k3/8/8/3P4/8/8/8/4K3 w - - 0 1"
    assert verdict(find(run("The pawn on d5 is not passed.", fen=fen), "is_passed_pawn")[0], fen) == "FALSE"


# ── attacks ──────────────────────────────────────────────────────────────────
ATTACK_FEN = "4k3/8/8/3p4/8/2N5/8/4K3 w - - 0 1"


@pytest.mark.parametrize("text", ["The knight on c3 attacks the pawn on d5.", "The knight on c3 threatens the pawn on d5.", "The knight on c3 is attacking d5."])
def test_explicit_attack_wording_maps_to_attacks(text):
    got = [a for p, a, _ in facts_of(text, ATTACK_FEN) if p == "attacks"]
    assert got and got[0]["source"] == "c3" and got[0]["target"] == "d5" and got[0]["piece"] == "knight"


@pytest.mark.parametrize("text", ["The knight on c3 eyes the pawn on d5.", "The knight on c3 targets the pawn on d5.", "The knight on c3 puts pressure on the pawn on d5.", "The knight on c3 threatens d5."])
def test_vague_or_move_threat_wording_is_never_an_attack(text):
    assert not [p for p, _, _ in facts_of(text, ATTACK_FEN) if p == "attacks"]


def test_the_stated_target_is_part_of_the_claim():
    f = find(run("The knight on c3 attacks the rook on d5.", fen=ATTACK_FEN), "attacks")[0]
    assert verdict(f, ATTACK_FEN) == "FALSE"  # there is a pawn on d5, not a rook


def test_a_participle_after_a_move_takes_the_moved_piece_as_source():
    fen = "r3k3/8/8/8/8/2N5/8/4K3 w - - 0 1"
    text = "1. white knight c3-b5, attacking the black pawn on a7 and the black pawn on c7."
    got = {a["target"]: a for p, a, _ in facts_of(text, fen) if p == "attacks"}
    assert set(got) == {"a7", "c7"} and got["a7"]["source"] == "b5" and got["a7"]["piece"] == "knight"


def test_a_participle_with_no_move_just_before_it_is_ignored():
    assert not facts_of("White is doing well, attacking the black pawn on a7.")


def test_a_referent_the_v2_proposal_guesses_is_ambiguous():
    two = "7k/8/8/8/8/2B2B2/8/K7 w - - 0 1"
    facts = run("The bishop attacks h8.", claim("The bishop attacks h8", "attacks", source="c3", target="h8", piece="bishop"), fen=two)
    assert not [f for f in facts if f.status == "EXTRACTED" and f.fact.predicate == "attacks"]


# ── board and branch binding ─────────────────────────────────────────────────
LINE = "1. e4 e5 2. Nf3 and now the knight on f3 attacks e5. Earlier the knight on g1 was home."
AFTER = ["e2e4", "e7e5", "g1f3"]


@pytest.mark.parametrize("quote,branch,predicate,args", [
    ("the knight on g1", [], "piece_at", {"square": "g1"}),                                   # root
    ("the knight on g1", ["e2e4"], "piece_at", {"square": "g1"}),                             # after one move
    ("the knight on g1", ["e2e4", "e7e5"], "piece_at", {"square": "g1"}),                     # earlier state
    ("the knight on f3", AFTER, "piece_at", {"square": "f3"}),                                # latest state
])
def test_a_catalogued_board_is_kept_exactly(quote, branch, predicate, args):
    f = [c for c in extracted(LINE, claim(quote, predicate, branch=branch, **args)) if "v2" in c.produced_by and c.fact.predicate == predicate and c.fact.args["square"] == args["square"]]
    assert f and list(f[0].prefix) == branch and f[0].branch_source == "catalog"


def test_a_branch_outside_the_catalogue_is_ambiguous_here():
    facts = run("1. e4 and after Nc6 the knight on c6 is out.", claim("the knight on c6", "piece_at", branch=["e2e4", "b8c6"], square="c6"))
    assert not [f for f in facts if f.status == "EXTRACTED" and "v2" in f.produced_by]
    assert [f for f in facts if f.status == "AMBIGUOUS" and "catalogue" in (f.reason or "")]


def test_an_illegal_invented_branch_is_ambiguous():
    facts = run("The pawn on e5 is central.", claim("The pawn on e5", "piece_at", branch=["e2e5"], square="e5"))
    assert not [f for f in facts if f.status == "EXTRACTED" and "v2" in f.produced_by] and [f for f in facts if "replay" in (f.reason or "")]


ALTERNATIVES = "I. 1. e4 e5 is one try.\n\nII. 1. d4 d5 is the other, and the d4 pawn stands on d4."


def test_alternatives_stay_separate():
    facts = run(ALTERNATIVES, claim("the d4 pawn stands on d4", "piece_at", branch=["e2e4", "d7d5"], square="d4"))
    assert not [f for f in facts if f.status == "EXTRACTED" and "v2" in f.produced_by] and [f for f in facts if "separate lines" in (f.reason or "")]


def test_no_unique_board_stays_unjudged():
    f = [c for c in extracted("The pawn on e4 is central.", claim("The pawn on e4", "piece_at", branch=None, square="e4")) if c.fact.predicate == "piece_at"][0]
    assert f.prefix is None and verdict(f) == "UNKNOWN"


def test_board_at_follows_the_moves_written_so_far():
    text = "The pawn on e2 waits. 1. e4 e5 2. Nf3 and the knight is out."
    v1 = x.extract(text, START, "t")
    assert h.board_at(text, START, v1, text.index("The pawn")) == []
    assert h.board_at(text, START, v1, text.index("and the knight")) == AFTER


# ── the merger ───────────────────────────────────────────────────────────────
def _claim_for(predicate, args, sources, prefix=("e2e4",), start=0, end=30, polarity="positive", change=False, status="EXTRACTED"):
    fact = Fact(predicate=predicate, args=args) if status == "EXTRACTED" else None
    return h.HybridFact(source_id="t", sentence="x", span_start=start, span_end=end, status=status, fact=fact, prefix=prefix, polarity=polarity, change_from=fact if change else None, produced_by=sources, candidate_ids=tuple(f"{s}:1" for s in sources))


MERGE_TEXT = "x" * 60


def test_a_v1_only_claim_survives():
    merged = h.merge(MERGE_TEXT, [_claim_for("is_check", {}, ("v1",))])
    assert merged[0].status == "EXTRACTED" and merged[0].produced_by == ("v1",)


def test_a_v2_only_claim_is_added():
    merged = h.merge(MERGE_TEXT, [_claim_for("is_check", {}, ("v1",)), _claim_for("is_open_file", {"file": "e"}, ("v2",), start=40, end=55)])
    assert {tuple(c.produced_by) for c in merged} == {("v1",), ("v2",)}


def test_an_exact_duplicate_becomes_one_with_both_recorded():
    merged = h.merge(MERGE_TEXT, [_claim_for("is_check", {}, ("v1",)), _claim_for("is_check", {}, ("v2",))])
    assert len(merged) == 1 and merged[0].produced_by == ("v1", "v2") and merged[0].candidate_ids == ("v1:1", "v2:1")


def test_duplicates_keep_every_stated_identity():
    a, b = _claim_for("attacks", {"source": "a1", "target": "a7"}, ("v1",)), _claim_for("attacks", {"source": "a1", "target": "a7", "piece": "rook"}, ("v2",))
    merged = h.merge(MERGE_TEXT, [a, b])
    assert len(merged) == 1 and merged[0].fact.args["piece"] == "rook"


def test_separate_claims_with_overlapping_spans_are_not_merged():
    merged = h.merge(MERGE_TEXT, [_claim_for("attacks", {"source": "a1", "target": "a7"}, ("v1",)), _claim_for("attacks", {"source": "a1", "target": "h1"}, ("v2",))])
    assert len(merged) == 2 and all(c.status == "EXTRACTED" for c in merged)


def test_the_same_claim_in_separate_places_is_not_merged():
    assert len(h.merge(MERGE_TEXT, [_claim_for("is_check", {}, ("v1",)), _claim_for("is_check", {}, ("v2",), start=35, end=50)])) == 2


def test_different_polarity_is_not_a_duplicate():
    assert len(h.merge(MERGE_TEXT, [_claim_for("is_check", {}, ("v1",)), _claim_for("is_check", {}, ("v2",), polarity="negative")])) == 2


def test_conflicting_referents_become_ambiguous_and_stay_auditable():
    merged = h.merge(MERGE_TEXT, [_claim_for("attacks", {"source": "e3", "target": "d4", "piece": "bishop"}, ("v1",)), _claim_for("attacks", {"source": "d2", "target": "d4", "piece": "knight"}, ("v2",))])
    assert len(merged) == 1 and merged[0].status == "AMBIGUOUS" and merged[0].conflict == "unresolved source" and merged[0].fact is None
    assert len(merged[0].competing) == 2 and "e3" in merged[0].competing[0] + merged[0].competing[1]
    assert q21.judge_hybrid(START, merged[0]) is None


def test_a_source_is_never_preferred_for_its_name():
    args1, args2 = {"source": "e3", "target": "d4"}, {"source": "d2", "target": "d4"}
    first = h.merge(MERGE_TEXT, [_claim_for("attacks", args1, ("v1",)), _claim_for("attacks", args2, ("v2",))])
    second = h.merge(MERGE_TEXT, [_claim_for("attacks", args1, ("v2",)), _claim_for("attacks", args2, ("v1",))])
    assert first[0].status == second[0].status == "AMBIGUOUS"


def test_squares_written_in_the_text_settle_a_conflict():
    text = "The bishop on e3 attacks d4 with force."
    merged = h.merge(text, [_claim_for("attacks", {"source": "e3", "target": "d4"}, ("v1",), end=len(text)), _claim_for("attacks", {"source": "d2", "target": "d4"}, ("v2",), end=len(text))])
    assert len(merged) == 1 and merged[0].status == "EXTRACTED" and merged[0].fact.args["source"] == "e3" and merged[0].conflict == "source settled by written squares"
    assert merged[0].competing


def test_two_sources_agreeing_settle_a_conflict_with_a_third():
    merged = h.merge(MERGE_TEXT, [_claim_for("attacks", {"source": "e3", "target": "d4"}, ("v1",)), _claim_for("attacks", {"source": "e3", "target": "d4"}, ("v2",)), _claim_for("attacks", {"source": "d2", "target": "d4"}, ("rules",))])
    assert [c.fact.args["source"] for c in merged if c.status == "EXTRACTED"] == ["e3"] and merged[0].conflict == "source settled by agreement"


def test_a_board_conflict_with_nothing_to_settle_it_is_ambiguous():
    merged = h.merge(MERGE_TEXT, [_claim_for("is_open_file", {"file": "e"}, ("v1",), prefix=("e2e4",)), _claim_for("is_open_file", {"file": "e"}, ("v2",), prefix=("d2d4",))])
    assert len(merged) == 1 and merged[0].status == "AMBIGUOUS" and merged[0].conflict == "unresolved board"


def test_the_moves_written_before_a_claim_settle_a_board_conflict():
    text = "1. e4 e5 and the e-file is not touched yet here."
    v1 = x.extract(text, START, "t")
    span = (text.index("the e-file"), text.index("the e-file") + 10)
    merged = h.merge(text, [_claim_for("is_open_file", {"file": "e"}, ("v1",), prefix=("e2e4",), start=span[0], end=span[1]), _claim_for("is_open_file", {"file": "e"}, ("v2",), prefix=("e2e4", "e7e5"), start=span[0], end=span[1])], START, v1)
    assert len(merged) == 1 and merged[0].prefix == ("e2e4", "e7e5") and merged[0].conflict == "board settled by the moves written before it"


def test_a_claim_with_no_board_gives_way_to_the_same_claim_on_a_board():
    merged = h.merge(MERGE_TEXT, [_claim_for("is_check", {}, ("v1",), prefix=None), _claim_for("is_check", {}, ("v2",), prefix=("e2e4",))])
    assert len(merged) == 1 and merged[0].prefix == ("e2e4",)


def test_an_invalid_v2_candidate_cannot_override_a_valid_v1_claim():
    valid = _claim_for("is_check", {}, ("v1",))
    for bad in (_claim_for("x", {}, ("v2",), status="PARSE_ERROR"), _claim_for("x", {}, ("v2",), status="AMBIGUOUS"), _claim_for("x", {}, ("v2",), status="UNSUPPORTED")):
        merged = h.merge(MERGE_TEXT, [valid, bad])
        assert [c for c in merged if c.status == "EXTRACTED"][0].produced_by == ("v1",)


def test_merging_cannot_change_a_verdict_by_provenance():
    text = "The king on e1 is safe."
    a = extracted(text, claim("The king on e1", "piece_at", square="e1"))[0]
    assert verdict(a) == verdict(a.model_copy(update={"produced_by": ("v1", "v2", "rules"), "conflict": "anything", "normalized": False})) == "VERIFIED"


# ── parents and children ─────────────────────────────────────────────────────
def test_child_facts_survive_an_unsupported_parent():
    text = "Black dominates the center with the queen on d8 and the pawn on e7."
    facts = run(text, claim(text, status="UNSUPPORTED"), claim("the queen on d8", "piece_at", square="d8", piece="queen"), claim("the pawn on e7", "piece_at", square="e7", piece="pawn"))
    assert sorted(f.fact.args["square"] for f in facts if f.status == "EXTRACTED") == ["d8", "e7"]
    assert [f for f in facts if f.status == "UNSUPPORTED"]


def test_no_claim_is_invented_from_unsupported_prose():
    text = "White has lasting pressure and the initiative, with a comfortable edge."
    assert not [f for f in run(text, claim(text, status="UNSUPPORTED")) if f.status == "EXTRACTED"]


def test_a_piece_at_whose_quote_never_names_the_square_is_refused():
    text = "1. e4 and Black has doubled pawns."
    facts = run(text, claim("Black has doubled pawns", "piece_at", branch=["e2e4"], square="c7", piece="pawn"))
    assert not [f for f in facts if f.status == "EXTRACTED" and f.fact.predicate == "piece_at"]


# ── provenance and the frozen boundaries ─────────────────────────────────────
def test_provenance_is_recorded_on_every_claim():
    text = "White is up a pawn and the king on e1 is safe."
    facts = run(text, claim("the king on e1", "piece_at", square="e1"))
    rule = next(f for f in facts if f.fact and f.fact.predicate == "material_balance_is")
    v2 = next(f for f in facts if f.fact and f.fact.predicate == "piece_at")
    assert rule.produced_by == ("rules",) and rule.normalized and v2.produced_by == ("v2",) and v2.alignment["method"] == "exact" and v2.branch_source == "catalog"


def test_variations_are_exactly_v1s():
    for case in qe.load_cases().values():
        hybrid = h.extract_hybrid(case["text"], case["fen"], ScriptedBackend({"claims": []}), case["id"])
        assert hybrid.variations == x.extract(case["text"], case["fen"], case["id"]).variations


def test_v1_facts_all_reach_the_merger_when_the_model_says_nothing():
    case = qe.load_cases()["05_pin"]
    v1 = x.extract(case["text"], case["fen"], "c")
    hybrid = h.extract_hybrid(case["text"], case["fen"], ScriptedBackend({"claims": []}), "c")
    wanted = {(c.fact.predicate, str(c.fact.args), c.prefix) for c in v1.facts if c.status == "EXTRACTED"}
    assert wanted <= {(c.fact.predicate, str(c.fact.args), c.prefix) for c in hybrid.facts if c.status == "EXTRACTED"}


def test_the_hybrid_never_calls_a_model_when_replaying(monkeypatch):
    import litellm

    def refuse(**_):
        raise AssertionError("a model was called")
    monkeypatch.setattr(litellm, "completion", refuse)
    assert q21.replay("dev")


def test_recorded_v2_results_are_untouched_by_the_hybrid_path():
    m = q2.evaluate_dev(q2.replay("dev", x.DEFAULT_MODEL))["extraction"]
    assert (m["exact_match"], m["board_match"]) == (61, 57)


def test_the_post_hoc_comparison_replays_from_recordings():
    labels = sc.load_jsonl(os.path.join(sc.HERE, "labels.jsonl"))
    predictions = q21.to_predictions(q21.replay("heldout"))
    m = q2.heldout_metrics(q2.score_heldout(predictions, labels))
    assert (m["exact"], m["board"], m["false_capture"], m["precision"]) == ((245, 468), (232, 245), (45, 92), (182, 244))
    assert (m["false_verified"], m["false_end_to_end_verified"], m["variations"], m["false_lines"]) == (0, 0, (143, 175), (18, 27))


def test_a_false_verdict_is_counted_as_unconfirmed_unless_a_false_label_agrees():
    def c(start):
        return {"status": "EXTRACTED", "span_start": start, "span_end": start + 5, "source_text": "x", "predicate": "is_check", "args": {}, "polarity": "positive", "board_moves": [], "verifier_status": "FALSE"}
    fact = {"label_id": "f", "case": "c", "kind": "fact", "extraction_class": "EXTRACTED", "span_start": 0, "span_end": 5, "source_text": "x", "predicate": "is_check", "args": {}, "branch": [], "chess_truth": "FALSE", "polarity": "positive"}
    other = {**fact, "label_id": "t", "span_start": 10, "span_end": 15, "chess_truth": "TRUE", "branch": ["e2e4"]}
    non = {"label_id": "n", "case": "c", "kind": "non_fact", "extraction_class": "UNSUPPORTED", "span_start": 20, "span_end": 25, "source_text": "x", "false_concrete": False}
    tally = q21.false_verdicts_unconfirmed({"c": {"facts": [c(0), c(10), c(20), c(30)], "variations": []}}, [fact, other, non])
    assert (tally["on_non_fact"], tally["true_fact_other_board"], tally["no_label"]) == (1, 1, 1)


def test_the_development_comparison_replays():
    table = q21.dev_table()
    assert table["V1"]["exact"] == (83, 106) and table["V2"]["exact"] == (61, 106)
    assert table["V2.1"]["false_acceptances"] == 0 and table["V2.1"]["exact"][0] >= 83
    assert table["V2.1"]["variations"] == table["V1"]["variations"]
