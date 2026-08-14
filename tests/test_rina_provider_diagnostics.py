from __future__ import annotations

from rina.audit_models import RinaAIAuditEvent
from extensions import db
from services.rina_provider_diagnostics import build_rina_provider_diagnostics


def _clear_provider_env(monkeypatch):
    for name in (
        "OPENAI_API_KEY",
        "OPEN_AI_KEY",
        "RINA_OPENAI_PROVIDER_ENABLED",
        "RINA_ORCHESTRATION_ENABLED",
        "RINA_OPENAI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_diagnostics_reports_missing_credentials_without_secret_material(app, monkeypatch):
    _clear_provider_env(monkeypatch)

    with app.app_context():
        report = build_rina_provider_diagnostics()

    assert report["runtime"]["orchestration_enabled"] is True
    assert report["runtime"]["provider_enabled"] is False
    assert report["runtime"]["credential_source"] == "none"
    assert report["diagnosis"]["code"] == "credentials_missing"
    assert "api_key" not in str(report).lower()


def test_explicit_provider_disable_wins_even_when_key_is_present(app, monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-never-expose-this-value")
    monkeypatch.setenv("RINA_OPENAI_PROVIDER_ENABLED", "false")

    with app.app_context():
        report = build_rina_provider_diagnostics()

    assert report["runtime"]["provider_enabled"] is False
    assert report["runtime"]["provider_flag_state"] == "disabled"
    assert report["runtime"]["credential_source"] == "OPENAI_API_KEY"
    assert report["diagnosis"]["code"] == "provider_disabled_by_flag"
    assert "sk-test-never-expose-this-value" not in str(report)


def test_transient_audit_classifies_quota_rate_limit_or_connectivity_boundary(
    app,
    monkeypatch,
):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-never-expose-this-value")

    with app.app_context():
        db.session.add(
            RinaAIAuditEvent(
                request_id="diag-transient-1",
                authority="driver",
                state="provider_unavailable",
                outcome="provider_failed",
                provider="openai",
                provider_model="gpt-4o-mini",
                provider_status="unavailable",
                audit_metadata={
                    "failure_class": "transient",
                    "provider_attempted": True,
                },
            )
        )
        db.session.commit()

        report = build_rina_provider_diagnostics()

    latest = report["latest_event"]
    assert latest is not None
    assert latest["authority"] == "driver"
    assert latest["provider_attempted"] is True
    assert latest["failure_class"] == "transient"
    assert report["diagnosis"]["code"] == "provider_transient_failure"
    assert "sk-test-never-expose-this-value" not in str(report)


def test_configuration_rejection_is_distinct_from_missing_credentials(app, monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-never-expose-this-value")

    with app.app_context():
        db.session.add(
            RinaAIAuditEvent(
                request_id="diag-config-1",
                authority="owner",
                state="provider_unavailable",
                outcome="provider_failed",
                provider="openai",
                provider_status="rejected",
                audit_metadata={
                    "failure_class": "configuration",
                    "provider_attempted": True,
                },
            )
        )
        db.session.commit()

        report = build_rina_provider_diagnostics()

    assert report["diagnosis"]["code"] == "credentials_or_permissions_rejected"
    assert report["latest_event"]["provider_status"] == "rejected"
    assert "sk-test-never-expose-this-value" not in str(report)


def test_provider_status_route_is_registered_and_requires_authentication(app, client):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/admin/rina/provider-status" in rules

    response = client.get("/admin/rina/provider-status")
    assert response.status_code in {302, 401}
