"""Append-only advisor addenda for completed Treatment Actions."""

from __future__ import annotations

from datetime import datetime, timezone

from extensions import db
from security.access import resolve_vehicle_authority
from treatment.models import (
    TREATMENT_ACTION_ADDENDUM_CATEGORIES,
    TREATMENT_ACTION_ADDENDUM_VISIBILITIES,
    TreatmentAction,
    TreatmentActionAddendum,
)


class TreatmentActionAddendumError(ValueError):
    """Raised when a treatment-action addendum request is invalid."""


def _clean(value: object) -> str:
    return str(value or "").strip()


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def add_treatment_action_addendum(
    *,
    treatment_action_id: int,
    actor_user_id: int,
    category: str,
    reason: str,
    visibility: str,
    detail_text: str,
    idempotency_key: str,
    occurred_at: datetime | None = None,
) -> TreatmentActionAddendum:
    action = db.session.get(TreatmentAction, treatment_action_id)
    if action is None:
        raise TreatmentActionAddendumError("Treatment Action does not exist.")

    authority = resolve_vehicle_authority(actor_user_id, action.car_id)
    if authority not in {"advisor", "administrator"}:
        raise TreatmentActionAddendumError(
            "Treatment Action addenda require advisor authority."
        )
    if action.status != "completed":
        raise TreatmentActionAddendumError(
            "Addenda can only be recorded against completed work."
        )

    clean_category = _clean(category).lower()
    clean_reason = _clean(reason)
    clean_visibility = _clean(visibility).lower()
    clean_detail = _clean(detail_text)
    clean_key = _clean(idempotency_key)

    if clean_category not in set(TREATMENT_ACTION_ADDENDUM_CATEGORIES):
        raise TreatmentActionAddendumError("Invalid addendum category.")
    if not clean_reason:
        raise TreatmentActionAddendumError("Addendum reason is required.")
    if len(clean_reason) > 240:
        raise TreatmentActionAddendumError(
            "Addendum reason must be 240 characters or fewer."
        )
    if clean_visibility not in set(TREATMENT_ACTION_ADDENDUM_VISIBILITIES):
        raise TreatmentActionAddendumError("Invalid addendum visibility.")
    if not clean_detail:
        raise TreatmentActionAddendumError("Addendum detail is required.")
    if not clean_key:
        raise TreatmentActionAddendumError("Addendum idempotency key is required.")
    if len(clean_key) > 128:
        raise TreatmentActionAddendumError(
            "Addendum idempotency key must be 128 characters or fewer."
        )

    existing = TreatmentActionAddendum.query.filter_by(
        idempotency_key=clean_key
    ).first()
    if existing is not None:
        if not all(
            (
                existing.treatment_action_id == action.id,
                existing.created_by_user_id == actor_user_id,
                existing.category == clean_category,
                existing.reason == clean_reason,
                existing.visibility == clean_visibility,
                existing.detail_text == clean_detail,
            )
        ):
            raise TreatmentActionAddendumError(
                "Addendum idempotency key was reused with different content."
            )
        return existing

    event_time = occurred_at or _utcnow_naive()
    if event_time.tzinfo is not None:
        event_time = event_time.astimezone(timezone.utc).replace(tzinfo=None)

    addendum = TreatmentActionAddendum(
        treatment_action_id=action.id,
        created_by_user_id=actor_user_id,
        category=clean_category,
        reason=clean_reason,
        visibility=clean_visibility,
        detail_text=clean_detail,
        idempotency_key=clean_key,
        created_at=event_time,
    )
    db.session.add(addendum)
    db.session.flush()
    return addendum
