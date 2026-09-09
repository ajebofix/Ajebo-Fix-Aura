"""Canonical priority-request event adapter for Aura Wave 2.4D."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from security.access import resolve_vehicle_authority
from services import event_emission as canonical_events


PRIORITY_EVENT_TYPES = frozenset(
    {
        "priority.requested",
        "priority.review_started",
        "priority.accepted",
        "priority.deferred",
        "priority.resolved",
        "priority.cancelled",
    }
)
_ADVISOR_AUTHORITIES = frozenset({"advisor", "administrator"})


def _register_with_canonical_emitter() -> None:
    canonical_events.PRIORITY_EVENT_TYPES = PRIORITY_EVENT_TYPES
    canonical_events.CANONICAL_EVENT_TYPES = (
        canonical_events.CANONICAL_EVENT_TYPES | PRIORITY_EVENT_TYPES
    )
    canonical_events._EVENT_SUBJECT_RULES.update(
        {event_type: "priority_request" for event_type in PRIORITY_EVENT_TYPES}
    )
    canonical_events._EVENT_DIRECTION_RULES.update(
        {
            event_type: frozenset({"not_applicable"})
            for event_type in PRIORITY_EVENT_TYPES
        }
    )
    canonical_events._TRANSITION_EVENT_TYPES = (
        canonical_events._TRANSITION_EVENT_TYPES
        | {
            "priority.review_started",
            "priority.accepted",
            "priority.deferred",
            "priority.resolved",
            "priority.cancelled",
        }
    )


_register_with_canonical_emitter()


class PriorityEventError(canonical_events.EventEmissionError):
    """Raised when a priority event violates the locked family contract."""


class PriorityEventAuthorityError(PriorityEventError):
    """Raised when the actor lacks authority for a priority transition."""


def _validate_transition(
    *,
    event_type: str,
    previous_state: str | None,
    new_state: str | None,
) -> None:
    allowed = {
        "priority.requested": {(None, "requested")},
        "priority.review_started": {
            ("requested", "under_review"),
            ("deferred", "under_review"),
        },
        "priority.accepted": {("under_review", "accepted")},
        "priority.deferred": {
            ("requested", "deferred"),
            ("under_review", "deferred"),
        },
        "priority.resolved": {("accepted", "resolved")},
        "priority.cancelled": {
            ("requested", "cancelled"),
            ("under_review", "cancelled"),
            ("deferred", "cancelled"),
        },
    }
    if (previous_state, new_state) not in allowed.get(event_type, set()):
        raise PriorityEventError(
            f"invalid {event_type} transition: {previous_state!r} -> {new_state!r}"
        )


def _validate_actor(
    *,
    car_id: int,
    event_type: str,
    actor_user_id: int | None,
) -> str:
    if actor_user_id is None:
        raise PriorityEventAuthorityError("priority events require a human actor")

    authority = resolve_vehicle_authority(actor_user_id, car_id)
    if event_type == "priority.requested":
        if authority not in {"owner", "advisor", "administrator"}:
            raise PriorityEventAuthorityError(
                "priority requests require owner or advisor authority"
            )
    elif authority not in _ADVISOR_AUTHORITIES:
        raise PriorityEventAuthorityError(
            "professional priority transitions require advisor authority"
        )
    return authority or "unknown"


def emit_priority_event(
    *,
    car_id: int,
    request_id: int,
    event_type: str,
    actor_user_id: int,
    occurred_at: datetime,
    previous_state: str | None,
    new_state: str,
    request_kind: str,
    request_source: str,
    eligibility_at_request: bool,
    idempotency_key: str,
    consultation_id: int | None = None,
) -> Any:
    if event_type not in PRIORITY_EVENT_TYPES:
        raise PriorityEventError(f"unsupported priority event: {event_type}")

    _validate_transition(
        event_type=event_type,
        previous_state=previous_state,
        new_state=new_state,
    )
    authority = _validate_actor(
        car_id=car_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
    )

    visibility = "advisor" if event_type == "priority.review_started" else "client"
    title = {
        "priority.requested": "Priority request submitted",
        "priority.review_started": "Priority request under review",
        "priority.accepted": "Priority request accepted",
        "priority.deferred": "Priority request deferred",
        "priority.resolved": "Priority request resolved",
        "priority.cancelled": "Priority request cancelled",
    }[event_type]

    data = {
        "request_kind": request_kind,
        "request_source": request_source,
        "eligibility_at_request": bool(eligibility_at_request),
    }
    if consultation_id is not None:
        data["consultation_id"] = consultation_id

    return canonical_events.emit_vehicle_event(
        car_id=car_id,
        event_type=event_type,
        subject_type="priority_request",
        subject_id=request_id,
        actor_type="user",
        actor_user_id=actor_user_id,
        visibility=visibility,
        source="priority",
        occurred_at=occurred_at,
        title=title,
        progression_direction="not_applicable",
        idempotency_key=idempotency_key,
        previous_state=previous_state,
        new_state=new_state,
        evidence_refs=[],
        data=data,
        severity="high" if request_kind == "emergency_review" else "moderate",
    )
