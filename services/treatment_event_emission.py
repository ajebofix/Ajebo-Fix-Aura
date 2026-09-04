"""Canonical treatment event adapters for Aura Wave 2.3.

The durable VehicleEvent write remains owned by ``services.event_emission``.
This module registers Treatment Plan and Treatment Action event families with
that canonical emitter and enforces treatment-specific transition/authority
contracts before delegating. It deliberately does not commit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from security.access import resolve_vehicle_authority
from services import event_emission as canonical_events


TREATMENT_OUTCOME_DIRECTIONS = frozenset(
    {
        "improving",
        "stable",
        "deteriorating",
        "resolved",
        "insufficient_evidence",
    }
)

TREATMENT_PLAN_EVENT_TYPES = frozenset(
    {
        "treatment.proposed",
        "treatment.authorized",
        "treatment.scheduled",
        "treatment.started",
        "treatment.monitoring_started",
        "treatment.completed",
        "treatment.deferred",
        "treatment.cancelled",
        "treatment.escalated",
        "treatment.outcome_recorded",
    }
)

TREATMENT_ACTION_EVENT_TYPES = frozenset(
    {
        "treatment_action.created",
        "treatment_action.scheduled",
        "treatment_action.started",
        "treatment_action.completed",
        "treatment_action.deferred",
        "treatment_action.cancelled",
    }
)

_ADVISOR_ONLY_PLAN_EVENTS = frozenset(
    {
        "treatment.proposed",
        "treatment.scheduled",
        "treatment.started",
        "treatment.monitoring_started",
        "treatment.completed",
        "treatment.escalated",
        "treatment.outcome_recorded",
    }
)
_OWNER_ONLY_PLAN_EVENTS = frozenset({"treatment.authorized"})
_OWNER_OR_ADVISOR_PLAN_EVENTS = frozenset(
    {"treatment.deferred", "treatment.cancelled"}
)

_NONTERMINAL_PLAN_STATES = frozenset(
    {
        "proposed",
        "authorized",
        "scheduled",
        "in_progress",
        "monitoring",
        "deferred",
        "approved",  # legacy compatibility state only
    }
)
_OUTCOME_PLAN_STATES = frozenset({"in_progress", "monitoring", "completed"})


def _register_with_canonical_emitter() -> None:
    """Extend the single canonical emitter with Wave 2.3 treatment families."""

    canonical_events.TREATMENT_PLAN_EVENT_TYPES = TREATMENT_PLAN_EVENT_TYPES
    canonical_events.TREATMENT_ACTION_EVENT_TYPES = TREATMENT_ACTION_EVENT_TYPES
    canonical_events.CANONICAL_EVENT_TYPES = (
        canonical_events.CANONICAL_EVENT_TYPES
        | TREATMENT_PLAN_EVENT_TYPES
        | TREATMENT_ACTION_EVENT_TYPES
    )
    canonical_events._EVENT_SUBJECT_RULES.update(
        {event_type: "treatment_plan" for event_type in TREATMENT_PLAN_EVENT_TYPES}
    )
    canonical_events._EVENT_SUBJECT_RULES.update(
        {event_type: "treatment_action" for event_type in TREATMENT_ACTION_EVENT_TYPES}
    )

    plan_direction_rules = {
        event_type: frozenset({"not_applicable"})
        for event_type in TREATMENT_PLAN_EVENT_TYPES
        if event_type != "treatment.outcome_recorded"
    }
    plan_direction_rules["treatment.outcome_recorded"] = TREATMENT_OUTCOME_DIRECTIONS
    canonical_events._EVENT_DIRECTION_RULES.update(plan_direction_rules)
    canonical_events._EVENT_DIRECTION_RULES.update(
        {
            event_type: frozenset({"not_applicable"})
            for event_type in TREATMENT_ACTION_EVENT_TYPES
        }
    )

    plan_transition_events = TREATMENT_PLAN_EVENT_TYPES - {"treatment.proposed"}
    action_transition_events = TREATMENT_ACTION_EVENT_TYPES - {
        "treatment_action.created"
    }
    canonical_events._TRANSITION_EVENT_TYPES = (
        canonical_events._TRANSITION_EVENT_TYPES
        | plan_transition_events
        | action_transition_events
    )


_register_with_canonical_emitter()


class TreatmentEventError(canonical_events.EventEmissionError):
    """Raised when a canonical treatment event violates its family contract."""


class TreatmentEventAuthorityError(TreatmentEventError):
    """Raised when the treatment event actor lacks required authority."""


def _validate_plan_transition(
    *,
    event_type: str,
    previous_state: str | None,
    new_state: str | None,
    progression_direction: str,
) -> None:
    if event_type == "treatment.proposed":
        valid = previous_state is None and new_state == "proposed"
    elif event_type == "treatment.authorized":
        valid = previous_state in {"proposed", "deferred"} and new_state == "authorized"
    elif event_type == "treatment.scheduled":
        valid = (
            previous_state in {"authorized", "deferred", "approved"}
            and new_state == "scheduled"
        )
    elif event_type == "treatment.started":
        valid = (
            previous_state in {"authorized", "scheduled", "approved", "monitoring"}
            and new_state == "in_progress"
        )
    elif event_type == "treatment.monitoring_started":
        valid = previous_state == "in_progress" and new_state == "monitoring"
    elif event_type == "treatment.completed":
        valid = previous_state in {"in_progress", "monitoring"} and new_state == "completed"
    elif event_type == "treatment.deferred":
        valid = (
            previous_state in {"proposed", "authorized", "scheduled", "approved"}
            and new_state == "deferred"
        )
    elif event_type == "treatment.cancelled":
        valid = (
            previous_state
            in {"proposed", "authorized", "scheduled", "deferred", "approved"}
            and new_state == "cancelled"
        )
    elif event_type == "treatment.escalated":
        valid = (
            previous_state is not None
            and previous_state in _NONTERMINAL_PLAN_STATES
            and previous_state == new_state
        )
    elif event_type == "treatment.outcome_recorded":
        valid = (
            previous_state is not None
            and previous_state in _OUTCOME_PLAN_STATES
            and previous_state == new_state
            and progression_direction in TREATMENT_OUTCOME_DIRECTIONS
        )
    else:
        raise TreatmentEventError(f"unsupported treatment event type: {event_type}")

    if event_type != "treatment.outcome_recorded" and progression_direction != "not_applicable":
        valid = False

    if not valid:
        raise TreatmentEventError(
            f"invalid {event_type} transition: {previous_state!r} -> {new_state!r}"
        )


def _validate_action_transition(
    *,
    event_type: str,
    previous_state: str | None,
    new_state: str | None,
) -> None:
    if event_type == "treatment_action.created":
        valid = previous_state is None and new_state == "planned"
    elif event_type == "treatment_action.scheduled":
        valid = previous_state in {"planned", "deferred"} and new_state == "scheduled"
    elif event_type == "treatment_action.started":
        valid = previous_state == "scheduled" and new_state == "in_progress"
    elif event_type == "treatment_action.completed":
        valid = previous_state == "in_progress" and new_state == "completed"
    elif event_type == "treatment_action.deferred":
        valid = previous_state in {"planned", "scheduled"} and new_state == "deferred"
    elif event_type == "treatment_action.cancelled":
        valid = previous_state in {"planned", "scheduled", "deferred"} and new_state == "cancelled"
    else:
        raise TreatmentEventError(
            f"unsupported Treatment Action event type: {event_type}"
        )

    if not valid:
        raise TreatmentEventError(
            f"invalid {event_type} transition: {previous_state!r} -> {new_state!r}"
        )


def _authority(*, car_id: int, actor_user_id: int) -> str:
    authority = resolve_vehicle_authority(actor_user_id, car_id)
    if authority is None:
        raise TreatmentEventAuthorityError(
            "actor has no proven authority for this vehicle"
        )
    return authority


def _validate_plan_authority(
    *,
    car_id: int,
    event_type: str,
    actor_user_id: int,
) -> str:
    authority = _authority(car_id=car_id, actor_user_id=actor_user_id)

    if event_type in _ADVISOR_ONLY_PLAN_EVENTS and authority not in {
        "advisor",
        "administrator",
    }:
        raise TreatmentEventAuthorityError(
            f"{event_type} requires advisor authority"
        )

    if event_type in _OWNER_ONLY_PLAN_EVENTS and authority != "owner":
        raise TreatmentEventAuthorityError(
            f"{event_type} requires current owner authority"
        )

    if event_type in _OWNER_OR_ADVISOR_PLAN_EVENTS and authority not in {
        "owner",
        "advisor",
        "administrator",
    }:
        raise TreatmentEventAuthorityError(
            f"{event_type} requires owner or advisor authority"
        )

    return authority


def _validate_action_authority(*, car_id: int, actor_user_id: int) -> str:
    authority = _authority(car_id=car_id, actor_user_id=actor_user_id)
    if authority not in {"advisor", "administrator"}:
        raise TreatmentEventAuthorityError(
            "Treatment Action events require advisor authority"
        )
    return authority


def emit_treatment_plan_event(
    *,
    car_id: int,
    plan_id: int,
    event_type: str,
    actor_user_id: int,
    occurred_at: datetime,
    source: str,
    title: str,
    previous_state: str | None,
    new_state: str | None,
    idempotency_key: str,
    visibility: str = "client",
    data: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    description: str | None = None,
    progression_direction: str = "not_applicable",
    evidence_refs: list[dict[str, Any]] | None = None,
) -> Any:
    """Validate and emit one canonical Treatment Plan event."""

    if event_type not in TREATMENT_PLAN_EVENT_TYPES:
        raise TreatmentEventError(f"unsupported treatment event type: {event_type}")

    _validate_plan_transition(
        event_type=event_type,
        previous_state=previous_state,
        new_state=new_state,
        progression_direction=progression_direction,
    )
    _validate_plan_authority(
        car_id=car_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
    )

    return canonical_events.emit_vehicle_event(
        car_id=car_id,
        event_type=event_type,
        subject_type="treatment_plan",
        subject_id=plan_id,
        actor_type="user",
        actor_user_id=actor_user_id,
        visibility=visibility,
        source=source,
        occurred_at=occurred_at,
        title=title,
        progression_direction=progression_direction,
        idempotency_key=idempotency_key,
        description=description,
        previous_state=previous_state,
        new_state=new_state,
        correlation_id=correlation_id,
        causation_id=causation_id,
        evidence_refs=evidence_refs or [],
        data=data or {},
    )


def emit_treatment_action_event(
    *,
    car_id: int,
    action_id: int,
    event_type: str,
    actor_user_id: int,
    occurred_at: datetime,
    source: str,
    title: str,
    previous_state: str | None,
    new_state: str | None,
    idempotency_key: str,
    visibility: str = "client",
    data: dict[str, Any] | None = None,
    description: str | None = None,
) -> Any:
    """Validate and emit one canonical Treatment Action lifecycle fact."""

    if event_type not in TREATMENT_ACTION_EVENT_TYPES:
        raise TreatmentEventError(
            f"unsupported Treatment Action event type: {event_type}"
        )

    _validate_action_transition(
        event_type=event_type,
        previous_state=previous_state,
        new_state=new_state,
    )
    _validate_action_authority(
        car_id=car_id,
        actor_user_id=actor_user_id,
    )

    return canonical_events.emit_vehicle_event(
        car_id=car_id,
        event_type=event_type,
        subject_type="treatment_action",
        subject_id=action_id,
        actor_type="user",
        actor_user_id=actor_user_id,
        visibility=visibility,
        source=source,
        occurred_at=occurred_at,
        title=title,
        progression_direction="not_applicable",
        idempotency_key=idempotency_key,
        description=description,
        previous_state=previous_state,
        new_state=new_state,
        evidence_refs=[],
        data=data or {},
    )
