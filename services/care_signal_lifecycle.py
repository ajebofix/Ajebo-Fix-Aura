"""Transactional VehicleHealthAlert lifecycle for Aura Wave 2.4C.

Care signals are operational monitoring facts. This service owns state legality
but deliberately never commits; callers coordinate the domain mutation and its
canonical VehicleEvent in one outer transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Car, CarOwnership, VehicleEvent, VehicleHealthAlert
from security.access import resolve_vehicle_authority
from services.care_signal_event_emission import emit_care_signal_event


DETERMINISTIC_SYSTEM_SIGNAL_TYPES = frozenset(
    {
        "low_health_status",
        "declining_health_trajectory",
        "maintenance_monitoring",
    }
)
_ADVISOR_AUTHORITIES = frozenset({"advisor", "administrator"})
_VALID_STATES = frozenset({"new", "acknowledged", "resolved"})


class CareSignalError(ValueError):
    """Base error for care-signal lifecycle operations."""


class CareSignalAuthorityError(CareSignalError):
    """Raised when an actor cannot perform the requested transition."""


class CareSignalStateError(CareSignalError):
    """Raised when a transition would violate the locked state contract."""


class CareSignalScopeError(CareSignalError):
    """Raised when a signal no longer belongs to the active stewardship scope."""


class CareSignalIdempotencyConflict(CareSignalError):
    """Raised when a repeated operation changes durable signal semantics."""


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalise_time(value: datetime | None) -> datetime:
    if value is None:
        return _utcnow_naive()
    if not isinstance(value, datetime):
        raise CareSignalError("occurred_at must be a datetime")
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _active_ownership(car_id: int) -> CarOwnership:
    ownerships = (
        CarOwnership.query.filter_by(car_id=car_id, is_active=True)
        .order_by(CarOwnership.start_date.desc(), CarOwnership.id.desc())
        .all()
    )
    if len(ownerships) != 1:
        raise CareSignalScopeError(
            "care-signal lifecycle requires exactly one active vehicle stewardship row"
        )
    return ownerships[0]


def _require_advisor(actor_user_id: int, car_id: int) -> None:
    authority = resolve_vehicle_authority(actor_user_id, car_id)
    if authority not in _ADVISOR_AUTHORITIES:
        raise CareSignalAuthorityError(
            "care-signal professional transitions require advisor authority"
        )


def _validate_actor(
    *,
    car_id: int,
    alert_type: str,
    actor_type: str,
    actor_user_id: int | None,
    allow_system: bool,
) -> None:
    if actor_type == "system":
        if not allow_system:
            raise CareSignalAuthorityError(
                "system actor is not allowed for this care-signal transition"
            )
        if actor_user_id is not None:
            raise CareSignalAuthorityError("system actor cannot impersonate a user")
        if alert_type not in DETERMINISTIC_SYSTEM_SIGNAL_TYPES:
            raise CareSignalAuthorityError(
                "system actor may mutate only approved deterministic care-signal types"
            )
        return

    if actor_type != "user" or not isinstance(actor_user_id, int):
        raise CareSignalAuthorityError("care-signal human transition requires a user")
    _require_advisor(actor_user_id, car_id)


def _validate_text(value: str, *, field: str, limit: int) -> str:
    clean = (value or "").strip()
    if not clean or len(clean) > limit:
        raise CareSignalError(f"{field} is required and must be {limit} characters or fewer")
    return clean


def _assert_signal_scope(signal: VehicleHealthAlert) -> CarOwnership:
    ownership = _active_ownership(signal.car_id)
    if signal.ownership_id != ownership.id:
        raise CareSignalScopeError(
            "care signal does not belong to the vehicle's active stewardship"
        )
    return ownership


def _raised_event(signal_id: int) -> VehicleEvent | None:
    return VehicleEvent.query.filter_by(
        subject_type="vehicle_health_alert",
        subject_id=signal_id,
        event_type="care_signal.raised",
    ).first()


class CareSignalLifecycleService:
    """Single mutation authority for durable VehicleHealthAlert occurrences."""

    @staticmethod
    def raise_signal(
        *,
        car_id: int,
        alert_type: str,
        severity: str,
        message: str,
        source_classification: str,
        actor_type: str = "system",
        actor_user_id: int | None = None,
        occurred_at: datetime | None = None,
    ) -> VehicleHealthAlert:
        if db.session.get(Car, car_id) is None:
            raise CareSignalScopeError("vehicle does not exist")

        alert_type = _validate_text(alert_type, field="alert_type", limit=50)
        severity = _validate_text(severity, field="severity", limit=20)
        message = _validate_text(message, field="message", limit=2000)
        source_classification = _validate_text(
            source_classification,
            field="source_classification",
            limit=50,
        )
        occurred_at = _normalise_time(occurred_at)
        ownership = _active_ownership(car_id)
        _validate_actor(
            car_id=car_id,
            alert_type=alert_type,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            allow_system=True,
        )

        existing = VehicleHealthAlert.query.filter_by(
            car_id=car_id,
            ownership_id=ownership.id,
            alert_type=alert_type,
            is_active=True,
        ).first()
        if existing is not None:
            if existing.severity != severity or existing.message != message:
                raise CareSignalIdempotencyConflict(
                    "active care signal already exists with different semantics"
                )
            if existing.status not in {"new", "acknowledged"}:
                raise CareSignalStateError(
                    "active care signal has an invalid semantic state"
                )
            # Existing pre-2.4C rows remain historical data. Never manufacture a
            # missing care_signal.raised event merely because the rule runs again.
            return existing

        signal = VehicleHealthAlert(
            car_id=car_id,
            ownership_id=ownership.id,
            alert_type=alert_type,
            severity=severity,
            status="new",
            message=message,
            is_active=True,
            created_at=occurred_at,
            resolved_at=None,
            acknowledged_at=None,
            acknowledged_by_id=None,
        )

        dialect = db.session.get_bind().dialect.name
        if dialect == "postgresql":
            try:
                with db.session.begin_nested():
                    db.session.add(signal)
                    db.session.flush()
            except IntegrityError:
                winner = VehicleHealthAlert.query.filter_by(
                    car_id=car_id,
                    ownership_id=ownership.id,
                    alert_type=alert_type,
                    is_active=True,
                ).first()
                if winner is None:
                    raise
                if winner.severity != severity or winner.message != message:
                    raise CareSignalIdempotencyConflict(
                        "concurrent care-signal raise used different semantics"
                    )
                return winner
        else:
            db.session.add(signal)
            db.session.flush()

        emit_care_signal_event(
            car_id=car_id,
            signal_id=signal.id,
            event_type="care_signal.raised",
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            previous_state=None,
            new_state="new",
            alert_type=alert_type,
            severity=severity,
            source_classification=source_classification,
            idempotency_key=f"care-signal:{signal.id}:raised",
        )
        return signal

    @staticmethod
    def acknowledge(
        *,
        signal_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
    ) -> VehicleHealthAlert:
        signal = db.session.get(VehicleHealthAlert, signal_id)
        if signal is None:
            raise CareSignalScopeError("care signal does not exist")
        _assert_signal_scope(signal)
        _require_advisor(actor_user_id, signal.car_id)

        if signal.status not in _VALID_STATES:
            raise CareSignalStateError("care signal has an unsupported state")
        if signal.status == "resolved" or not signal.is_active:
            raise CareSignalStateError("resolved care-signal occurrences are terminal")
        if signal.status == "acknowledged":
            return signal
        if signal.status != "new":
            raise CareSignalStateError("only a new care signal may be acknowledged")

        occurred_at = _normalise_time(occurred_at)
        signal.status = "acknowledged"
        signal.acknowledged_at = occurred_at
        signal.acknowledged_by_id = actor_user_id

        emit_care_signal_event(
            car_id=signal.car_id,
            signal_id=signal.id,
            event_type="care_signal.acknowledged",
            actor_type="user",
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            previous_state="new",
            new_state="acknowledged",
            alert_type=signal.alert_type,
            severity=signal.severity,
            source_classification="advisor_review",
            idempotency_key=f"care-signal:{signal.id}:acknowledged",
        )
        return signal

    @staticmethod
    def resolve(
        *,
        signal_id: int,
        actor_type: str,
        actor_user_id: int | None = None,
        source_classification: str,
        occurred_at: datetime | None = None,
    ) -> VehicleHealthAlert:
        signal = db.session.get(VehicleHealthAlert, signal_id)
        if signal is None:
            raise CareSignalScopeError("care signal does not exist")
        _assert_signal_scope(signal)
        _validate_actor(
            car_id=signal.car_id,
            alert_type=signal.alert_type,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            allow_system=True,
        )
        source_classification = _validate_text(
            source_classification,
            field="source_classification",
            limit=50,
        )

        if signal.status not in _VALID_STATES:
            raise CareSignalStateError("care signal has an unsupported state")
        if signal.status == "resolved":
            return signal
        if not signal.is_active or signal.status not in {"new", "acknowledged"}:
            raise CareSignalStateError("care signal cannot transition to resolved")

        previous_state = signal.status
        occurred_at = _normalise_time(occurred_at)
        signal.status = "resolved"
        signal.is_active = False
        signal.resolved_at = occurred_at

        emit_care_signal_event(
            car_id=signal.car_id,
            signal_id=signal.id,
            event_type="care_signal.resolved",
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            previous_state=previous_state,
            new_state="resolved",
            alert_type=signal.alert_type,
            severity=signal.severity,
            source_classification=source_classification,
            idempotency_key=f"care-signal:{signal.id}:resolved",
        )
        return signal

    @staticmethod
    def resolve_active_system_signal(
        *,
        car_id: int,
        alert_type: str,
        source_classification: str,
        occurred_at: datetime | None = None,
    ) -> VehicleHealthAlert | None:
        ownership = _active_ownership(car_id)
        signal = VehicleHealthAlert.query.filter_by(
            car_id=car_id,
            ownership_id=ownership.id,
            alert_type=alert_type,
            is_active=True,
        ).first()
        if signal is None:
            return None

        return CareSignalLifecycleService.resolve(
            signal_id=signal.id,
            actor_type="system",
            actor_user_id=None,
            source_classification=source_classification,
            occurred_at=occurred_at,
        )