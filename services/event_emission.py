"""Canonical VehicleEvent emission service.

This module owns creation of canonical events. It deliberately does not commit
the SQLAlchemy session: the domain mutation and its event must remain in the
caller's transaction so they succeed or fail together.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any

from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Car, CarOwnership, VehicleEvent
from security.access import resolve_vehicle_authority


logger = logging.getLogger(__name__)

CANONICAL_EVENT_SCHEMA_VERSION = 1

CONCERN_EVENT_TYPES = frozenset(
    {
        "concern.reported",
        "concern.review_started",
        "concern.monitoring_started",
        "concern.resolved",
        "concern.reopened",
        "concern.corrected",
    }
)
EVIDENCE_EVENT_TYPES = frozenset({"evidence.reviewed", "evidence.linked"})
CONSULTATION_EVENT_TYPES = frozenset(
    {
        "consultation.requested",
        "consultation.scheduled",
        "consultation.started",
        "consultation.completed",
    }
)
CANONICAL_EVENT_TYPES = (
    CONCERN_EVENT_TYPES | EVIDENCE_EVENT_TYPES | CONSULTATION_EVENT_TYPES
)

ALLOWED_VISIBILITIES = frozenset({"client", "advisor", "internal"})
ALLOWED_PROGRESSION_DIRECTIONS = frozenset(
    {
        "improving",
        "stable",
        "deteriorating",
        "recurring",
        "resolved",
        "insufficient_evidence",
        "not_applicable",
    }
)
RESERVED_ACTOR_TYPES = frozenset({"system", "provider"})

_EVENT_SUBJECT_RULES = {
    **{event_type: "reported_concern" for event_type in CONCERN_EVENT_TYPES},
    **{event_type: "vehicle_evidence" for event_type in EVIDENCE_EVENT_TYPES},
    **{event_type: "consultation" for event_type in CONSULTATION_EVENT_TYPES},
}

_EVENT_DIRECTION_RULES = {
    "concern.reported": frozenset({"insufficient_evidence"}),
    "concern.review_started": frozenset({"stable", "insufficient_evidence"}),
    "concern.monitoring_started": frozenset({"stable", "insufficient_evidence"}),
    "concern.resolved": frozenset({"resolved"}),
    "concern.reopened": frozenset({"recurring", "insufficient_evidence"}),
    "concern.corrected": frozenset({"not_applicable"}),
    "evidence.reviewed": frozenset({"not_applicable"}),
    "evidence.linked": frozenset({"not_applicable"}),
    "consultation.requested": frozenset({"not_applicable"}),
    "consultation.scheduled": frozenset({"not_applicable"}),
    "consultation.started": frozenset({"not_applicable"}),
    "consultation.completed": frozenset({"not_applicable"}),
}

_TRANSITION_EVENT_TYPES = frozenset(
    {
        "concern.review_started",
        "concern.monitoring_started",
        "concern.resolved",
        "concern.reopened",
        "evidence.reviewed",
    }
)

_FORBIDDEN_PAYLOAD_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "prompt",
)
_MAX_DATA_BYTES = 16 * 1024
_MAX_EVIDENCE_BYTES = 12 * 1024


class EventEmissionError(ValueError):
    """Base validation error for canonical event emission."""


class EventAuthorityError(EventEmissionError):
    """Raised when an actor has no proven authority for the vehicle."""


class EventIdempotencyConflict(EventEmissionError):
    """Raised when an idempotency key is replayed with different semantics."""


def _normalise_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise EventEmissionError("occurred_at must be a datetime")

    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    return value


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _assert_safe_json(value: Any, *, label: str, max_bytes: int) -> None:
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                key_text = str(key).strip().lower()
                if any(
                    fragment in key_text
                    for fragment in _FORBIDDEN_PAYLOAD_KEY_FRAGMENTS
                ):
                    raise EventEmissionError(
                        f"{label} contains a prohibited sensitive key: {key}"
                    )
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EventEmissionError(f"{label} must be JSON serializable") from exc

    if len(encoded) > max_bytes:
        raise EventEmissionError(f"{label} exceeds the canonical event size limit")


def _active_ownership_for(car_id: int) -> CarOwnership:
    ownerships = (
        CarOwnership.query.filter_by(car_id=car_id, is_active=True)
        .order_by(CarOwnership.start_date.desc(), CarOwnership.id.desc())
        .all()
    )

    if not ownerships:
        raise EventEmissionError(
            "canonical events currently require one active vehicle ownership"
        )

    if len(ownerships) > 1:
        raise EventEmissionError(
            "vehicle has multiple active ownership rows; resolve stewardship before emitting"
        )

    return ownerships[0]


def _fingerprint(
    *,
    car_id: int,
    subject_type: str,
    subject_id: int,
    event_type: str,
    idempotency_key: str,
) -> str:
    namespace = "|".join(
        (
            f"schema:{CANONICAL_EVENT_SCHEMA_VERSION}",
            f"car:{car_id}",
            f"subject:{subject_type}:{subject_id}",
            f"event:{event_type}",
            f"key:{idempotency_key}",
        )
    )
    return hashlib.sha256(namespace.encode("utf-8")).hexdigest()


def _validate_event_contract(
    *,
    event_type: str,
    subject_type: str,
    subject_id: int,
    actor_type: str,
    actor_user_id: int | None,
    visibility: str,
    source: str,
    title: str,
    progression_direction: str,
    previous_state: str | None,
    new_state: str | None,
    correction_of_event_id: int | None,
    idempotency_key: str,
) -> None:
    if event_type not in CANONICAL_EVENT_TYPES:
        raise EventEmissionError(f"unsupported canonical event type: {event_type}")

    expected_subject_type = _EVENT_SUBJECT_RULES[event_type]
    if subject_type != expected_subject_type:
        raise EventEmissionError(
            f"{event_type} requires subject_type='{expected_subject_type}'"
        )

    if not isinstance(subject_id, int) or subject_id <= 0:
        raise EventEmissionError("subject_id must identify a persisted domain row")

    if actor_type in RESERVED_ACTOR_TYPES:
        raise EventEmissionError(
            "system/provider actors remain reserved until legacy created_by nullability is relaxed"
        )

    if actor_type != "user" or not isinstance(actor_user_id, int):
        raise EventEmissionError(
            "canonical human events require actor_type='user' and actor_user_id"
        )

    if visibility not in ALLOWED_VISIBILITIES:
        raise EventEmissionError(f"invalid canonical event visibility: {visibility}")

    if not source or len(source) > 50:
        raise EventEmissionError("source is required and must be 50 characters or fewer")

    if not title or len(title) > 120:
        raise EventEmissionError("title is required and must be 120 characters or fewer")

    if not idempotency_key or len(idempotency_key) > 256:
        raise EventEmissionError(
            "idempotency_key is required and must be 256 characters or fewer"
        )

    if progression_direction not in ALLOWED_PROGRESSION_DIRECTIONS:
        raise EventEmissionError(
            f"invalid progression direction: {progression_direction}"
        )

    allowed_directions = _EVENT_DIRECTION_RULES[event_type]
    if progression_direction not in allowed_directions:
        raise EventEmissionError(
            f"{event_type} cannot use progression direction {progression_direction}"
        )

    if event_type in _TRANSITION_EVENT_TYPES and (
        previous_state is None or new_state is None
    ):
        raise EventEmissionError(
            f"{event_type} requires previous_state and new_state"
        )

    if event_type == "concern.reported" and (
        previous_state is not None or new_state != "reported"
    ):
        raise EventEmissionError(
            "concern.reported requires previous_state=None and new_state='reported'"
        )

    if event_type == "evidence.reviewed" and (
        previous_state != "pending_review"
        or new_state not in {"accepted", "rejected"}
    ):
        raise EventEmissionError(
            "evidence.reviewed requires pending_review -> accepted/rejected"
        )

    if event_type == "evidence.linked" and (
        previous_state is not None or new_state is not None
    ):
        raise EventEmissionError("evidence.linked is not a state transition")

    if event_type == "consultation.requested" and (
        previous_state is not None or new_state != "requested"
    ):
        raise EventEmissionError(
            "consultation.requested requires previous_state=None and new_state='requested'"
        )

    if event_type == "consultation.scheduled" and (
        previous_state not in {None, "requested", "deferred"}
        or new_state != "scheduled"
    ):
        raise EventEmissionError(
            "consultation.scheduled requires none/requested/deferred -> scheduled"
        )

    if event_type == "consultation.started" and (
        previous_state != "scheduled" or new_state != "in_progress"
    ):
        raise EventEmissionError(
            "consultation.started requires scheduled -> in_progress"
        )

    if event_type == "consultation.completed" and (
        previous_state != "in_progress" or new_state != "completed"
    ):
        raise EventEmissionError(
            "consultation.completed requires in_progress -> completed"
        )

    if event_type == "concern.corrected" and correction_of_event_id is None:
        raise EventEmissionError(
            "concern.corrected requires correction_of_event_id"
        )

    if event_type != "concern.corrected" and correction_of_event_id is not None:
        raise EventEmissionError(
            "correction_of_event_id is only valid for concern.corrected"
        )


def _same_semantics(
    event: VehicleEvent,
    *,
    ownership_id: int,
    occurred_at: datetime,
    actor_user_id: int,
    actor_authority: str,
    visibility: str,
    source: str,
    title: str,
    description: str | None,
    previous_state: str | None,
    new_state: str | None,
    progression_direction: str,
    correlation_id: str | None,
    causation_id: str | None,
    evidence_refs: list[dict[str, Any]],
    correction_of_event_id: int | None,
    data: dict[str, Any],
    mileage: int | None,
) -> bool:
    return all(
        (
            event.ownership_id == ownership_id,
            event.occurred_at == occurred_at,
            event.actor_user_id == actor_user_id,
            event.actor_authority == actor_authority,
            event.visibility == visibility,
            event.source == source,
            event.title == title,
            event.description == description,
            event.previous_state == previous_state,
            event.new_state == new_state,
            event.progression_direction == progression_direction,
            event.correlation_id == correlation_id,
            event.causation_id == causation_id,
            (event.evidence_refs or []) == evidence_refs,
            event.correction_of_event_id == correction_of_event_id,
            (event.data or {}) == data,
            event.mileage == mileage,
        )
    )


def _flush_new_event(
    event: VehicleEvent,
    *,
    fingerprint: str,
    semantic_kwargs: dict[str, Any],
) -> VehicleEvent:
    """Flush one event while preserving caller-owned transaction semantics.

    PostgreSQL uses a SAVEPOINT so a concurrent unique-fingerprint collision
    can be recovered without invalidating the caller's outer transaction.
    SQLite is only Aura's local/test compatibility dialect; its SAVEPOINT
    release can escape a deferred outer transaction, so it deliberately uses a
    plain flush. Production concurrency guarantees are exercised on PostgreSQL.
    """

    dialect_name = db.session.get_bind().dialect.name

    if dialect_name == "sqlite":
        db.session.add(event)
        db.session.flush()
        return event

    try:
        with db.session.begin_nested():
            db.session.add(event)
            db.session.flush()
    except IntegrityError:
        existing = VehicleEvent.query.filter_by(fingerprint=fingerprint).first()
        if existing is None:
            raise
        if not _same_semantics(existing, **semantic_kwargs):
            raise EventIdempotencyConflict(
                "concurrent idempotency replay used different event semantics"
            )
        return existing

    return event


def emit_vehicle_event(
    *,
    car_id: int,
    event_type: str,
    subject_type: str,
    subject_id: int,
    actor_type: str,
    actor_user_id: int | None,
    visibility: str,
    source: str,
    occurred_at: datetime,
    title: str,
    progression_direction: str,
    idempotency_key: str,
    description: str | None = None,
    previous_state: str | None = None,
    new_state: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    correction_of_event_id: int | None = None,
    data: dict[str, Any] | None = None,
    mileage: int | None = None,
    severity: str = "low",
) -> VehicleEvent:
    """Create or idempotently return one canonical VehicleEvent.

    The function flushes but never commits. Callers own the surrounding
    transaction so a domain mutation cannot succeed while its event silently
    fails. Family-specific authority rules are enforced after vehicle authority
    is resolved.
    """

    _validate_event_contract(
        event_type=event_type,
        subject_type=subject_type,
        subject_id=subject_id,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        visibility=visibility,
        source=source,
        title=title,
        progression_direction=progression_direction,
        previous_state=previous_state,
        new_state=new_state,
        correction_of_event_id=correction_of_event_id,
        idempotency_key=idempotency_key,
    )

    occurred_at = _normalise_datetime(occurred_at)
    evidence_refs = evidence_refs or []
    data = data or {}

    _assert_safe_json(
        evidence_refs,
        label="evidence_refs",
        max_bytes=_MAX_EVIDENCE_BYTES,
    )
    _assert_safe_json(data, label="data", max_bytes=_MAX_DATA_BYTES)

    car = db.session.get(Car, car_id)
    if car is None:
        raise EventEmissionError("vehicle does not exist")

    ownership = _active_ownership_for(car_id)

    if actor_user_id is None:
        raise EventEmissionError("canonical human event is missing actor_user_id")

    actor_authority = resolve_vehicle_authority(actor_user_id, car_id)
    if actor_authority is None:
        raise EventAuthorityError("actor has no proven authority for this vehicle")

    if event_type in EVIDENCE_EVENT_TYPES and actor_authority not in {
        "advisor",
        "administrator",
    }:
        raise EventAuthorityError(
            "canonical evidence review/link events require advisor authority"
        )

    if event_type == "consultation.requested" and actor_authority != "owner":
        raise EventAuthorityError(
            "consultation requests require current owner authority"
        )

    if event_type in {
        "consultation.scheduled",
        "consultation.started",
        "consultation.completed",
    } and actor_authority not in {"advisor", "administrator"}:
        raise EventAuthorityError(
            "professional consultation transitions require advisor authority"
        )

    if correction_of_event_id is not None:
        corrected_event = db.session.get(VehicleEvent, correction_of_event_id)
        if corrected_event is None:
            raise EventEmissionError("correction target event does not exist")
        if corrected_event.car_id != car_id:
            raise EventEmissionError("correction target belongs to another vehicle")
        if (
            corrected_event.subject_type != subject_type
            or corrected_event.subject_id != subject_id
        ):
            raise EventEmissionError(
                "correction target does not match the canonical event subject"
            )

    fingerprint = _fingerprint(
        car_id=car_id,
        subject_type=subject_type,
        subject_id=subject_id,
        event_type=event_type,
        idempotency_key=idempotency_key,
    )

    semantic_kwargs = {
        "ownership_id": ownership.id,
        "occurred_at": occurred_at,
        "actor_user_id": actor_user_id,
        "actor_authority": actor_authority,
        "visibility": visibility,
        "source": source,
        "title": title,
        "description": description,
        "previous_state": previous_state,
        "new_state": new_state,
        "progression_direction": progression_direction,
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "evidence_refs": evidence_refs,
        "correction_of_event_id": correction_of_event_id,
        "data": data,
        "mileage": mileage,
    }

    existing = VehicleEvent.query.filter_by(fingerprint=fingerprint).first()
    if existing is not None:
        if not _same_semantics(existing, **semantic_kwargs):
            raise EventIdempotencyConflict(
                "idempotency key was already used with different event semantics"
            )
        return existing

    event = VehicleEvent(
        car_id=car_id,
        ownership_id=ownership.id,
        event_type=event_type,
        severity=severity,
        event_date=occurred_at.date(),
        title=title,
        description=description,
        mileage=mileage,
        source=source,
        data=data,
        fingerprint=fingerprint,
        schema_version=CANONICAL_EVENT_SCHEMA_VERSION,
        occurred_at=occurred_at,
        recorded_at=_utcnow_naive(),
        subject_type=subject_type,
        subject_id=subject_id,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        actor_authority=actor_authority,
        visibility=visibility,
        previous_state=previous_state,
        new_state=new_state,
        progression_direction=progression_direction,
        correlation_id=correlation_id,
        causation_id=causation_id,
        evidence_refs=evidence_refs,
        correction_of_event_id=correction_of_event_id,
        created_by=actor_user_id,
    )

    event = _flush_new_event(
        event,
        fingerprint=fingerprint,
        semantic_kwargs=semantic_kwargs,
    )

    logger.info(
        "canonical_vehicle_event_emitted event_id=%s car_id=%s event_type=%s subject_type=%s subject_id=%s actor_authority=%s visibility=%s",
        event.id,
        car_id,
        event_type,
        subject_type,
        subject_id,
        actor_authority,
        visibility,
    )

    return event
