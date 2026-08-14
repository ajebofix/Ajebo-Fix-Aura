"""Privacy-safe operational diagnostics for the Wave 1.3 Rina provider boundary.

This module reads only runtime configuration *presence/state* and the existing
metadata-only ``RinaAIAuditEvent`` ledger. It never exposes credential values,
prompts, user messages, provider response bodies, or hidden memory.
"""

from __future__ import annotations

import os
from typing import Any

from rina.audit_models import RinaAIAuditEvent
from services.rina_runtime_flags import (
    rina_openai_max_retries,
    rina_openai_model,
    rina_openai_provider_enabled,
    rina_openai_timeout_seconds,
    rina_orchestration_enabled,
)


_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _flag_state(name: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return "automatic"

    normalized = raw.strip().lower()
    if normalized in _TRUTHY:
        return "enabled"
    if normalized in _FALSY:
        return "disabled"
    return "invalid"


def _credential_source() -> str:
    canonical = bool((os.getenv("OPENAI_API_KEY") or "").strip())
    legacy = bool((os.getenv("OPEN_AI_KEY") or "").strip())

    if canonical and legacy:
        return "canonical_and_legacy"
    if canonical:
        return "OPENAI_API_KEY"
    if legacy:
        return "OPEN_AI_KEY_legacy"
    return "none"


def _audit_snapshot(event: RinaAIAuditEvent) -> dict[str, Any]:
    metadata = event.audit_metadata or {}
    return {
        "id": event.id,
        "request_id": event.request_id,
        "user_id": event.user_id,
        "car_id": event.car_id,
        "authority": event.authority,
        "state": event.state,
        "outcome": event.outcome,
        "provider": event.provider,
        "provider_model": event.provider_model,
        "provider_status": event.provider_status,
        "failure_class": metadata.get("failure_class"),
        "provider_attempted": bool(metadata.get("provider_attempted", False)),
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _diagnosis(
    *,
    orchestration_enabled: bool,
    provider_enabled: bool,
    provider_flag_state: str,
    credential_source: str,
    latest: dict[str, Any] | None,
) -> dict[str, str]:
    if not orchestration_enabled:
        return {
            "status": "blocked",
            "code": "orchestration_disabled",
            "summary": "Wave 1.3 orchestration is explicitly disabled.",
            "next_action": "Remove the false override or enable RINA_ORCHESTRATION_ENABLED.",
        }

    if provider_flag_state == "disabled":
        return {
            "status": "blocked",
            "code": "provider_disabled_by_flag",
            "summary": "Outbound OpenAI calls are explicitly disabled by the provider rollout flag.",
            "next_action": "Remove the false override or enable RINA_OPENAI_PROVIDER_ENABLED.",
        }

    if provider_flag_state == "invalid":
        return {
            "status": "blocked",
            "code": "provider_flag_invalid",
            "summary": "The provider rollout flag has an unrecognised value.",
            "next_action": "Use true/false for RINA_OPENAI_PROVIDER_ENABLED or remove the variable.",
        }

    if credential_source == "none":
        return {
            "status": "blocked",
            "code": "credentials_missing",
            "summary": "No OpenAI credential variable is present on this Aura web service.",
            "next_action": "Configure OPENAI_API_KEY on the live Ajebo-Fix-Aura Railway web service.",
        }

    if not provider_enabled:
        return {
            "status": "blocked",
            "code": "provider_not_enabled",
            "summary": "Rina's language provider is not enabled at runtime.",
            "next_action": "Review the provider rollout flag and credential presence.",
        }

    if latest is None:
        return {
            "status": "ready",
            "code": "no_audit_events",
            "summary": "Provider configuration is present, but no Rina audit event is available yet.",
            "next_action": "Send one normal Rina message and refresh this page.",
        }

    if latest.get("state") == "answered" and latest.get("provider_status") == "ok":
        return {
            "status": "healthy",
            "code": "provider_healthy",
            "summary": "The latest audited Rina request completed through the language provider.",
            "next_action": "No provider remediation is indicated by the latest request.",
        }

    failure_class = latest.get("failure_class")
    attempted = bool(latest.get("provider_attempted"))

    if failure_class == "configuration":
        if attempted:
            return {
                "status": "degraded",
                "code": "credentials_or_permissions_rejected",
                "summary": "OpenAI was attempted but rejected the configured credentials or permissions.",
                "next_action": "Verify the project API key and its project permissions; rotate the key only if rejection is confirmed.",
            }
        return {
            "status": "blocked",
            "code": "provider_configuration_error",
            "summary": "The provider could not be constructed from the current runtime configuration.",
            "next_action": "Verify OPENAI_API_KEY is present on the live web service and the provider flag is not forcing an invalid state.",
        }

    if failure_class == "transient":
        return {
            "status": "degraded",
            "code": "provider_transient_failure",
            "summary": "OpenAI was attempted but returned a transient availability, connection, timeout, rate-limit, or quota-class failure.",
            "next_action": "Check OpenAI API project billing/credits and usage limits first, then Railway outbound connectivity if billing is healthy.",
        }

    if failure_class == "rejected":
        return {
            "status": "degraded",
            "code": "provider_request_rejected",
            "summary": "OpenAI was reached but rejected the request contract or model request.",
            "next_action": "Review the configured model and the minimized Responses API request contract.",
        }

    if failure_class == "feature_disabled":
        return {
            "status": "blocked",
            "code": "provider_feature_disabled",
            "summary": "The latest request was handled while a Rina rollout feature was disabled.",
            "next_action": "Review the Wave 1.3 orchestration and provider rollout flags.",
        }

    if latest.get("provider_status") in {"unavailable", "rejected", "disabled"}:
        return {
            "status": "degraded",
            "code": "provider_unavailable_unclassified",
            "summary": "The latest Rina request did not complete through the language provider.",
            "next_action": "Use the latest audit metadata below to narrow the provider failure before changing credentials.",
        }

    return {
        "status": "ready",
        "code": "provider_ready_unconfirmed",
        "summary": "Runtime configuration appears ready, but the latest audit does not prove a successful provider answer.",
        "next_action": "Send one normal Rina message and refresh this page.",
    }


def build_rina_provider_diagnostics(*, limit: int = 10) -> dict[str, Any]:
    """Return an administrator-safe provider status report.

    The report contains no secret values and no conversational content. Recent
    audit rows are already constrained by the Wave 1.3 metadata-only audit
    contract.
    """

    bounded_limit = min(max(int(limit), 1), 25)
    rows = (
        RinaAIAuditEvent.query.order_by(
            RinaAIAuditEvent.created_at.desc(),
            RinaAIAuditEvent.id.desc(),
        )
        .limit(bounded_limit)
        .all()
    )
    events = [_audit_snapshot(row) for row in rows]

    orchestration_enabled = rina_orchestration_enabled()
    provider_enabled = rina_openai_provider_enabled()
    provider_flag_state = _flag_state("RINA_OPENAI_PROVIDER_ENABLED")
    orchestration_flag_state = _flag_state("RINA_ORCHESTRATION_ENABLED")
    credential_source = _credential_source()
    latest = events[0] if events else None

    return {
        "runtime": {
            "orchestration_enabled": orchestration_enabled,
            "orchestration_flag_state": orchestration_flag_state,
            "provider_enabled": provider_enabled,
            "provider_flag_state": provider_flag_state,
            "credential_source": credential_source,
            "model": rina_openai_model(),
            "timeout_seconds": rina_openai_timeout_seconds(),
            "max_retries": rina_openai_max_retries(),
        },
        "diagnosis": _diagnosis(
            orchestration_enabled=orchestration_enabled,
            provider_enabled=provider_enabled,
            provider_flag_state=provider_flag_state,
            credential_source=credential_source,
            latest=latest,
        ),
        "latest_event": latest,
        "recent_events": events,
    }
