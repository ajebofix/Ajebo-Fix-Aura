"""Canonical care-signal event adapter for Aura Wave 2.4C.

``VehicleHealthAlert`` remains the durable subject. This adapter registers the
care-signal family with Aura's single canonical ``VehicleEvent`` emitter,
enforces transition/authority rules, and never commits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from security.access import resolve_vehicle_authority
from services import event_emission as canonical_events


CARE_SIGNAL_EVENT_TYPES = frozenset(
    {
        "care_signal.raised",
        "care_signal.acknowledged",
        "care_signal.resolved",
    }
)
_SYSTEM_CARE_SIGNAL_EVENT_TYPES = frozenset(
    {"care_signal.raised", "care_signal.resolved"}
)
_ADVISOR_AUTHORITIES = frozenset({"advisor", "administrator"})


def _register_with_canonical_emitter() -> None:
    canonical_events.CARE_SIGNAL_EVENT_TYPES = CARE_SIGNAL_EVENT_TYPES
    canonical_events.CANONICAL_EVENT_TYPES = (
        canonical_events.CANONICAL_EVENT_TYPES | CARE_SIGNAL_EVENT_TYPES
    )
    canonical_events._EVENT_SUBJECT_RULES.update(
        {
            event_type: "vehicle_health_alert"
            for event_type in CARE_SIGNAL_EVENT_TYPES
        }
    )
    canonical_events._EVENT_DIRECTION_RULES.update(
        {
            event_type: frozenset({"not_applicable"})
            for event_type in CARE_SIGNAL_EVENT_TYPES
        }
    )
    canonical_events._TRANSITION_EVENT_TYPES = (
        canonical_events._TRANSITION_EVENT_TYPES
        | {"care_signal.acknowledged", "care_signal.resolved"}
    )
    canonical_events.SYSTEM_ACTOR_EVENT_TYPES = (
        canonical_events.SYSTEM_ACTOR_EVENT_TYPES
        | _SYSTEM_CARE_SIGNAL_EVENT_TYPES
    )


_register_with_canonical_emitter()


class CareSignalEventError(canonical_events.EventEmissionError):
    """Raised when a care-signal event violates the locked family contract."""


class CareSignalEventAuthorityError(CareSignalEventError):
    """Raised when the care-signal actor lacks the required authority."""


def _validate_transition(
    *,
    event_type: str,
    previous_state: str | None,
    new_state: str | None,
) -> None:
    if event_type == "care_signal.raised":
        valid = previous_state is None and new_state == "new"
    elif event_type == "care_signal.acknowledged":
        valid = previous_state == "new" and new_state == "acknowledged"
    elif event_type == "care_signal.resolved":
        valid = previous_state in {"new", "acknowledged"} and new_state == "resolved"
    else:
        raise CareSignalEventError(f"unsupported care-signal event: {event_type}")

    if not valid:
        raise CareSignalEventError(
            f"invalid {event_type} transition: {previous_state!r} -> {new_state!r}"
        )


def _validate_actor(
    *,
    car_id: int,
    event_type: str,
    actor_type: str,
    actor_user_id: int | None,
) -> None:
    if actor_type == "system":
        if event_type not in _SYSTEM_CARE_SIGNAL_EVENT_TYPES:
            raise CareSignalEventAuthorityError(
                "system actor may only raise or resolve approved deterministic care signals"
            )
        if actor_user_id is not None:
            raise CareSignalEventAuthorityError(
                "system care-signal events must not impersonate a human"
            )
        return

    if actor_type != "user" or actor_user_id is None:
        raise CareSignalEventAuthorityError(
            "care-signal human events require an authenticated user actor"
        )

    authority = resolve_vehicle_authority(actor_user_id, car_id)
    if authority not in _ADVISOR_AUTHORITIES:
        raise CareSignalEventAuthorityError(
            "professional care-signal transitions require advisor authority"
        )


def emit_care_signal_event(
    *,
    car_id: int,
    signal_id: int,
    event_type: str,
    actor_type: str,
    actor_user_id: int | None,
    occurred_at: datetime,
    previous_state: str | None,
    new_state: str,
    alert_type: str,
    severity: str,
    source_classification: str,
    idempotency_key: str,
) -> Any:
    """Validate and emit one bounded canonical care-signal lifecycle fact."""

    if event_type not in CARE_SIGNAL_EVENT_TYPES:
        raise CareSignalEventError(f"unsupported care-signal event: {event_type}")

    _validate_transition(
        event_type=event_type,
        previous_state=previous_state,
        new_state=new_state,
    )
    _validate_actor(
        car_id=car_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
    )

    visibility = "advisor" if event_type == "care_signal.acknowledged" else "client"
    title = {
        "care_signal.raised": "Care signal raised",
        "care_signal.acknowledged": "Care signal acknowledged",
        "care_signal.resolved": "Care signal resolved",
    }[event_type]

    return canonical_events.emit_vehicle_event(
        car_id=car_id,
        event_type=event_type,
        subject_type="vehicle_health_alert",
        subject_id=signal_id,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        visibility=visibility,
        source="care_signal",
        occurred_at=occurred_at,
        title=title,
        progression_direction="not_applicable",
        idempotency_key=idempotency_key,
        previous_state=previous_state,
        new_state=new_state,
        evidence_refs=[],
        data={
            "alert_type": alert_type,
            "severity": severity,
            "source_classification": source_classification,
        },
        severity=severity,
    )
