"""Verify Aura Wave 2.4B driver-observation contracts on PostgreSQL."""

from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import sys
import uuid

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402
from extensions import db  # noqa: E402
from models import (  # noqa: E402
    Car,
    CarDriver,
    CarOwnership,
    DriverCheckIn,
    User,
    VehicleEvent,
)
from services.driver_observation import (  # noqa: E402
    DriverObservationAuthorityError,
    DriverObservationConflict,
    DriverObservationService,
)


def _user(*, token: str, role: str, code: int, driver_score: int = 100) -> User:
    user = User(
        name=f"Wave 2.4B {role} {code}",
        email=f"wave24b-{code}-{token}@example.com",
        phone_number=f"+23488{int(token[:6], 16) % 1000000:06d}{code:02d}",
        role=role,
        driver_score=driver_score,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _raw_driver_event(
    *,
    car_id: int,
    ownership_id: int,
    driver_id: int,
    checkin_id: int,
    progression_direction: str | None = "insufficient_evidence",
    new_state: str | None = "recorded",
    visibility: str = "advisor",
    actor_authority: str = "driver",
) -> VehicleEvent:
    now = datetime(2026, 9, 6, 8, 0, 0)
    return VehicleEvent(
        car_id=car_id,
        ownership_id=ownership_id,
        event_type="driver_observation.checkin_recorded",
        severity="low",
        event_date=now.date(),
        title="Wave 2.4B PostgreSQL negative probe",
        description=None,
        mileage=27000,
        source="verify.driver_observations",
        data={"operational_date": now.date().isoformat()},
        fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        schema_version=1,
        occurred_at=now,
        recorded_at=now,
        subject_type="driver_checkin",
        subject_id=checkin_id,
        actor_type="user",
        actor_user_id=driver_id,
        actor_authority=actor_authority,
        visibility=visibility,
        previous_state=None,
        new_state=new_state,
        progression_direction=progression_direction,
        evidence_refs=[],
        correction_of_event_id=None,
        created_by=driver_id,
        is_deleted=False,
    )


def _must_reject(row: VehicleEvent, label: str) -> None:
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return
    raise SystemExit(f"PostgreSQL accepted invalid Wave 2.4B event: {label}")


def main() -> None:
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            raise SystemExit("This verifier must run against PostgreSQL")

        inspector = inspect(db.engine)
        checks = {
            item["name"]
            for item in inspector.get_check_constraints("vehicle_events")
        }
        required_checks = {
            "ck_vehicle_events_canonical_subject_event",
            "ck_vehicle_events_driver_observation_contract",
        }
        missing_checks = required_checks - checks
        if missing_checks:
            raise SystemExit(f"Missing Wave 2.4B checks: {sorted(missing_checks)}")

        indexes = {
            item["name"]: item
            for item in inspector.get_indexes("driver_checkins")
        }
        day_index = indexes.get("uq_driver_checkins_driver_car_operational_day")
        if day_index is None or not day_index.get("unique"):
            raise SystemExit("Wave 2.4B operational-day unique index is missing")

        # Migration must not synthesize canonical history from old check-in rows.
        if VehicleEvent.query.filter_by(subject_type="driver_checkin").count():
            raise SystemExit("Wave 2.4B synthesized driver-observation history")

        token = uuid.uuid4().hex[:10]
        owner = _user(token=token, role="user", code=1)
        driver = _user(token=token, role="driver", code=2, driver_score=73)
        advisor = _user(token=token, role="admin", code=3)
        unrelated_driver = _user(token=token, role="driver", code=4)

        car = Car(
            brand="Mercedes-Benz",
            model="GLE 450",
            year=2024,
            vin=f"W1N24B{token.upper()}",
            current_mileage=27000,
        )
        db.session.add(car)
        db.session.flush()
        ownership = CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"D-{token[:3].upper()}-LA",
            mileage_at_transfer=26000,
            is_active=True,
        )
        db.session.add(ownership)
        db.session.add(CarDriver(user_id=driver.id, car_id=car.id, is_active=True))
        db.session.commit()

        original_score = driver.driver_score
        first = DriverObservationService.record_checkin(
            car_id=car.id,
            actor_user_id=driver.id,
            tyre_warning=True,
            dashboard_light=True,
            notes="VERIFIER PRIVATE DRIVER NOTE",
            occurred_at=datetime(2026, 9, 6, 8, 30, 0),
        )
        db.session.commit()

        event = VehicleEvent.query.filter_by(
            subject_type="driver_checkin",
            subject_id=first.checkin.id,
            event_type="driver_observation.checkin_recorded",
        ).one()
        if event.progression_direction != "insufficient_evidence":
            raise SystemExit("Driver warning was incorrectly classified as health progression")
        if event.actor_authority != "driver" or event.visibility != "advisor":
            raise SystemExit("Driver event authority/visibility contract is incorrect")
        if "VERIFIER PRIVATE DRIVER NOTE" in str(event.data):
            raise SystemExit("Driver free text leaked into canonical event payload")
        if db.session.get(User, driver.id).driver_score != original_score:
            raise SystemExit("Driver check-in mutated the legacy driver_score")

        replay = DriverObservationService.record_checkin(
            car_id=car.id,
            actor_user_id=driver.id,
            tyre_warning=True,
            dashboard_light=True,
            notes="VERIFIER PRIVATE DRIVER NOTE",
            occurred_at=datetime(2026, 9, 6, 18, 30, 0),
        )
        db.session.commit()
        if replay.created or replay.checkin.id != first.checkin.id:
            raise SystemExit("Identical same-day replay was not idempotent")
        if DriverCheckIn.query.filter_by(car_id=car.id, driver_id=driver.id).count() != 1:
            raise SystemExit("Idempotent replay created a duplicate check-in")

        try:
            DriverObservationService.record_checkin(
                car_id=car.id,
                actor_user_id=driver.id,
                vibration=True,
                occurred_at=datetime(2026, 9, 6, 19, 0, 0),
            )
        except DriverObservationConflict:
            db.session.rollback()
        else:
            raise SystemExit("Conflicting same-day driver observation did not fail closed")

        for actor in (owner, advisor, unrelated_driver):
            try:
                DriverObservationService.record_checkin(
                    car_id=car.id,
                    actor_user_id=actor.id,
                    occurred_at=datetime(2026, 9, 7, 8, 0, 0),
                )
            except DriverObservationAuthorityError:
                db.session.rollback()
            else:
                raise SystemExit("Non-assigned actor created a driver observation")

        # The database index must reject bypass attempts on the same operational day.
        duplicate = DriverCheckIn(
            car_id=car.id,
            driver_id=driver.id,
            unusual_sound=True,
            created_at=datetime(2026, 9, 6, 23, 59, 0),
        )
        db.session.add(duplicate)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        else:
            raise SystemExit("PostgreSQL accepted duplicate driver/vehicle/day check-in")

        car_id = car.id
        ownership_id = ownership.id
        driver_id = driver.id
        checkin_id = first.checkin.id
        probes = [
            (
                "deteriorating progression",
                _raw_driver_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    driver_id=driver_id,
                    checkin_id=checkin_id,
                    progression_direction="deteriorating",
                ),
            ),
            (
                "missing recorded state",
                _raw_driver_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    driver_id=driver_id,
                    checkin_id=checkin_id,
                    new_state=None,
                ),
            ),
            (
                "client-visible driver event",
                _raw_driver_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    driver_id=driver_id,
                    checkin_id=checkin_id,
                    visibility="client",
                ),
            ),
            (
                "owner authority on driver event",
                _raw_driver_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    driver_id=driver_id,
                    checkin_id=checkin_id,
                    actor_authority="owner",
                ),
            ),
        ]
        for label, probe in probes:
            _must_reject(probe, label)

        print("Wave 2.4B PostgreSQL driver-observation verification passed.")


if __name__ == "__main__":
    main()
