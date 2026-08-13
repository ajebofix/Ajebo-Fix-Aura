"""Privacy-safe audit persistence for material Rina orchestration outcomes."""

from __future__ import annotations

from typing import Any, Final

from extensions import db
from rina.audit_models import RinaAIAuditEvent


_ALLOWED_METADATA_KEYS: Final = frozenset(
    {
        "channel",
        "context_version",
        "memory_policy",
        "feature_flag",
        "failure_class",
        "provider_attempted",
    }
)

_PROHIBITED_KEY_FRAGMENTS: Final = (
    "prompt",
    "message",
    "response",
    "secret",
    "token",
    "api_key",
    "password",
    "chain",
    "thought",
)


class RinaAuditPolicyError(ValueError):
    """Raised when unsafe metadata is offered to the Rina audit surface."""


def _safe_evidence_refs(value: tuple[dict[str, Any], ...] | list[dict[str, Any]]):
    clean: list[dict[str, Any]] = []
    for item in value or ():
        if not isinstance(item, dict):
            continue
        ref_type = str(item.get("type") or "").strip()
        ref_id = item.get("id")
        if not ref_type or ref_id is None:
            continue
        try:
            normalized_id = int(ref_id)
        except (TypeError, ValueError):
            continue
        clean.append({"type": ref_type[:64], "id": normalized_id})
    return clean


def _safe_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}

    clean: dict[str, Any] = {}
    for key, item in value.items():
        normalized = str(key).strip().lower()
        if any(fragment in normalized for fragment in _PROHIBITED_KEY_FRAGMENTS):
            raise RinaAuditPolicyError(
                f"prohibited Rina audit metadata key: {key!r}"
            )
        if normalized not in _ALLOWED_METADATA_KEYS:
            raise RinaAuditPolicyError(
                f"unsupported Rina audit metadata key: {key!r}"
            )
        if isinstance(item, (str, int, float, bool)) or item is None:
            clean[normalized] = item
        else:
            clean[normalized] = str(item)[:120]
    return clean


def record_rina_audit(
    *,
    request_id: str,
    user_id: int | None,
    car_id: int | None,
    authority: str | None,
    state: str,
    outcome: str,
    provider_status: str,
    action_family: str = "respond",
    provider: str | None = None,
    provider_model: str | None = None,
    provider_request_id: str | None = None,
    evidence_refs: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    metadata: dict[str, Any] | None = None,
    commit: bool = True,
) -> RinaAIAuditEvent:
    """Persist one final audit row per orchestration request.

    Replay with the same request ID returns the existing record rather than
    duplicating it. The function has no parameters for prompts, user messages,
    model response bodies, credentials or chain-of-thought.
    """

    clean_request_id = (request_id or "").strip()
    if not clean_request_id or len(clean_request_id) > 64:
        raise RinaAuditPolicyError("valid request_id is required")

    existing = RinaAIAuditEvent.query.filter_by(request_id=clean_request_id).first()
    if existing is not None:
        return existing

    row = RinaAIAuditEvent(
        request_id=clean_request_id,
        user_id=user_id,
        car_id=car_id,
        authority=authority,
        state=state,
        outcome=outcome,
        action_family=(action_family or "respond")[:64],
        provider=provider[:32] if provider else None,
        provider_model=provider_model[:96] if provider_model else None,
        provider_status=provider_status,
        provider_request_id=(
            provider_request_id[:128] if provider_request_id else None
        ),
        evidence_refs=_safe_evidence_refs(evidence_refs),
        audit_metadata=_safe_metadata(metadata),
    )
    db.session.add(row)
    db.session.flush()

    if commit:
        db.session.commit()

    return row
