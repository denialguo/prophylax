"""Extractor V2.2 with scripted V2 answers, never a live call: material wording, counts and negation scope."""
import chess

from domain import extract_v2_2 as h
from domain.extract_v2 import ScriptedBackend
from evals import queen_extraction_v2_1 as q21

START = chess.STARTING_FEN
# White: Ra1 Bc1 Ke1 Nf3, pawns a2 e4. Black: Ke8 Re5 Bb7 Nd7, pawn e7. One rook and one bishop each side's reach is fixed below.
ONE_EACH = "4k3/1b1np3/8/4r3/4P3/5N2/P7/R1B1K3 w - - 0 1"


def claim(quote, predicate=None, status="EXTRACTED", branch=(), polarity="positive", **args):
    return {"source_text": quote, "status": status, "predicate": predicate, "args": [{"name": k, "value": str(v)} for k, v in args.items()], "polarity": polarity, "is_change": False,
            "branch": list(branch) if branch is not None else None, "stated_identities": [], "interpretations": [], "reason": None}


def run(text, *claims_, fen=START):
    return h.extract_hybrid(text, fen, ScriptedBackend({"claims": list(claims_)}), "t").facts


def read(text, *claims_, fen=START):
    """(predicate, args, polarity) of everything EXTRACTED."""
    return [(f.fact.predicate, f.fact.args, f.polarity) for f in run(text, *claims_, fen=fen) if f.status == "EXTRACTED"]


def materials(text, *claims_):
    return [r for r in read(text, *claims_) if r[0] == "material_balance_is"]


def test_nine_points_down_is_an_exact_balance():
    assert materials("White is nine points down.") == [("material_balance_is", {"points": -9}, "positive")]


def test_points_from_blacks_side_and_in_digits():
    assert materials("Black is 3 points up.") == [("material_balance_is", {"points": -3}, "positive")]
    assert materials("White is down by two points.") == [("material_balance_is", {"points": -2}, "positive")]


def test_a_point_surplus_with_a_side_is_exact():
    assert materials("White has a ten-point material surplus.") == [("material_balance_is", {"points": 10}, "positive")]
    assert materials("Black enjoys a four-point material deficit.") == [("material_balance_is", {"points": 4}, "positive")]


def test_a_point_surplus_with_no_side_is_ambiguous():
    facts = run("There is a ten-point material surplus.")
    assert not [f for f in facts if f.status == "EXTRACTED"] and any(f.status == "AMBIGUOUS" for f in facts)


def test_a_stated_balance_is_exact():
    assert materials("The material balance is +3.") == [("material_balance_is", {"points": 3}, "positive")]
    assert materials("The material balance is -1.") == [("material_balance_is", {"points": -1}, "positive")]


def test_queen_down_is_not_an_exact_balance():
    for text in ("White is a queen down.", "White stands a full queen down.", "White is down a full queen.", "Black is a queen ahead.", "This leaves White a queen down."):
        assert materials(text) == [], text
        assert any(f.status == "UNSUPPORTED" for f in run(text)), text


def test_relative_piece_wording_never_becomes_a_balance():
    for text in ("White is a rook ahead.", "Black is a knight up.", "White is down the exchange.", "White is a pawn up.", "White is up a rook and pawn."):
        assert materials(text) == [], text


def test_a_sideless_relative_phrase_is_not_a_balance():
    assert materials("That leaves a queen down.") == []


def test_a_model_cannot_turn_a_queen_down_into_a_balance():
    text = "White is a queen down."
    assert materials(text, claim("White is a queen down", "material_balance_is", points=-9)) == []
    assert any(f.status == "UNSUPPORTED" for f in run(text, claim("White is a queen down", "material_balance_is", points=-9)))


def test_a_model_may_read_a_written_figure():
    text = "So White is nine points down."
    assert materials(text, claim("White is nine points down", "material_balance_is", points=-9)) == [("material_balance_is", {"points": -9}, "positive")]


def test_a_model_figure_must_match_the_written_figure():
    text = "So White is nine points down."
    facts = run(text, claim("nine points down", "material_balance_is", points=-5))
    assert not any(f.fact and f.fact.args == {"points": -5} and f.status == "EXTRACTED" for f in facts)


def test_material_equality_stays_exact():
    assert materials("A capture, restoring material equality.") == [("material_balance_is", {"points": 0}, "positive")]
    assert materials("Material is level.") == [("material_balance_is", {"points": 0}, "positive")]


def test_has_no_queen_is_a_count_of_zero():
    assert ("piece_count_is", {"color": "white", "piece": "queen", "count": 0}, "positive") in read("White has no queen.")


def test_two_bishops_is_a_count_of_two():
    assert ("piece_count_is", {"color": "black", "piece": "bishop", "count": 2}, "positive") in read("Black has two bishops.")


def test_a_verifier_verdict_on_a_written_figure_still_comes_from_the_board():
    fact = next(f for f in run("White is nine points down.") if f.status == "EXTRACTED")
    assert q21.judge_hybrid(START, fact)["status"] == "FALSE"


# ── counts ───────────────────────────────────────────────────────────────────
def counts(text, *claims_):
    return [(r[1]["color"], r[1]["piece"], r[1]["count"]) for r in read(text, *claims_) if r[0] == "piece_count_is"]


def test_count_wording_families():
    for text, want in (("White has no rook.", ("white", "rook", 0)), ("Black has one knight.", ("black", "knight", 1)), ("White still has two rooks.", ("white", "rook", 2)), ("Black now has three pawns.", ("black", "pawn", 3)),
                       ("Black has only one rook left.", ("black", "rook", 1)), ("Only one white rook remains.", ("white", "rook", 1)), ("White retains both bishops.", ("white", "bishop", 2)), ("Black keeps both of its knights.", ("black", "knight", 2))):
        assert counts(text) == [want], text


def test_the_development_inventories_still_read():
    assert sorted(counts("White holds a decisive material advantage—queen and knight against a lone rook and two pawns—with more.")) == sorted(
        [("white", "queen", 1), ("white", "knight", 1), ("black", "rook", 1), ("black", "pawn", 2)])
    assert len(counts("After this, Black has no pieces and the position is simple.")) == 4


def test_a_sideless_remaining_piece_is_ambiguous():
    facts = run("Only one rook remains.")
    assert not [f for f in facts if f.status == "EXTRACTED" and f.fact.predicate == "piece_count_is"] and any(f.status == "AMBIGUOUS" for f in facts)


def test_has_a_piece_does_not_say_exactly_one():
    assert counts("White has a rook.") == []
    assert any(f.status == "AMBIGUOUS" for f in run("White has a rook."))
    assert counts("White has only a rook.") == [("white", "rook", 1)]
    assert sorted(counts("White has a rook and two pawns.")) == [("white", "pawn", 2), ("white", "rook", 1)]


def test_a_model_cannot_read_has_a_piece_as_exactly_one():
    assert counts("White has a rook.", claim("White has a rook", "piece_count_is", color="white", piece="rook", count=1)) == []


def test_a_piece_word_inside_another_noun_is_no_count():
    for text in ("White has two rook lifts.", "Black has no queen sacrifice here.", "White has no pawn breaks.", "Black has two pawns on the kingside."):
        assert counts(text) == [], text


def test_a_loss_is_not_a_count():
    assert counts("White lost a rook.") == []
    assert counts("White lost a rook.", claim("lost a rook", "piece_count_is", color="white", piece="rook", count=0)) == []


def test_the_bishop_pair_has_no_rule_without_development_support():
    assert counts("Black retains the bishop pair.") == []


# ── negation ─────────────────────────────────────────────────────────────────
ROOK_BLOCKED = "6k1/4p3/8/8/1B6/8/4P3/4R1K1 w - - 0 1"  # Re1 is behind its own e2 pawn, Bb4 sees e7
ROOK = {"source": "e1", "target": "e7", "piece": "rook"}
BISHOP = {"source": "b4", "target": "e7", "piece": "bishop"}


def relations(text, *claims_, fen=ROOK_BLOCKED):
    return [(r[1], r[2]) for r in read(text, *claims_, fen=fen) if r[0] in ("attacks", "defends")]


def test_negation_stops_at_its_own_clause():
    assert sorted(relations("The rook does not attack e7 but the bishop does."), key=str) == sorted([(ROOK, "negative"), (BISHOP, "positive")], key=str)


def test_negation_can_sit_in_the_second_clause():
    assert sorted(relations("The bishop attacks e7 but the rook does not."), key=str) == sorted([(ROOK, "negative"), (BISHOP, "positive")], key=str)


def test_both_clauses_get_the_right_verdict():
    facts = [f for f in run("The rook does not attack e7 but the bishop does.", fen=ROOK_BLOCKED) if f.status == "EXTRACTED"]
    assert [q21.judge_hybrid(ROOK_BLOCKED, f)["status"] for f in facts] == ["VERIFIED", "VERIFIED"]


def test_but_not_denies_only_the_second_target():
    got = relations("The bishop on b4 attacks e7 but not f8.")
    assert ({"source": "b4", "target": "e7", "piece": "bishop"}, "positive") in got and ({"source": "b4", "target": "f8", "piece": "bishop"}, "negative") in got


def test_a_negative_verb_forms():
    for text in ("The rook on e1 does not attack e7.", "The rook on e1 doesn't attack e7.", "The rook on e1 isn't attacking e7.", "The rook on e1 is not attacking the pawn on e7.", "The e1 rook does not attack the e7 pawn."):
        assert (ROOK, "negative") in relations(text), text


def test_a_passive_denial_is_not_read_backwards():
    assert relations("The rook on e1 is not attacked by e7.") == []


def test_no_longer_requires_the_earlier_state():
    facts = [f for f in run("The rook on e1 no longer attacks e7.", fen=ROOK_BLOCKED) if f.status == "EXTRACTED" and f.polarity == "negative"]
    assert facts and facts[0].held_before is not None


def test_doesnt_defend_is_a_negative_defends():
    assert relations("The rook on e1 doesn't defend e7.")[0][1] == "negative"
    assert read("The rook on e1 doesn't defend e7.", fen=ROOK_BLOCKED)[0][0] == "defends"


def test_a_piece_that_is_not_unique_is_ambiguous():
    two_rooks = "6k1/4p3/8/8/8/8/4P3/R3R1K1 w - - 0 1"
    facts = run("The rook does not attack e7 but the king does.", fen=two_rooks)
    assert any(f.status == "AMBIGUOUS" for f in facts) and not any(f.status == "EXTRACTED" and f.fact.args.get("piece") == "rook" for f in facts)


def test_not_passed_forms():
    fen = "6k1/8/8/3p4/4P3/8/8/6K1 w - - 0 1"
    for text in ("The d5 pawn is not passed.", "The pawn on d5 isn't a passed pawn.", "The d5-pawn is no longer passed.", "The pawn on d5 is not a passer."):
        assert ("is_passed_pawn", {"square": "d5"}, "negative") in read(text, fen=fen), text


def test_file_denials():
    assert ("is_open_file", {"file": "e"}, "negative") in read("The e-file isn't open.")
    assert ("is_open_file", {"file": "e"}, "negative") in read("The e-file is not open.")
    assert ("is_open_file", {"file": "e"}, "negative") in read("the e-file is no longer open.")


def test_without_a_queen_is_a_count_when_a_side_is_named():
    assert counts("White is without a queen.") == [("white", "queen", 0)]
    assert counts("Black, with no queen, must defend.") == [("black", "queen", 0)]


def test_without_and_no_do_not_reach_other_nouns():
    assert counts("White wins without a queen sacrifice.") == []
    assert counts("White plays on without the queen.") == []
    assert any(f.status == "AMBIGUOUS" for f in run("The game goes on without a queen."))


def test_a_negation_earlier_in_the_clause_blocks_a_positive_rule():
    assert (ROOK, "positive") not in relations("It is not true that the rook on e1 attacks e7.")


def test_a_negation_in_another_clause_does_not_block_a_positive_rule():
    assert (BISHOP, "positive") in relations("The plan does not work, and the bishop on b4 attacks e7.")


def test_a_model_positive_in_the_clean_clause_is_kept():
    text = "The rook does not attack e7 but the bishop does."
    got = relations(text, claim(text[:-1], "attacks", source="b4", target="e7", piece="bishop"))
    assert (BISHOP, "positive") in got


def test_a_model_cannot_carry_a_negation_into_the_other_clause():
    text = "The rook on e1 does not attack e7 but the bishop on b4 attacks e7."
    facts = run(text, claim(text[:-1], "attacks", polarity="negative", source="b4", target="e7", piece="bishop"), fen=ROOK_BLOCKED)
    assert not any(f.status == "EXTRACTED" and f.polarity == "negative" and f.fact.args.get("source") == "b4" for f in facts)


def test_a_model_positive_in_the_negated_clause_is_refused():
    text = "The rook on e1 does not attack e7 but the bishop on b4 attacks e7."
    facts = run(text, claim(text[:-1], "attacks", source="e1", target="e7", piece="rook"), fen=ROOK_BLOCKED)
    assert not any(f.status == "EXTRACTED" and f.polarity == "positive" and f.fact.args.get("source") == "e1" for f in facts)


def test_a_negation_does_not_reach_into_a_parenthetical():
    text = "The king cannot move to f2 (attacked by the queen)."
    assert h._negated_before(text, text.index("attacked")) is False
    assert h._negated_before(text, text.index("to f2")) is True


# ── what must not have moved, and the development measures ───────────────────
def test_the_merger_is_v2_1s():
    from domain import extract_v2_1
    assert h.merge is extract_v2_1.merge


def test_no_command_reaches_the_held_out_set_with_a_model():
    import inspect

    from evals import queen_extraction_v2_2 as q
    source = inspect.getsource(q.main)
    assert 'q2.record("dev"' in source and "--set" not in source


def test_flash_lite_replays_to_its_recorded_development_numbers():
    from domain.extract_v2 import DEFAULT_MODEL
    from evals import queen_extraction as qe
    from evals import queen_extraction_v2 as q2
    from evals import queen_extraction_v2_2 as q
    m = q.v2_metrics(q.replay_v2(DEFAULT_MODEL, q2.OUT), qe.load_gold(), qe.load_cases())
    assert m["exact"] == [61, 106] and m["board"] == [57, 61] and m["false_acceptances"] == 0
    assert sum(v[0] for v in m["by_predicate"].values()) == 61 and sum(v[1] for v in m["by_predicate"].values()) == 106


def test_the_count_taxonomy_comes_from_the_labels():
    from evals import queen_extraction as qe
    from evals import queen_extraction_v2_2 as q
    t = q.count_taxonomy(qe.load_gold())
    assert t["labels"] == 11 and t["sentences"] == 3 and t["families"] == {"inventory: X and Y against Z": 7, "has no pieces": 4}


def test_the_v2_1_false_alarm_analysis_reproduces_the_31():
    import os

    from evals import queen_extraction_v2 as q2
    from evals import queen_extraction_v2_2 as q
    from evals.queen_heldout import score as sc
    rows = q.false_alarm_rows(q21.to_predictions(q21.replay("heldout")), sc.load_jsonl(os.path.join(q2.HELDOUT, "labels.jsonl")))
    assert len(rows) == 31 and sum(r["family"] == "material" and r["from_rule"] for r in rows) == 5
