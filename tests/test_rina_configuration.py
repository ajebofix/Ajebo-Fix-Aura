from __future__ import annotations

import importlib
import os

import rina
from services.rina_runtime_flags import (
    rina_openai_max_output_tokens,
    rina_openai_max_retries,
    rina_openai_model,
    rina_openai_provider_enabled,
    rina_openai_reasoning_effort,
    rina_openai_timeout_seconds,
)


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


def test_rina_provider_defaults_are_sized_for_advisor_context(monkeypatch):
    for name in (
        "RINA_OPENAI_MODEL",
        "RINA_OPENAI_TIMEOUT_SECONDS",
        "RINA_OPENAI_MAX_RETRIES",
        "RINA_OPENAI_REASONING_EFFORT",
        "RINA_OPENAI_MAX_OUTPUT_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)

    assert rina_openai_model() == "gpt-5.6-terra"
    assert rina_openai_timeout_seconds() == 30.0
    assert rina_openai_max_retries() == 0
    assert rina_openai_reasoning_effort() == "low"
    assert rina_openai_max_output_tokens() == 1800
