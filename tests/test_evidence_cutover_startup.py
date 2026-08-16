from __future__ import annotations

import pytest

from app import create_app
from evidence.readiness import EvidenceCutoverConfigurationError


def test_production_startup_rejects_advisor_review_without_private_retrieval(
    monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setenv("SECRET_KEY", "cutover-production-test-secret")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")
    monkeypatch.setenv("EVIDENCE_ADVISOR_REVIEW_ENABLED", "1")
    monkeypatch.setenv("EVIDENCE_RETRIEVAL_ENABLED", "0")
    monkeypatch.setenv("EVIDENCE_IMAGE_INTAKE_ENABLED", "0")
    monkeypatch.setenv("EVIDENCE_TIMELINE_ENABLED", "0")
    monkeypatch.setenv("EVIDENCE_ADVISOR_DELETION_ENABLED", "0")

    with pytest.raises(EvidenceCutoverConfigurationError):
        create_app()
