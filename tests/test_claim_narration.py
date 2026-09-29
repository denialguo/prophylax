"""M13: claim-grounded narration behind PROPHYLAX_NARRATION_INPUT (default: flag)."""
import pytest

from config.settings import get_narration_input
from domain.claims import build_claims
from domain.convert import move_analysis_from_flag
from evals.validate_narration import claims_ground, concept_violation, grounding_violation
from scripts.format_narration import claim_sentence, format_claims_for_llm
from tests.test_claims import EVAL, board_before, board_claims


def prompt_for(name, board=True):
    flag = EVAL[name]
    claims = board_claims(flag) if board else build_claims(move_analysis_from_flag(flag), game_id="g1")
    return flag, claims, format_claims_for_llm(flag, claims, 1, 1800)


def test_claims_prompt_lists_exactly_the_claims():
    flag, claims, prompt = prompt_for("scandinavian_b4_blunder")
    assert prompt.splitlines()[0] == "header: Move 13.b4 (White): WDL drop 31.9%"
    block = prompt.split("VERIFIED CLAIMS\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert block == [f"C{i}: {claim_sentence(c)}" for i, c in enumerate(claims, 1)]
    assert "Black's pawn on d4 attacks it." in prompt
    # D8 and no internal numbers: no raw deltas or scores reach the narrator
    for banned in ("feature_deltas", "WDL Delta", "piece_activity", "pawn_structure", "king_safety_delta", "-0."):
        assert banned not in prompt


def test_payload_only_case_builds_a_prompt():
    _, claims, prompt = prompt_for("synthetic_endgame_pawn_race", board=False)
    assert "VERIFIED CLAIMS" in prompt and len(claims) >= 1
    assert "pawn_structure" not in prompt  # its only feature is a score (D8)


@pytest.mark.parametrize("text,violates", [
    ("This leaves a backward pawn on c6.", "backward pawn"),
    ("Two backward pawns now.", "backward pawn"),
    ("White gains an outpost.", "outpost"),
    ("Black's outposts multiply.", "outpost"),
    ("a3 becomes a hole.", "hole"),
    ("The weak square on a3 hurts.", "weak square"),
    ("The whole structure creaks.", None),   # 'whole' is not 'hole'
    ("A long-term weakness appears.", None),  # ordinary vocabulary stays allowed
    ("King activity decides it.", None),
])
def test_terminology_needs_a_claim(text, violates):
    claims = build_claims(move_analysis_from_flag(EVAL["scandinavian_h4_blunder"]), game_id="g1")  # no structure
    reason = concept_violation(text, claims)
    assert (reason is None) if violates is None else (violates in reason)


def test_terminology_backed_by_claims_passes():
    _, claims, _ = prompt_for("carlsbad_b5_quiet")
    for text in ("c6 is a backward pawn.", "c5 is a hole and a future outpost.", "The weak squares a6 and c5."):
        assert concept_violation(text, claims) is None


def test_claims_ground_allows_evidence_squares_only():
    flag, claims, _ = prompt_for("scandinavian_b4_blunder")
    ground = claims_ground(claims)
    assert grounding_violation("The d4 pawn attacks c3, and a3 is weak.", ground) is None
    assert "h5" in grounding_violation("The knight heads for h5.", ground)
    assert grounding_violation("The engine preferred 13.cxd4.", ground, None) is None
    assert "Qxh7" in grounding_violation("Qxh7 wins.", ground)


def test_switch(monkeypatch):
    monkeypatch.delenv("PROPHYLAX_NARRATION_INPUT", raising=False)
    assert get_narration_input() == "flag"
    monkeypatch.setenv("PROPHYLAX_NARRATION_INPUT", "claims")
    assert get_narration_input() == "claims"
    monkeypatch.setenv("PROPHYLAX_NARRATION_INPUT", "claim")
    with pytest.raises(ValueError):
        get_narration_input()


def test_claims_narrators_add_the_claims_section_only():
    from app.agent import narrator_for
    from tests.test_agent_structure import test_each_phase_gets_contract_plus_its_section  # noqa: F401
    for phase in ("opening", "middlegame", "endgame"):
        flag_instr = narrator_for(phase).instruction
        claims_instr = narrator_for(phase, claims=True).instruction
        assert "VERIFIED CLAIMS" not in flag_instr  # the flag arm keeps today's instruction
        assert claims_instr.startswith(flag_instr) and "VERIFIED CLAIMS" in claims_instr


def test_agent_in_claims_mode(monkeypatch):
    from tests.payload_schema import pgn_payload
    from tests.test_domain_models import _agent_run
    monkeypatch.setenv("PROPHYLAX_NARRATION_INPUT", "claims")
    pgn = "1. e4 d5 2. Nc3 d4 3. Nce2 e5 4. d3 Nc6 5. Ng3 Nf6 6. Nf3 Bg4 7. Be2 Bxf3 8. Bxf3 Bd6 " \
          "9. O-O O-O 10. Bg5 h6 11. Bd2 Re8 12. c3 Ne7 13. b4 *"
    flag = {k: v for k, v in EVAL["scandinavian_b4_blunder"].items()}
    prompts, out = _agent_run(pgn_payload(pgn, [flag]), messages=[pgn])
    (name, prompt), = [p for p in prompts if p[0].startswith("analysing_")]
    assert "VERIFIED CLAIMS" in prompt and "Black's pawn on d4 attacks it." in prompt
    assert "Game Report" in out[-1]


def test_agent_claims_mode_rejects_unbacked_terms(monkeypatch):
    """'backward pawn' with no backward claim: retried once, then the claim-sentence fallback."""
    import asyncio
    from app.agent import CoachingAgent
    monkeypatch.setenv("PROPHYLAX_NARRATION_INPUT", "claims")
    flag = EVAL["scandinavian_h4_blunder"]
    move = move_analysis_from_flag(flag)
    calls = []

    async def sub(agent, prompt):
        calls.append(prompt)
        return "This creates a backward pawn. The engine preferred another plan."

    coach = CoachingAgent(name="t")
    object.__setattr__(coach, "_run_sub_agent", sub)
    stats = {"attempted": 1, "passed_first": 0, "passed_retry": 0, "fallback": 0}
    text = asyncio.run(coach._narrate(flag, None, 1, 1800, stats, move=move, game_id="g1"))
    assert len(calls) == 2 and "backward pawn" in calls[1]
    assert stats["fallback"] == 1
    claims = build_claims(move, game_id="g1")
    assert text == " ".join(claim_sentence(c) for c in claims)
