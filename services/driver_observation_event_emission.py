"""Canonical driver-observation event adapter for Aura Wave 2.4B.

The durable VehicleEvent write remains owned by ``services.event_emission``.
This adapter registers the driver-observation event family with that canonical
emitter, enforces the driver-only authority/state contract, and never commits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from security.access import VEHICLE_AUTHORITY_DRIVER, resolve_vehicle_authority
from services import event_emission as canonical_events


DRIVER_OBSERVATION_EVENT_TYPES = frozenset(
    {"driver_observation.checkin_recorded"}
)


def _register_with_canonical_emitter() -> None:
    """Extend the single canonical emitter with the Wave 2.4B family."""

    canonical_events.DRIVER_OBSERVATION_EVENT_TYPES = DRIVER_OBSERVATION_EVENT_TYPES
    canonical_events.CANONICAL_EVENT_TYPES = (
        canonical_events.CANONICAL_EVENT_TYPES | DRIVER_OBSERVATION_EVENT_TYPES
    )
    canonical_events._EVENT_SUBJECT_RULES.update(
        {
            event_type: "driver_checkin"
            for event_type in DRIVER_OBSERVATION_EVENT_TYPES
        }
    )
    canonical_events._EVENT_DIRECTION_RULES.update(
        {
            event_type: frozenset({"insufficient_evidence"})
            for event_type in DRIVER_OBSERVATION_EVENT_TYPES
        }
    )


_register_with_canonical_emitter()


class DriverObservationEventError(canonical_events.EventEmissionError):
    """Raised when a driver-observation event violates its family contract."""


class DriverObservationEventAuthorityError(DriverObservationEventError):
    """Raised when the actor is not the currently assigned driver."""


def emit_driver_checkin_recorded(
    *,
    car_id: int,
    checkin_id: int,
    actor_user_id: int,
    occurred_at: datetime,
    operational_date: str,
    tyre_warning: bool,
    fuel_low: bool,
    dashboard_light: bool,
    vibration: bool,
    unusual_sound: bool,
    notes_present: bool,
    idempotency_key: str,
) -> Any:
    """Emit one canonical driver check-in fact without committing."""

    authority = resolve_vehicle_authority(actor_user_id, car_id)
    if authority != VEHICLE_AUTHORITY_DRIVER:
        raise DriverObservationEventAuthorityError(
            "driver observation requires current assigned-driver authority"
        )

    return canonical_events.emit_vehicle_event(
        car_id=car_id,
        event_type="driver_observation.checkin_recorded",
        subject_type="driver_checkin",
        subject_id=checkin_id,
        actor_type="user",
        actor_user_id=actor_user_id,
        visibility="advisor",
        source="driver_checkin",
        occurred_at=occurred_at,
        title="Driver check-in recorded",
        progression_direction="insufficient_evidence",
        idempotency_key=idempotency_key,
        previous_state=None,
        new_state="recorded",
        evidence_refs=[],
        data={
            "operational_date": operational_date,
            "tyre_warning": bool(tyre_warning),
            "fuel_low": bool(fuel_low),
            "dashboard_light": bool(dashboard_light),
            "vibration": bool(vibration),
            "unusual_sound": bool(unusual_sound),
            "notes_present": bool(notes_present),
        },
    )
