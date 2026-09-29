"""Fast unit tests for narrator model rotation config.

No LLM calls, no engine calls. Tests config resolution, validation,
fallback ordering, and collision detection via monkeypatch.
"""
import os
import pytest
from unittest.mock import patch

from config.settings import (
    APPROVED_NARRATORS,
    get_narrator_model,
    get_fallback_narrators,
    get_judge_model,
    _DEFAULT_NARRATOR,
    _DEFAULT_JUDGE,
)


class TestDefaultResolution:
    """get_narrator_model() returns _DEFAULT_NARRATOR when env var is unset."""

    def test_default_model(self, monkeypatch):
        monkeypatch.delenv("NARRATOR_MODEL", raising=False)
        assert get_narrator_model() == _DEFAULT_NARRATOR

    def test_default_is_approved(self):
        assert _DEFAULT_NARRATOR in APPROVED_NARRATORS


class TestEnvOverride:
    """NARRATOR_MODEL env var overrides the default."""

    def test_override_to_approved_model(self, monkeypatch):
        for model in APPROVED_NARRATORS:
            monkeypatch.setenv("NARRATOR_MODEL", model)
            assert get_narrator_model() == model

    def test_override_to_uncertified_model_hard_fails(self, monkeypatch):
        monkeypatch.setenv("NARRATOR_MODEL", "gemini-99-turbo")
        with pytest.raises(ValueError, match="not certified"):
            get_narrator_model()

    def test_error_message_names_gate_command(self, monkeypatch):
        monkeypatch.setenv("NARRATOR_MODEL", "gemini-99-turbo")
        with pytest.raises(ValueError, match="pytest -v -m narration"):
            get_narrator_model()


class TestFallbackOrder:
    """get_fallback_narrators() returns APPROVED_NARRATORS minus the primary."""

    def test_fallback_excludes_primary(self):
        primary = APPROVED_NARRATORS[0]
        fallbacks = get_fallback_narrators(primary)
        assert primary not in fallbacks
        assert len(fallbacks) == len(APPROVED_NARRATORS) - 1

    def test_fallback_preserves_order(self):
        primary = APPROVED_NARRATORS[0]
        fallbacks = get_fallback_narrators(primary)
        expected = [m for m in APPROVED_NARRATORS if m != primary]
        assert fallbacks == expected

    def test_fallback_all_approved(self):
        for primary in APPROVED_NARRATORS:
            for fb in get_fallback_narrators(primary):
                assert fb in APPROVED_NARRATORS


class TestJudgeNarratorCollision:
    """JUDGE_MODEL must differ from the resolved narrator model."""

    def test_default_no_collision(self, monkeypatch):
        monkeypatch.delenv("NARRATOR_MODEL", raising=False)
        monkeypatch.delenv("JUDGE_MODEL", raising=False)
        # Should not raise
        judge = get_judge_model()
        narrator = get_narrator_model()
        assert judge != narrator

    def test_collision_hard_fails(self, monkeypatch):
        monkeypatch.setenv("NARRATOR_MODEL", "gemini-3.5-flash")
        monkeypatch.setenv("JUDGE_MODEL", "gemini-3.5-flash")
        with pytest.raises(ValueError, match="must differ"):
            get_judge_model()

    def test_judge_stays_independent(self, monkeypatch):
        monkeypatch.delenv("JUDGE_MODEL", raising=False)
        monkeypatch.delenv("NARRATOR_MODEL", raising=False)
        assert get_judge_model() == _DEFAULT_JUDGE


def test_certification_run_admits_only_the_named_model(monkeypatch):
    monkeypatch.setenv("NARRATOR_MODEL", "groq/some-model")
    monkeypatch.setenv("PROPHYLAX_CERTIFY_NARRATOR", "groq/some-model")
    assert get_narrator_model() == "groq/some-model"
    monkeypatch.setenv("PROPHYLAX_CERTIFY_NARRATOR", "groq/other-model")
    with pytest.raises(ValueError, match="not certified"):
        get_narrator_model()


def test_certification_run_has_no_fallbacks(monkeypatch):
    from config.settings import get_fallback_narrators
    monkeypatch.setenv("PROPHYLAX_CERTIFY_NARRATOR", "groq/some-model")
    assert get_fallback_narrators("groq/some-model") == []
