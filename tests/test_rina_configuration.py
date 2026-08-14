from __future__ import annotations

import importlib
import os

import rina
from services.rina_runtime_flags import rina_openai_provider_enabled


def test_legacy_open_ai_key_populates_canonical_variable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPEN_AI_KEY", "  legacy-test-key  ")

    importlib.reload(rina)

    assert os.environ["OPENAI_API_KEY"] == "legacy-test-key"


def test_canonical_openai_key_is_never_overwritten(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "  canonical-test-key  ")
    monkeypatch.setenv("OPEN_AI_KEY", "legacy-test-key")

    importlib.reload(rina)

    assert os.environ["OPENAI_API_KEY"] == "canonical-test-key"


def test_runtime_flag_accepts_documented_railway_alias(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPEN_AI_KEY", "legacy-test-key")
    monkeypatch.delenv("RINA_OPENAI_PROVIDER_ENABLED", raising=False)

    assert rina_openai_provider_enabled() is True


def test_explicit_provider_disable_still_wins_over_key_alias(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPEN_AI_KEY", "legacy-test-key")
    monkeypatch.setenv("RINA_OPENAI_PROVIDER_ENABLED", "false")

    assert rina_openai_provider_enabled() is False
