from __future__ import annotations

import pytest

from evidence.readiness import (
    EvidenceCutoverConfigurationError,
    evaluate_evidence_cutover_readiness,
    require_safe_evidence_cutover_configuration,
)


def _config() -> dict[str, object]:
    return {
        "EVIDENCE_IMAGE_INTAKE_ENABLED": False,
        "EVIDENCE_RETRIEVAL_ENABLED": False,
        "EVIDENCE_ADVISOR_REVIEW_ENABLED": False,
        "EVIDENCE_TIMELINE_ENABLED": False,
        "EVIDENCE_ADVISOR_DELETION_ENABLED": False,
        "EVIDENCE_RETENTION_DAYS": None,
        "EVIDENCE_RETRIEVAL_GRANT_SECONDS": None,
        "EVIDENCE_STORAGE_PROVIDER": "r2",
        "R2_ACCOUNT_ID": None,
        "R2_ACCESS_KEY_ID": None,
        "R2_SECRET_ACCESS_KEY": None,
        "R2_BUCKET": None,
    }


def _with_private_storage(config: dict[str, object]) -> dict[str, object]:
    config.update(
        {
            "R2_ACCOUNT_ID": "cutover-test-account",
            "R2_ACCESS_KEY_ID": "cutover-test-access",
            "R2_SECRET_ACCESS_KEY": "cutover-test-secret",
            "R2_BUCKET": "cutover-test-bucket",
        }
    )
    return config


def test_disabled_evidence_stack_is_ready_without_storage_credentials():
    readiness = evaluate_evidence_cutover_readiness(_config())

    assert readiness.ready is True
    assert readiness.state == "disabled"
    assert readiness.enabled_features == ()
    assert readiness.storage_required is False
    assert readiness.errors == ()


def test_image_intake_requires_private_storage_and_retention_policy():
    config = _config()
    config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True

    readiness = evaluate_evidence_cutover_readiness(config)

    assert readiness.ready is False
    assert readiness.state == "not_ready"
    assert readiness.storage_required is True
    assert "private_storage_not_configured" in readiness.errors
    assert "retention_policy_not_configured" in readiness.errors


def test_image_intake_is_ready_with_private_storage_and_finite_retention():
    config = _with_private_storage(_config())
    config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    config["EVIDENCE_RETENTION_DAYS"] = "180"

    readiness = evaluate_evidence_cutover_readiness(config)

    assert readiness.ready is True
    assert readiness.state == "ready"
    assert readiness.enabled_features == ("image_intake",)
    assert readiness.errors == ()


def test_private_retrieval_requires_safe_grant_policy():
    config = _with_private_storage(_config())
    config["EVIDENCE_RETRIEVAL_ENABLED"] = True
    config["EVIDENCE_RETRIEVAL_GRANT_SECONDS"] = "301"

    readiness = evaluate_evidence_cutover_readiness(config)
    assert readiness.ready is False
    assert "retrieval_grant_policy_not_configured" in readiness.errors

    config["EVIDENCE_RETRIEVAL_GRANT_SECONDS"] = "120"
    readiness = evaluate_evidence_cutover_readiness(config)
    assert readiness.ready is True
    assert readiness.errors == ()


def test_advisor_review_cannot_be_cut_over_without_private_retrieval():
    config = _config()
    config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True

    readiness = evaluate_evidence_cutover_readiness(config)

    assert readiness.ready is False
    assert readiness.storage_required is False
    assert "advisor_review_requires_private_retrieval" in readiness.errors


def test_advisor_review_is_ready_when_private_retrieval_is_ready():
    config = _with_private_storage(_config())
    config["EVIDENCE_RETRIEVAL_ENABLED"] = True
    config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    config["EVIDENCE_RETRIEVAL_GRANT_SECONDS"] = "120"

    readiness = evaluate_evidence_cutover_readiness(config)

    assert readiness.ready is True
    assert readiness.enabled_features == ("retrieval", "advisor_review")
    assert readiness.errors == ()


def test_timeline_can_remain_read_only_but_warns_without_active_review():
    config = _config()
    config["EVIDENCE_TIMELINE_ENABLED"] = True

    readiness = evaluate_evidence_cutover_readiness(config)

    assert readiness.ready is True
    assert readiness.state == "ready"
    assert readiness.storage_required is False
    assert readiness.warnings == ("timeline_enabled_without_active_review",)


def test_advisor_deletion_requires_private_storage():
    config = _config()
    config["EVIDENCE_ADVISOR_DELETION_ENABLED"] = True

    readiness = evaluate_evidence_cutover_readiness(config)

    assert readiness.ready is False
    assert readiness.storage_required is True
    assert "private_storage_not_configured" in readiness.errors


def test_strict_cutover_guard_raises_only_for_blocking_configuration_errors():
    bad = _config()
    bad["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True

    with pytest.raises(EvidenceCutoverConfigurationError):
        require_safe_evidence_cutover_configuration(bad)

    timeline_only = _config()
    timeline_only["EVIDENCE_TIMELINE_ENABLED"] = True
    readiness = require_safe_evidence_cutover_configuration(timeline_only)
    assert readiness.ready is True
    assert readiness.warnings


def test_healthz_reports_disabled_evidence_stack_without_requiring_r2(app, client):
    response = client.get("/healthz")
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["evidence"]["state"] == "disabled"
    assert payload["evidence"]["issues"] == []


def test_healthz_fails_closed_when_enabled_evidence_config_is_incomplete(app, client):
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    app.config["EVIDENCE_RETENTION_DAYS"] = None
    app.config["R2_ACCOUNT_ID"] = None
    app.config["R2_ACCESS_KEY_ID"] = None
    app.config["R2_SECRET_ACCESS_KEY"] = None
    app.config["R2_BUCKET"] = None

    response = client.get("/healthz")
    assert response.status_code == 503

    payload = response.get_json()
    assert payload["status"] == "not_ready"
    assert payload["evidence"]["state"] == "not_ready"
    assert "private_storage_not_configured" in payload["evidence"]["issues"]
    assert "retention_policy_not_configured" in payload["evidence"]["issues"]

    serialized = str(payload)
    assert "cutover-test-secret" not in serialized
    assert "R2_SECRET_ACCESS_KEY" not in serialized
    assert "R2_BUCKET" not in serialized


def test_healthz_reports_ready_after_static_cutover_requirements_are_met(app, client):
    app.config.update(
        EVIDENCE_IMAGE_INTAKE_ENABLED=True,
        EVIDENCE_RETRIEVAL_ENABLED=True,
        EVIDENCE_ADVISOR_REVIEW_ENABLED=True,
        EVIDENCE_RETENTION_DAYS="180",
        EVIDENCE_RETRIEVAL_GRANT_SECONDS="120",
        EVIDENCE_STORAGE_PROVIDER="r2",
        R2_ACCOUNT_ID="cutover-test-account",
        R2_ACCESS_KEY_ID="cutover-test-access",
        R2_SECRET_ACCESS_KEY="cutover-test-secret",
        R2_BUCKET="cutover-test-bucket",
    )

    response = client.get("/healthz")
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["evidence"]["state"] == "ready"
    assert payload["evidence"]["issues"] == []
    assert payload["evidence"]["enabled_features"] == [
        "image_intake",
        "retrieval",
        "advisor_review",
    ]
