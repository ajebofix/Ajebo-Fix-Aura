"""Transactional integration between Reported Concerns and canonical events.

The existing Aura codebase has several active routes that create or transition
``CarFault`` rows (client, driver, advisor, emergency review). Wave 1.2 must
cover the domain without duplicating event semantics in every route.

This module therefore observes the *domain transaction* immediately before
commit, flushes the concern mutation so its durable subject id exists, and then
delegates every canonical write to ``services.event_emission``. The listener
never commits independently: concern state and event history still succeed or
fail as one transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from flask import has_request_context, request
from flask_login import current_user
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from extensions import db
from models import CarFault
from services.event_emission import EventEmissionError, emit_vehicle_event


_INTEGRATION_GUARD = "aura_reported_concern_event_integration"


class ReportedConcernIntegrationError(EventEmissionError):
    """Raised when a concern transition cannot be represented safely."""


@dataclass(frozen=True)
class _PendingConcernEvent:
    concern: CarFault
    event_type: str
    actor_user_id: int | None
    previous_state: str | None
    new_state: str
    progression_direction: str
    occurred_at: datetime
    source: str
    idempotency_suffix: str


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _request_actor_user_id() -> int | None:
    if not has_request_context():
        return None
    if not current_user.is_authenticated:
        return None
    return int(current_user.id)


def _source_for(concern: CarFault) -> str:
    if has_request_context() and request.endpoint:
        return request.endpoint[:50]

    raw_source = (concern.source or "domain.car_fault").strip()
    return raw_source[:50] or "domain.car_fault"


def _normalise_datetime(value: datetime | None, *, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _actor_for_new_concern(concern: CarFault) -> int | None:
    return concern.reported_by or _request_actor_user_id()


def _actor_for_transition(concern: CarFault, new_state: str) -> int | None:
    if new_state == "under_review" and concern.reviewed_by:
        return concern.reviewed_by
    if new_state == "resolved" and concern.resolved_by:
        return concern.resolved_by
    return _request_actor_user_id()


def _plan_new_concern(concern: CarFault, now: datetime) -> list[_PendingConcernEvent]:
    actor_user_id = _actor_for_new_concern(concern)
    source = _source_for(concern)
    final_state = concern.status or "reported"
    reported_at = _normalise_datetime(concern.reported_at, fallback=now)

    events = [
        _PendingConcernEvent(
            concern=concern,
            event_type="concern.reported",
            actor_user_id=actor_user_id,
            previous_state=None,
            new_state="reported",
            progression_direction="insufficient_evidence",
            occurred_at=reported_at,
            source=source,
            idempotency_suffix="reported",
        )
    ]

    if final_state == "reported":
        return events

    if final_state == "under_review":
        review_at = _normalise_datetime(concern.reviewed_at, fallback=reported_at)
        events.append(
            _PendingConcernEvent(
                concern=concern,
                event_type="concern.review_started",
                actor_user_id=actor_user_id,
                previous_state="reported",
                new_state="under_review",
                progression_direction="insufficient_evidence",
                occurred_at=review_at,
                source=source,
                idempotency_suffix="review_started:initial",
            )
        )
        return events

    if final_state == "monitoring":
        events.append(
            _PendingConcernEvent(
                concern=concern,
                event_type="concern.monitoring_started",
                actor_user_id=actor_user_id,
                previous_state="reported",
                new_state="monitoring",
                progression_direction="stable",
                occurred_at=reported_at,
                source=source,
                idempotency_suffix="monitoring_started:initial",
            )
        )
        return events

    raise ReportedConcernIntegrationError(
        f"new reported concern cannot start in unsupported state {final_state!r}"
    )


def _plan_status_transition(
    concern: CarFault,
    *,
    previous_state: str,
    new_state: str,
    now: datetime,
) -> _PendingConcernEvent:
    source = _source_for(concern)
    actor_user_id = _actor_for_transition(concern, new_state)

    if previous_state == "reported" and new_state == "under_review":
        occurred_at = _normalise_datetime(concern.reviewed_at, fallback=now)
        event_type = "concern.review_started"
        direction = "insufficient_evidence"
    elif previous_state in {"reported", "under_review"} and new_state == "monitoring":
        occurred_at = now
        event_type = "concern.monitoring_started"
        direction = "stable"
    elif previous_state in {"reported", "under_review", "monitoring"} and new_state == "resolved":
        occurred_at = _normalise_datetime(concern.resolved_at, fallback=now)
        event_type = "concern.resolved"
        direction = "resolved"
    elif previous_state == "resolved" and new_state in {"reported", "under_review"}:
        occurred_at = now
        event_type = "concern.reopened"
        direction = "insufficient_evidence"
    else:
        raise ReportedConcernIntegrationError(
            f"unsupported reported concern transition: {previous_state!r} -> {new_state!r}"
        )

    transition_token = occurred_at.isoformat(timespec="microseconds")
    return _PendingConcernEvent(
        concern=concern,
        event_type=event_type,
        actor_user_id=actor_user_id,
        previous_state=previous_state,
        new_state=new_state,
        progression_direction=direction,
        occurred_at=occurred_at,
        source=source,
        idempotency_suffix=(
            f"{event_type}:{previous_state}->{new_state}:{transition_token}"
        ),
    )


def _persisted_status(session: Session, concern: CarFault) -> str | None:
    """Read the pre-flush status when SQLAlchemy expired the old attribute.

    Flask-SQLAlchemy expires objects after commit. A route normally reloads the
    concern before mutating it, but services/tests may assign a new status to an
    expired instance. In that case SQLAlchemy history contains the new value but
    not the deleted value. Reading through the current transaction connection
    recovers the persisted state without flushing the pending mutation.
    """

    if concern.id is None:
        return None

    return session.connection().execute(
        select(CarFault.__table__.c.status).where(
            CarFault.__table__.c.id == concern.id
        )
    ).scalar_one_or_none()


def _collect_pending_events(session: Session) -> list[_PendingConcernEvent]:
    now = _utcnow_naive()
    pending: list[_PendingConcernEvent] = []

    for obj in list(session.new):
        if isinstance(obj, CarFault):
            pending.extend(_plan_new_concern(obj, now))

    for obj in list(session.dirty):
        if not isinstance(obj, CarFault) or obj in session.new:
            continue

        history = inspect(obj).attrs.status.history
        if not history.has_changes():
            continue

        new_state = history.added[-1] if history.added else obj.status
        previous_state = (
            history.deleted[-1]
            if history.deleted
            else _persisted_status(session, obj)
        )

        if previous_state is None or new_state is None:
            raise ReportedConcernIntegrationError(
                "reported concern status transition is missing persisted state evidence"
            )

        if previous_state == new_state:
            continue

        pending.append(
            _plan_status_transition(
                obj,
                previous_state=previous_state,
                new_state=new_state,
                now=now,
            )
        )

    return pending


def _emit_pending_event(item: _PendingConcernEvent) -> None:
    concern = item.concern
    if concern.id is None:
        raise ReportedConcernIntegrationError(
            "reported concern did not receive a durable subject id before event emission"
        )

    if item.actor_user_id is None:
        raise ReportedConcernIntegrationError(
            "reported concern event has no authenticated or persisted human actor"
        )

    evidence_refs: list[dict[str, Any]] = [
        {"type": "reported_concern", "id": concern.id}
    ]
    data = {
        "category": concern.category or "observation",
        "reported_source": concern.source or "unknown",
    }

    emit_vehicle_event(
        car_id=concern.car_id,
        event_type=item.event_type,
        subject_type="reported_concern",
        subject_id=concern.id,
        actor_type="user",
        actor_user_id=item.actor_user_id,
        visibility="client",
        source=item.source,
        occurred_at=item.occurred_at,
        title={
            "concern.reported": "Reported concern recorded",
            "concern.review_started": "Concern review started",
            "concern.monitoring_started": "Concern monitoring started",
            "concern.resolved": "Concern resolved",
            "concern.reopened": "Concern reopened for review",
        }[item.event_type],
        progression_direction=item.progression_direction,
        idempotency_key=f"reported_concern:{concern.id}:{item.idempotency_suffix}",
        previous_state=item.previous_state,
        new_state=item.new_state,
        evidence_refs=evidence_refs,
        data=data,
        mileage=None,
    )


@event.listens_for(Session, "before_commit")
def emit_reported_concern_events_before_commit(session: Session) -> None:
    """Attach canonical events to every Aura Reported Concern transaction."""

    if session.info.get(_INTEGRATION_GUARD):
        return

    try:
        aura_session = db.session()
    except RuntimeError:
        return

    if session is not aura_session:
        return

    pending = _collect_pending_events(session)
    if not pending:
        return

    session.info[_INTEGRATION_GUARD] = True
    try:
        # New concerns need their database id before they can become canonical
        # event subjects. This is still inside the caller's transaction.
        session.flush()
        for item in pending:
            _emit_pending_event(item)
    finally:
        session.info.pop(_INTEGRATION_GUARD, None)
