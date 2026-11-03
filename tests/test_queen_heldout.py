"""Held-out experiment infrastructure. Nothing here calls Hugging Face, QUEEN or any other
remote service: the generator is tested with a fake, and urlopen is blocked throughout."""
import hashlib
import json
import os
import stat
import urllib.request

import chess
import pytest

from evals.queen_heldout import freeze_predictions as fp
from evals.queen_heldout import generate_report as report
from evals.queen_heldout import run_queen as rq
from evals.queen_heldout import score as sc

HERE = fp.HERE
SETTINGS = json.load(open(os.path.join(HERE, "generation_settings.json"), encoding="utf-8"))
SUITE = json.load(open(os.path.join(HERE, "suite.json"), encoding="utf-8"))
REVISIONS = (SETTINGS["expected_space_sha"], SETTINGS["expected_model_sha"])
KINGS = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to use the network")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


def fen_key(fen):
    return fen.rsplit(" ", 2)[0]


# ── the suite ────────────────────────────────────────────────────────────────
def test_suite_composition_and_fields():
    cases = SUITE["cases"]
    counts = {}
    for c in cases:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
        assert c["id"] and c["provenance"] and c["reason"] and chess.Board(c["fen"]).is_valid()
    assert counts == {"tactical": 8, "quiet": 8, "structure": 4, "endgame": 4}
    assert len({c["id"] for c in cases}) == 24
    assert sum("player history" in c["provenance"] for c in cases) == 12


def test_suite_has_no_duplicates_within_itself_or_with_the_development_set():
    with open(os.path.join(os.path.dirname(HERE), "queen_benchmark", "cases.json"), encoding="utf-8") as f:
        dev = {fen_key(c["fen"]) for c in json.load(f)["cases"]}
    keys = [fen_key(c["fen"]) for c in SUITE["cases"]]
    assert len(set(keys)) == 24 and not set(keys) & dev


def test_positions_with_outputs_survive_the_suite_change():
    with open(os.path.join(HERE, "suite_v1.json"), encoding="utf-8") as f:
        v1 = {c["id"]: c["fen"] for c in json.load(f)["cases"]}
    v2 = {c["id"]: c["fen"] for c in SUITE["cases"]}
    for case in os.listdir(os.path.join(HERE, "raw")):
        assert v1[case] == v2[case]
        with open(os.path.join(HERE, "raw", case, "details.json"), encoding="utf-8") as f:
            assert json.load(f)["fen"] == v2[case]


def test_stored_outputs_match_their_logged_hashes():
    logged = fp.logged_hashes(os.path.join(HERE, "generation_log.jsonl"))
    for case in os.listdir(os.path.join(HERE, "raw")):
        assert fp.sha256_file(os.path.join(HERE, "raw", case, "explanation.txt")) == logged[case]["explanation_sha256"]


def test_settings_are_those_of_the_development_outputs():
    with open(os.path.join(os.path.dirname(HERE), "queen_benchmark", "cases.json"), encoding="utf-8") as f:
        dev = json.load(f)["cases"][0]["inference_settings"]
    assert (SETTINGS["max_tokens"], SETTINGS["temperature"]) == (dev["max_tokens"], dev["temperature"])
    assert SETTINGS["seed"] == dev["seed"] and SETTINGS["model"] == "princeton-nlp/queen_hce-4"


# ── the generator ────────────────────────────────────────────────────────────
CASES = [{"id": "a", "fen": KINGS, "category": "endgame"}, {"id": "b", "fen": "4k3/8/8/8/8/8/P7/4K3 w - - 0 1", "category": "endgame"}]


def complete(fen, text="ANALYSIS:\nThe c-file is open.\n"):
    return "complete", json.dumps(["svg", "move", text, {"fen": fen, "best_move_uci": "e1e2"}], ensure_ascii=False)


def gen_fake(results):
    calls = []

    def generate(fen, settings):
        calls.append(fen)
        item = results[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item(fen) if callable(item) else item
    generate.calls = calls
    return generate


def test_generator_saves_exactly_what_arrived(tmp_path):
    text = "ANALYSIS:\nÉtrange — text,\r\nkept exactly.\n"
    assert rq.run(CASES[:1], SETTINGS, str(tmp_path), gen_fake([lambda f: complete(f, text)]), REVISIONS) == rq.EXIT_OK
    with open(tmp_path / "raw" / "a" / "explanation.txt", "rb") as f:
        assert f.read() == text.encode("utf-8")
    assert json.loads((tmp_path / "raw" / "a" / "response.json").read_text(encoding="utf-8"))[2] == text
    entry = [json.loads(line) for line in (tmp_path / "generation_log.jsonl").read_text().splitlines()][-1]
    assert entry["explanation_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert entry["response_sha256"] == fp.sha256_file(tmp_path / "raw" / "a" / "response.json")


def test_generator_resumes_and_skips_completed_cases(tmp_path):
    first = gen_fake([complete])
    assert rq.run(CASES[:1], SETTINGS, str(tmp_path), first, REVISIONS) == rq.EXIT_OK
    second = gen_fake([complete])
    assert rq.run(CASES, SETTINGS, str(tmp_path), second, REVISIONS) == rq.EXIT_OK
    assert second.calls == [CASES[1]["fen"]]
    third = gen_fake([])
    assert rq.run(CASES, SETTINGS, str(tmp_path), third, REVISIONS) == rq.EXIT_OK and third.calls == []


def test_generator_stops_cleanly_on_quota_and_keeps_the_failure(tmp_path):
    body = '{"error": "You have exceeded your ZeroGPU runs limit."}'
    generate = gen_fake([complete, ("error", body), complete])
    cases = CASES + [{"id": "c", "fen": KINGS, "category": "endgame"}]
    assert rq.run(cases, SETTINGS, str(tmp_path), generate, REVISIONS) == rq.EXIT_QUOTA
    assert len(generate.calls) == 2 and not (tmp_path / "raw" / "b").exists()
    saved = list((tmp_path / "attempts" / "b").iterdir())
    assert len(saved) == 1 and json.loads(saved[0].read_text())["body"] == body


@pytest.mark.parametrize("failure,code", [(ConnectionError("down"), rq.EXIT_ERROR), (("no_result", ""), rq.EXIT_ERROR),
                                          (("error", '{"error": "boom"}'), rq.EXIT_ERROR)])
def test_generator_stops_on_any_other_failure_and_keeps_it(tmp_path, failure, code):
    assert rq.run(CASES[:1], SETTINGS, str(tmp_path), gen_fake([failure]), REVISIONS) == code
    assert not (tmp_path / "raw" / "a").exists() and list((tmp_path / "attempts" / "a").iterdir())


def test_generator_refuses_an_answer_about_another_position(tmp_path):
    wrong = lambda f: complete("8/8/8/8/8/8/8/K1k5 w - - 0 1")
    assert rq.run(CASES[:1], SETTINGS, str(tmp_path), gen_fake([wrong]), REVISIONS) == rq.EXIT_ERROR
    assert not (tmp_path / "raw" / "a").exists()


def test_a_completed_response_is_only_replaced_on_request_and_is_kept(tmp_path):
    rq.run(CASES[:1], SETTINGS, str(tmp_path), gen_fake([lambda f: complete(f, "first")]), REVISIONS)
    rq.run(CASES[:1], SETTINGS, str(tmp_path), gen_fake([]), REVISIONS)
    assert (tmp_path / "raw" / "a" / "explanation.txt").read_text() == "first"
    (tmp_path / "attempts" / "a").mkdir(parents=True)
    rq.run(CASES[:1], SETTINGS, str(tmp_path), gen_fake([lambda f: complete(f, "second")]), REVISIONS, overwrite_completed=True)
    assert (tmp_path / "raw" / "a" / "explanation.txt").read_text() == "second"
    old = [p for p in (tmp_path / "attempts" / "a").iterdir() if p.name.startswith("superseded-")]
    assert (old[0] / "explanation.txt").read_text() == "first"


def test_generator_stops_when_the_model_or_space_changed(tmp_path):
    generate = gen_fake([complete])
    assert rq.run(CASES[:1], SETTINGS, str(tmp_path), generate, ("other", "other")) == rq.EXIT_REVISION and generate.calls == []
    assert rq.run(CASES[:1], SETTINGS, str(tmp_path), generate, ("other", "other"), allow_revision_change=True) == rq.EXIT_OK


def test_request_uses_the_settings_and_the_token_is_never_saved(tmp_path, monkeypatch):
    secret = "hf_not_a_real_token"
    monkeypatch.setenv("HF_TOKEN", secret)
    seen = []

    class Reply:
        def __init__(self, lines): self.lines = lines
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self): return iter(self.lines)
        def read(self): return b'{"event_id": "e1"}'

    def fake_urlopen(request, timeout=None):
        seen.append(request)
        if request.data:
            return Reply([])
        return Reply([b"event: error\n", b'data: {"error": "ZeroGPU quota exceeded"}\n'])
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert rq.run(CASES[:1], SETTINGS, str(tmp_path), rq.space_generate, REVISIONS) == rq.EXIT_QUOTA
    assert json.loads(seen[0].data)["data"] == [KINGS, "", 2048, 0.6, 20260823]
    assert all(r.get_header("Authorization") == f"Bearer {secret}" for r in seen)
    for base, _, names in os.walk(tmp_path):
        for name in names:
            assert secret not in open(os.path.join(base, name), encoding="utf-8").read()


def test_quota_recognition_covers_both_messages_seen():
    assert rq.is_quota_error("You have exceeded your ZeroGPU runs limit. Authenticate with a Hugging Face token for more quota")
    assert rq.is_quota_error("Subscribe to Hugging Face PRO to get 40 min of ZeroGPU quota a day")
    assert not rq.is_quota_error("something else broke")


# ── the frozen code ──────────────────────────────────────────────────────────
def test_semantic_code_is_still_the_frozen_commit():
    """Delete this only after the held-out evaluation is reported."""
    try:
        drift = fp.semantic_changes()
    except Exception as e:
        pytest.skip(f"git history unavailable: {e}")
    assert drift == [], f"{drift} changed; the held-out evaluation must run on {fp.FROZEN_COMMIT}"


def test_semantic_check_notices_any_difference():
    assert fp.semantic_changes(lambda p: "x", lambda p: "x") == []
    assert fp.semantic_changes(lambda p: "x", lambda p: "y") == list(fp.SEMANTIC_FILES)


# ── the prediction freeze ────────────────────────────────────────────────────
TEXT = ("ANALYSIS:\nThe white pawn on e2 attacks the black pawn on d3. White has excellent practical chances.\n\n"
        "I. white pawn e2-e4 — immediately creating a passed pawn. The black king sits on e7. The white pawn on e4 attacks f5.\n\n"
        "II. white king e1-e3 — a blunder.\n\n"
        "BEST_MOVE: white pawn e2-e4\nCRITICAL_LINE: white pawn e2-e4 black king e8-e7\n"
        "PROMISING_MOVES: white pawn e2-e4\nEVALUATION: the player has a slight advantage of 0.2 pawns.\n")
FEN = "4k3/8/8/8/8/3p4/4P3/4K3 w - - 0 1"
CASE = {"id": "x1", "category": "endgame", "fen": FEN, "provenance": "test"}


def make_raw(base, case=CASE, text=TEXT):
    raw = base / "raw" / case["id"]
    raw.mkdir(parents=True)
    (raw / "explanation.txt").write_bytes(text.encode("utf-8"))
    (raw / "details.json").write_text(json.dumps({"fen": case["fen"]}))
    with open(base / "generation_log.jsonl", "a") as f:
        f.write(json.dumps({"event": "start", "space_sha": "S", "model_sha": "M"}) + "\n")
        f.write(json.dumps({"event": "complete", "case": case["id"], "explanation_sha256": hashlib.sha256(text.encode()).hexdigest(),
                            "response_sha256": "R"}) + "\n")


def do_freeze(base, cases=(CASE,), **kw):
    kw.setdefault("changes", lambda: [])
    return fp.freeze(list(cases), str(base / "raw"), str(base / "predictions"), str(base / "labels.jsonl"),
                     str(base / "generation_log.jsonl"), SETTINGS, **kw)


@pytest.fixture
def frozen(tmp_path):
    make_raw(tmp_path)
    do_freeze(tmp_path)
    return tmp_path


def test_freeze_writes_everything_needed_to_audit(frozen):
    data = json.loads((frozen / "predictions" / "x1.json").read_text())
    assert data["fen"] == FEN and data["raw_explanation_sha256"] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert data["extractor_commit"] and data["verifier_commit"]
    fact = next(f for f in data["facts"] if f["status"] == "EXTRACTED" and f["args"].get("target") == "f5")
    assert fact["predicate"] == "attacks" and fact["board_moves"] == ["e2e4"]
    assert fact["board_fen"] == "4k3/8/8/8/4P3/3p4/8/4K3 b - - 0 1" and fact["verifier_status"] == "VERIFIED"
    assert fact["verifier_evidence"] and TEXT[fact["span_start"]:fact["span_end"]] == fact["source_text"]
    assert {f["status"] for f in data["facts"]} >= {"EXTRACTED", "UNSUPPORTED"}
    critical = next(v for v in data["variations"] if v["kind"] == "critical_line")
    assert critical["moves"] == ["e2e4", "e8e7"] and critical["verifier_status"] == "VERIFIED" and critical["verifier_result"]["plies"]


def test_freeze_manifest_records_commits_and_hashes(frozen):
    m = json.loads((frozen / "predictions" / "MANIFEST.json").read_text())
    assert m["frozen_commit"] == "046bb19" and m["extractor_commit"] and m["verifier_commit"] and m["python_chess"] == chess.__version__
    assert m["model"] == SETTINGS["model"] and m["generation_settings"] == SETTINGS and m["complete_suite"] is True
    row = m["cases"][0]
    assert row["fen"] == FEN and (row["space_sha"], row["model_sha"], row["raw_response_sha256"]) == ("S", "M", "R")
    assert row["prediction_sha256"] == fp.sha256_file(frozen / "predictions" / "x1.json")


def test_frozen_files_are_read_only(frozen):
    for name in ("x1.json", "MANIFEST.json"):
        assert not os.stat(frozen / "predictions" / name).st_mode & stat.S_IWUSR


def test_predictions_are_reproducible(tmp_path):
    for sub in ("a", "b"):
        make_raw(tmp_path / sub)
        do_freeze(tmp_path / sub)
    assert (tmp_path / "a" / "predictions" / "x1.json").read_bytes() == (tmp_path / "b" / "predictions" / "x1.json").read_bytes()


def test_freeze_refuses_a_second_run(frozen):
    before = (frozen / "predictions" / "MANIFEST.json").read_bytes()
    with pytest.raises(fp.Refused, match="already frozen"):
        do_freeze(frozen)
    with pytest.raises(fp.Refused, match="already frozen"):
        do_freeze(frozen, override_reason="anything", allow_partial=True)
    assert (frozen / "predictions" / "MANIFEST.json").read_bytes() == before


def test_freeze_refuses_once_labels_exist(tmp_path):
    make_raw(tmp_path)
    (tmp_path / "labels.jsonl").write_text('{"label_id": "1"}\n')
    with pytest.raises(fp.Refused, match="not be blind"):
        do_freeze(tmp_path)
    assert not (tmp_path / "predictions" / "MANIFEST.json").exists()


def test_an_empty_label_file_does_not_block_the_freeze(tmp_path):
    make_raw(tmp_path)
    (tmp_path / "labels.jsonl").write_text("\n")
    assert do_freeze(tmp_path)["cases"]


def test_freeze_refuses_changed_semantic_code_unless_overridden_and_records_it(tmp_path):
    make_raw(tmp_path)
    with pytest.raises(fp.Refused, match="semantic code"):
        do_freeze(tmp_path, changes=lambda: ["domain/extract.py"])
    m = do_freeze(tmp_path, changes=lambda: ["domain/extract.py"], override_reason="testing the flag")
    assert m["semantic_override"] == "testing the flag" and m["semantic_drift"] == ["domain/extract.py"]


def test_freeze_refuses_another_chess_library_version(tmp_path):
    make_raw(tmp_path)
    with pytest.raises(fp.Refused, match="python-chess"):
        do_freeze(tmp_path, chess_version="0.0.1")


def test_freeze_refuses_a_missing_response_unless_partial_is_recorded(tmp_path):
    make_raw(tmp_path)
    other = {**CASE, "id": "x2"}
    with pytest.raises(fp.Refused, match="missing"):
        do_freeze(tmp_path, cases=(CASE, other))
    m = do_freeze(tmp_path, cases=(CASE, other), allow_partial=True)
    assert m["complete_suite"] is False and m["missing_cases"] == ["x2"]


def test_freeze_refuses_altered_raw_text(tmp_path):
    make_raw(tmp_path)
    (tmp_path / "raw" / "x1" / "explanation.txt").write_text(TEXT + "edited")
    with pytest.raises(fp.Refused, match="does not match"):
        do_freeze(tmp_path)


def test_freeze_refuses_a_response_about_another_position(tmp_path):
    make_raw(tmp_path)
    (tmp_path / "raw" / "x1" / "details.json").write_text(json.dumps({"fen": KINGS}))
    with pytest.raises(fp.Refused, match="different position"):
        do_freeze(tmp_path)


# ── labels ───────────────────────────────────────────────────────────────────
SCHEMA = json.load(open(os.path.join(HERE, "labels.schema.json"), encoding="utf-8"))


def span(quote, text=TEXT):
    start = text.index(quote)
    return {"span_start": start, "span_end": start + len(quote), "source_text": quote}


def fact(lid, quote, predicate, args, branch, truth, **kw):
    return {"label_id": lid, "case": "x1", "kind": "fact", "extraction_class": "EXTRACTED", **span(quote), "predicate": predicate,
            "args": args, "branch": branch, "chess_truth": truth, **kw}


def non_fact(lid, quote, cls, false_concrete=False, **kw):
    return {"label_id": lid, "case": "x1", "kind": "non_fact", "extraction_class": cls, **span(quote), "false_concrete": false_concrete, **kw}


def line(lid, quote, moves, legal=True, holds=True, failure=None, cls="EXTRACTED"):
    return {"label_id": lid, "case": "x1", "kind": "variation", "extraction_class": cls, **span(quote), "moves": moves, "legal": legal,
            "annotations_hold": holds, "failure_ply": failure}


GOLD = [
    fact("f1", "The white pawn on e2 attacks the black pawn on d3", "attacks", {"source": "e2", "target": "d3"}, [], "TRUE"),
    fact("f2", "immediately creating a passed pawn", "is_passed_pawn", {"square": "e4"}, ["e2e4"], "TRUE"),  # past the d3 pawn
    fact("f3", "The black king sits on e7", "piece_at", {"square": "e7", "color": "black", "piece": "king"}, ["e2e4"], "FALSE"),
    fact("f4", "The white pawn on e4 attacks f5", "attacks", {"source": "e4", "target": "f5", "piece": "pawn"}, ["e2e4"], "TRUE"),
    non_fact("n1", "White has excellent practical chances", "UNSUPPORTED"),
    non_fact("n2", "a blunder", "UNSUPPORTED", false_concrete=True, known_issue="other"),
    line("v1", "CRITICAL_LINE: white pawn e2-e4 black king e8-e7", ["e2e4", "e8e7"]),
    line("v2", "II. white king e1-e3", ["e1e3"], legal=False, failure=0),
]


def texts_and_fens():
    return {"x1": TEXT}, {"x1": FEN}


def test_the_schema_requires_the_fields_of_each_kind():
    assert sc.validate_labels(GOLD, *texts_and_fens(), SCHEMA) == []
    broken = {k: v for k, v in GOLD[0].items() if k != "chess_truth"}
    assert any("schema" in p for p in sc.validate_labels([broken], *texts_and_fens(), SCHEMA))
    no_flag = {k: v for k, v in GOLD[4].items() if k != "false_concrete"}
    assert sc.validate_labels([no_flag], *texts_and_fens(), SCHEMA)
    no_ply = {k: v for k, v in GOLD[6].items() if k != "failure_ply"}
    assert sc.validate_labels([no_ply], *texts_and_fens(), SCHEMA)


@pytest.mark.parametrize("change,expect", [
    ({"source_text": "not the text"}, "not the text at that span"),
    ({"predicate": "traps_piece"}, "not a predicate"),
    ({"branch": ["e2e5"]}, "does not replay"),
    ({"branch": None}, "UNDETERMINED"),
    ({"chess_truth": "UNDETERMINED"}, "UNDETERMINED"),
    ({"case": "nope"}, "unknown case"),
    ({"board_fen": KINGS}, "board_fen"),
])
def test_label_problems_are_named(change, expect):
    assert any(expect in p for p in sc.validate_labels([{**GOLD[0], **change}], *texts_and_fens(), SCHEMA))


def test_duplicate_label_ids_and_inconsistent_failure_ply_are_named():
    assert any("duplicate" in p for p in sc.validate_labels([GOLD[0], GOLD[0]], *texts_and_fens(), SCHEMA))
    assert any("failure_ply" in p for p in sc.validate_labels([{**GOLD[7], "failure_ply": None}], *texts_and_fens(), SCHEMA))


# ── scoring ──────────────────────────────────────────────────────────────────
def predictions():
    return {"x1": fp.predict_case(CASE, TEXT, "h")}


def scored(labels=GOLD):
    p = predictions()
    result = sc.score(p, labels)
    sc.add_unmatched_variations(result, p, labels)
    return result


def outcome_total(e):
    return sum(e.get(k, 0) for k in ("false_caught", "false_unknown", "false_missed", "false_incorrectly_verified",
                                     "false_found_on_wrong_board", "false_caught_by_stray_candidate"))


def test_extraction_metrics():
    e = scored()["extraction"]
    assert (e["facts_labelled"], e["predicate_match"], e["predicate_and_args_match"], e["board_match"]) == (4, 4, 4, 4)
    assert e["predicate_accuracy"] == e["predicate_and_args_accuracy"] == e["board_binding_accuracy"] == 1.0
    assert e["unsupported_detected"] == e["unsupported_labelled"] == 2 and e["unsupported_recall"] == 1.0
    assert e["missed"] == 0 and e["hallucinated_on_non_fact"] == 0


def test_verification_metrics_on_correct_reads():
    v = scored()["verification"]
    assert (v["correctly_extracted"], v["VERIFIED"], v["FALSE"]) == (4, 3, 1)
    assert v["verdict_accuracy"] == 1.0 and v["false_verified"] == 0 and v["false_verification_acceptance_rate"] == 0.0


def test_end_to_end_capture_counts_a_claim_no_predicate_can_represent_against_the_system():
    e = scored()["end_to_end_facts"]
    assert e["false_claims"] == 2 and e["false_caught"] == 1 and e["false_missed"] == 1
    assert e["false_outside_predicates"] == 1
    assert e["false_claim_capture_rate"] == 0.5 and e["false_claim_capture_rate_representable"] == 1.0
    assert outcome_total(e) == e["false_claims"]


def test_a_false_verified_is_counted_everywhere_it_matters():
    wrong = [fact("f1", "The white pawn on e2 attacks the black pawn on d3", "attacks", {"source": "e2", "target": "d3"}, [], "FALSE")]
    r = scored(wrong)
    assert r["verification"]["false_verified"] == 1 and r["verification"]["false_verification_acceptance_rate"] == 1.0
    assert r["end_to_end_facts"]["false_incorrectly_verified"] == 1 and r["verified_precision"]["facts_false"] == 1
    assert r["details"]["FALSE_VERIFIED"] and outcome_total(r["end_to_end_facts"]) == 1


def test_a_missed_fact_is_a_miss_and_not_a_verifier_failure():
    labels = [fact("f9", "White has excellent practical chances", "is_open_file", {"file": "a"}, [], "FALSE")]
    r = scored(labels)
    assert r["extraction"]["missed"] == 1 and r["failure_taxonomy"]["extractable_claim_missed"] == 1
    assert r["end_to_end_facts"]["false_missed"] == 1 and r["verification"].get("correctly_extracted", 0) == 0


def test_a_wrong_argument_and_a_wrong_board_are_told_apart():
    wrong_args = [fact("f1", "The white pawn on e4 attacks f5", "attacks", {"source": "e4", "target": "g5"}, ["e2e4"], "FALSE")]
    r = scored(wrong_args)
    assert r["extraction"]["predicate_match"] == 1 and r["extraction"]["predicate_and_args_match"] == 0
    assert r["failure_taxonomy"]["predicate_right_args_wrong"] == 1
    wrong_board = [fact("f4", "The white pawn on e4 attacks f5", "attacks", {"source": "e4", "target": "f5"}, [], "FALSE")]
    r = scored(wrong_board)
    assert r["extraction"]["predicate_and_args_match"] == 1 and r["extraction"]["board_match"] == 0
    assert r["failure_taxonomy"]["board_binding_wrong"] == 1 and r["verification"].get("correctly_extracted", 0) == 0
    assert r["end_to_end_facts"]["false_incorrectly_verified"] == 1  # verified on the wrong board is still a false VERIFIED
    assert r["verified_precision"]["facts_false"] == 1


def test_a_stray_extraction_on_a_non_fact_is_a_hallucination():
    labels = [non_fact("n9", "The white pawn on e4 attacks f5", "UNSUPPORTED", false_concrete=True)]
    r = scored(labels)
    assert r["extraction"]["hallucinated_on_non_fact"] == 1 and r["failure_taxonomy"]["hallucinated_on_non_fact"] == 1
    assert r["verified_precision"]["facts_false"] == 1 and outcome_total(r["end_to_end_facts"]) == 1


def test_an_extraction_no_label_covers_is_reported_and_counts_against_precision():
    r = scored([GOLD[0]])
    assert r["extraction"]["extraction_with_no_label"] == 3
    assert r["verified_precision"]["facts_true"] == 1 and r["verified_precision"]["facts_unlabelled"] == 2
    assert r["verified_precision"]["fact_precision"] == round(1 / 3, 4)


def test_optional_identity_arguments_only_count_when_both_state_them():
    assert sc.args_match({"source": "e4", "target": "f5"}, {"source": "e4", "target": "f5", "piece": "pawn"})
    assert not sc.args_match({"source": "e4", "target": "f5", "piece": "knight"}, {"source": "e4", "target": "f5", "piece": "pawn"})
    assert not sc.args_match({"source": "e4", "target": "g5"}, {"source": "e4", "target": "f5"})


def test_variation_metrics_and_capture():
    r = scored()
    v, vp = r["variations"], r["verified_precision"]
    assert (v["labelled"], v["exact"]) == (2, 2) and v["exact_match_rate"] == 1.0
    assert v["false_lines"] == 1 and v["false_caught"] == 1 and v["false_variation_capture_rate"] == 1.0
    assert v.get("false_incorrectly_verified", 0) == 0
    assert vp["variations_true"] == 1 and vp["variations_false"] == 0
    assert v["structured_answer_moves"] == {"VERIFIED": 2}


def test_a_missed_or_wrongly_verified_variation_is_reported():
    r = scored([line("v9", "II. white king e1-e3", ["e1e4"], legal=False, failure=0)])
    assert r["variations"]["missed"] == 1 and r["variations"]["false_missed"] == 1 and r["variations"]["false_variation_capture_rate"] == 0.0
    r = scored([line("v8", "CRITICAL_LINE: white pawn e2-e4 black king e8-e7", ["e2e4", "e8e7"], holds=False, failure=1)])
    assert r["variations"]["false_incorrectly_verified"] == 1 and r["verified_precision"]["variations_false"] == 1
    assert r["details"]["FALSE_VERIFIED_VARIATION"]


def test_scoring_is_deterministic():
    assert json.dumps(scored(), sort_keys=True) == json.dumps(scored(), sort_keys=True)


def test_known_issues_are_counted():
    assert scored()["known_issues"] == {"other": 1}


# ── the score command's refusals ─────────────────────────────────────────────
@pytest.fixture
def ready(frozen):
    (frozen / "labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in GOLD))
    (frozen / "labels.schema.json").write_text(json.dumps(SCHEMA))
    (frozen / "label_corrections.jsonl").write_text("")
    meta = {"status": "frozen", "predictions_manifest_sha256": fp.sha256_file(frozen / "predictions" / "MANIFEST.json"), "labeller": "t",
            "independent_of_grammar_author": False, "predictions_opened_before_labels_frozen": False, "corrections_file": "label_corrections.jsonl"}
    (frozen / "labels_meta.json").write_text(json.dumps(meta))
    return frozen


def test_score_runs_on_a_complete_experiment(ready):
    r = sc.run(str(ready))
    assert r["meta"]["set"] == "held-out evaluation set" and r["meta"]["draft"] is False and r["meta"]["labels"] == len(GOLD)


def test_score_refuses_labels_that_are_not_frozen_unless_marked_draft(ready):
    meta = json.loads((ready / "labels_meta.json").read_text())
    (ready / "labels_meta.json").write_text(json.dumps({**meta, "status": "in_progress"}))
    with pytest.raises(sc.ScoreError, match="not frozen"):
        sc.run(str(ready))
    assert sc.run(str(ready), draft=True)["meta"]["draft"] is True


def test_score_refuses_labels_made_for_another_snapshot(ready):
    meta = json.loads((ready / "labels_meta.json").read_text())
    (ready / "labels_meta.json").write_text(json.dumps({**meta, "predictions_manifest_sha256": "0" * 64}))
    with pytest.raises(sc.ScoreError, match="does not name"):
        sc.run(str(ready))


def test_score_refuses_altered_predictions(ready):
    path = ready / "predictions" / "x1.json"
    os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
    path.write_text(path.read_text().replace('"VERIFIED"', '"FALSE"'))
    with pytest.raises(sc.ScoreError, match="no longer matches"):
        sc.run(str(ready))


def test_score_refuses_invalid_labels(ready):
    (ready / "labels.jsonl").write_text(json.dumps({**GOLD[0], "source_text": "wrong"}) + "\n")
    with pytest.raises(sc.ScoreError, match="invalid labels"):
        sc.run(str(ready))


def test_score_refuses_before_any_freeze(tmp_path):
    (tmp_path / "labels_meta.json").write_text(json.dumps({"status": "not_started", "predictions_manifest_sha256": None}))
    with pytest.raises(sc.ScoreError, match="no frozen predictions"):
        sc.run(str(tmp_path))


# ── the report ───────────────────────────────────────────────────────────────
def test_template_says_it_has_no_results_and_names_both_sets():
    text = report.render(None, None, SUITE)
    assert "TEMPLATE" in text and "Development/tuning set" in text and "Held-out evaluation set" in text
    assert "_pending_" in text and "contamination" in text.lower() and "83 / 106" in text


def test_report_shows_the_experiment_and_never_adds_the_sets(ready):
    result = sc.run(str(ready))
    manifest = json.loads((ready / "predictions" / "MANIFEST.json").read_text())
    text = report.render(result, manifest, {"cases": [CASE]})
    assert "TEMPLATE" not in text and "DRAFT" not in text
    assert "046bb19" in text and manifest["cases"][0]["raw_explanation_sha256"] in text
    assert "| Capture rate" in text.replace("**", "") and "Verified-claim precision" in text and "Failure taxonomy" in text
    assert "| Exact predicate + args | 83 / 106 | 4 / 4 |" in text  # side by side, not summed
    assert "87 / 110" not in text


def test_a_draft_report_says_so(ready):
    result = {**sc.run(str(ready)), "meta": {"draft": True}}
    assert "DRAFT" in report.render(result, None, {"cases": [CASE]})


def test_miss_review_is_tallied():
    text = report.render(scored(), None, {"cases": [CASE]}, [{"miss_class": "needs_predicate"}, {"miss_class": "needs_predicate"}])
    assert "Needs a new chess predicate: 2" in text
