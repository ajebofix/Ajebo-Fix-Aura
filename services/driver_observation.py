"""Driver observation lifecycle for Aura Wave 2.4B.

Driver check-ins are additive operational observations. They do not diagnose a
vehicle, classify deterioration, resolve concerns, or mutate driver gamification
scores. This service owns check-in creation/idempotency but deliberately leaves
the outer transaction commit to its caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy.exc import IntegrityError

from extensions import db
from models import DriverCheckIn, VehicleEvent
from security.access import VEHICLE_AUTHORITY_DRIVER, resolve_vehicle_authority
from services.driver_observation_event_emission import emit_driver_checkin_recorded


class DriverObservationError(ValueError):
    """Base error for driver-observation recording."""


class DriverObservationAuthorityError(DriverObservationError):
    """Raised when the actor is not the currently assigned driver."""


class DriverObservationConflict(DriverObservationError):
    """Raised when a different observation already exists for the same day."""


class DriverObservationValidationError(DriverObservationError):
    """Raised when submitted observation data violates the bounded contract."""


@dataclass(frozen=True)
class DriverObservationResult:
    checkin: DriverCheckIn
    event: VehicleEvent | None
    created: bool


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalise_datetime(value: datetime | None) -> datetime:
    value = value or _utcnow_naive()
    if not isinstance(value, datetime):
        raise DriverObservationValidationError("occurred_at must be a datetime")
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _normalise_notes(notes: str | None) -> str:
    value = (notes or "").strip()
    if len(value) > 2000:
        raise DriverObservationValidationError("Check-in notes are too long.")
    return value


def _existing_for_day(
    *,
    car_id: int,
    driver_user_id: int,
    operational_date: date,
) -> DriverCheckIn | None:
    return (
        DriverCheckIn.query.filter(
            DriverCheckIn.car_id == car_id,
            DriverCheckIn.driver_id == driver_user_id,
            db.func.date(DriverCheckIn.created_at) == operational_date,
        )
        .order_by(DriverCheckIn.id.asc())
        .first()
    )


def _event_for(checkin_id: int) -> VehicleEvent | None:
    return VehicleEvent.query.filter_by(
        subject_type="driver_checkin",
        subject_id=checkin_id,
        event_type="driver_observation.checkin_recorded",
    ).first()


def _same_observation(
    checkin: DriverCheckIn,
    *,
    tyre_warning: bool,
    fuel_low: bool,
    dashboard_light: bool,
    vibration: bool,
    unusual_sound: bool,
    notes: str,
) -> bool:
    return all(
        (
            bool(checkin.tyre_warning) == bool(tyre_warning),
            bool(checkin.fuel_low) == bool(fuel_low),
            bool(checkin.dashboard_light) == bool(dashboard_light),
            bool(checkin.vibration) == bool(vibration),
            bool(checkin.unusual_sound) == bool(unusual_sound),
            (checkin.notes or "").strip() == notes,
        )
    )


class DriverObservationService:
    """Record one assigned-driver observation per vehicle operational day."""

    @staticmethod
    def record_checkin(
        *,
        car_id: int,
        actor_user_id: int,
        tyre_warning: bool = False,
        fuel_low: bool = False,
        dashboard_light: bool = False,
        vibration: bool = False,
        unusual_sound: bool = False,
        notes: str | None = None,
        occurred_at: datetime | None = None,
    ) -> DriverObservationResult:
        authority = resolve_vehicle_authority(actor_user_id, car_id)
        if authority != VEHICLE_AUTHORITY_DRIVER:
            raise DriverObservationAuthorityError(
                "driver check-in requires current assigned-driver authority"
            )

        occurred_at = _normalise_datetime(occurred_at)
        operational_date = occurred_at.date()
        notes = _normalise_notes(notes)

        structured = {
            "tyre_warning": bool(tyre_warning),
            "fuel_low": bool(fuel_low),
            "dashboard_light": bool(dashboard_light),
            "vibration": bool(vibration),
            "unusual_sound": bool(unusual_sound),
        }

        existing = _existing_for_day(
            car_id=car_id,
            driver_user_id=actor_user_id,
            operational_date=operational_date,
        )
        if existing is not None:
            if not _same_observation(existing, notes=notes, **structured):
                raise DriverObservationConflict(
                    "a different driver check-in already exists for this operational day"
                )
            # Historical rows are not given synthetic events. If the row predates
            # Wave 2.4B, an idempotent retry returns it without manufacturing history.
            return DriverObservationResult(
                checkin=existing,
                event=_event_for(existing.id),
                created=False,
            )

        checkin = DriverCheckIn(
            car_id=car_id,
            driver_id=actor_user_id,
            notes=notes or None,
            created_at=occurred_at,
            **structured,
        )

        try:
            # PostgreSQL same-day uniqueness is enforced by the Wave 2.4B
            # expression index. A SAVEPOINT lets a concurrent identical replay
            # recover without poisoning the caller-owned outer transaction.
            with db.session.begin_nested():
                db.session.add(checkin)
                db.session.flush([checkin])
        except IntegrityError as exc:
            existing = _existing_for_day(
                car_id=car_id,
                driver_user_id=actor_user_id,
                operational_date=operational_date,
            )
            if existing is None or not _same_observation(
                existing,
                notes=notes,
                **structured,
            ):
                raise DriverObservationConflict(
                    "a different driver check-in already exists for this operational day"
                ) from exc
            return DriverObservationResult(
                checkin=existing,
                event=_event_for(existing.id),
                created=False,
            )

        event = emit_driver_checkin_recorded(
            car_id=car_id,
            checkin_id=checkin.id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            operational_date=operational_date.isoformat(),
            notes_present=bool(notes),
            idempotency_key=(
                f"driver-checkin:{actor_user_id}:{car_id}:"
                f"{operational_date.isoformat()}:recorded"
            ),
            **structured,
        )

        return DriverObservationResult(checkin=checkin, event=event, created=True)
