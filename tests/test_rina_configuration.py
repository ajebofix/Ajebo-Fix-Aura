from __future__ import annotations

import importlib
import os

import rina


def test_legacy_open_ai_key_populates_canonical_variable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPEN_AI_KEY", "legacy-test-key")

    importlib.reload(rina)

    assert os.environ["OPENAI_API_KEY"] == "legacy-test-key"


def test_canonical_openai_key_is_never_overwritten(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "canonical-test-key")
    monkeypatch.setenv("OPEN_AI_KEY", "legacy-test-key")

    importlib.reload(rina)

    assert os.environ["OPENAI_API_KEY"] == "canonical-test-key"
