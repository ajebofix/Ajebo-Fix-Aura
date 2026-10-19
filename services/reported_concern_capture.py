"""Advisor-governed capture of reported concerns, including post-delivery reports.

A report is not a diagnosis, completed repair, inspection result or payment event.
The canonical concern.reported event is emitted explicitly within the same
transaction so Rina can receive the new concern ID before committing.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from extensions import db
from models import Car, CarFault
from security.access import resolve_vehicle_authority
from services.event_emission import emit_vehicle_event


class ConcernCaptureError(ValueError):
    """The requested report cannot be persisted safely."""


CATEGORIES = frozenset({
    "engine", "transmission", "brakes", "suspension",
    "suspension_steering", "electrical", "electrical_electronics",
    "warning_light", "cooling", "body", "interior", "other",
})


def record_advisor_reported_concern(
    *,
    car_id: int,
    actor_user_id: int,
    description: str,
    category: str = "other",
    observed_at: datetime | None = None,
    source: str = "admin",
    dedupe_recent: bool = False,
) -> tuple[CarFault, bool]:
    """Stage a client-reported observation, never a diagnosis.

    Returns (record, created). Caller owns commit/rollback. A repeated identical
    Rina command during a short retry window reuses the original record.
    """
    if resolve_vehicle_authority(actor_user_id, car_id) not in {"advisor", "administrator"}:
        raise ConcernCaptureError("Advisor access to this vehicle is required.")
    if db.session.get(Car, car_id) is None:
        raise ConcernCaptureError("Vehicle not found.")

    description = (description or "").strip()
    if not description or len(description) > 3000:
        raise ConcernCaptureError("Describe the concern in 1–3000 characters.")
    category = (category or "other").strip().lower()
    if category not in CATEGORIES:
        raise ConcernCaptureError("Choose a supported area of concern.")
    if source not in {"admin", "rina"}:
        raise ConcernCaptureError("Unsupported report source.")
    if observed_at is not None and not isinstance(observed_at, datetime):
        raise ConcernCaptureError("Observation date must be valid.")

    if dedupe_recent:
        existing = (
            CarFault.query.filter_by(
                car_id=car_id,
                reported_by=actor_user_id,
                source=source,
                description=description,
            )
            .filter(CarFault.created_at >= datetime.utcnow() - timedelta(hours=2))
            .order_by(CarFault.id.desc())
            .first()
        )
        if existing is not None:
            return existing, False

    concern = CarFault(
        car_id=car_id,
        title="Client-reported concern",
        category=category,
        description=description,
        status="reported",
        observed_at=observed_at,
        reported_by=actor_user_id,
        reported_at=datetime.utcnow(),
        source=source,
    )
    db.session.add(concern)
    db.session.flush()
    # The CarFault before_commit integration watches session.new. Because
    # Rina needs the concern ID to audit the same transaction, this explicit
    # emission follows the same canonical contract after flush.
    emit_vehicle_event(
        car_id=car_id,
        event_type="concern.reported",
        subject_type="reported_concern",
        subject_id=concern.id,
        actor_type="user",
        actor_user_id=actor_user_id,
        visibility="client",
        source=source,
        occurred_at=concern.reported_at,
        title="Reported concern recorded",
        progression_direction="insufficient_evidence",
        idempotency_key=f"reported_concern:{concern.id}:reported",
        previous_state=None,
        new_state="reported",
        evidence_refs=[{"type": "reported_concern", "id": concern.id}],
        data={"category": category, "reported_source": source},
    )
    return concern, True
