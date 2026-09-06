from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import Car, CarDriver, CarOwnership, DriverCheckIn, User, VehicleEvent
from services.driver_observation import (
    DriverObservationAuthorityError,
    DriverObservationConflict,
    DriverObservationService,
)


def _user(*, suffix: int, role: str = "user", driver_score: int = 100) -> User:
    user = User(
        name=f"Driver Observation {role} {suffix}",
        email=f"driver-observation-{role}-{suffix}@example.com",
        phone_number=f"+2348991{suffix:06d}",
        role=role,
        driver_score=driver_score,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _context(*, suffix: int):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 1000, role="admin")
    driver = _user(suffix=suffix + 2000, role="driver", driver_score=77)
    unrelated_driver = _user(suffix=suffix + 3000, role="driver")

    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1NDRIVER{suffix:09d}",
        current_mileage=25000,
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"DRV-{suffix:03d}-LA",
        mileage_at_transfer=24000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.add(CarDriver(user_id=driver.id, car_id=car.id, is_active=True))
    db.session.commit()
    return owner, advisor, driver, unrelated_driver, car


def _events(checkin_id: int) -> list[VehicleEvent]:
    return (
        VehicleEvent.query.filter_by(
            subject_type="driver_checkin",
            subject_id=checkin_id,
        )
        .order_by(VehicleEvent.id.asc())
        .all()
    )


def test_assigned_driver_records_canonical_observation_without_score_mutation(app):
    with app.app_context():
        _owner, _advisor, driver, _unrelated, car = _context(suffix=1)
        original_score = driver.driver_score

        result = DriverObservationService.record_checkin(
            car_id=car.id,
            actor_user_id=driver.id,
            tyre_warning=True,
            dashboard_light=True,
            notes="Tyre warning appeared after leaving the estate.",
            occurred_at=datetime(2026, 9, 6, 8, 30, 0),
        )
        db.session.commit()

        assert result.created is True
        assert result.checkin.driver_id == driver.id
        assert result.checkin.car_id == car.id
        assert driver.driver_score == original_score

        events = _events(result.checkin.id)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "driver_observation.checkin_recorded"
        assert event.previous_state is None
        assert event.new_state == "recorded"
        assert event.progression_direction == "insufficient_evidence"
        assert event.actor_authority == "driver"
        assert event.visibility == "advisor"
        assert event.data["operational_date"] == "2026-09-06"
        assert event.data["tyre_warning"] is True
        assert event.data["dashboard_light"] is True
        assert event.data["notes_present"] is True
        assert "Tyre warning appeared" not in str(event.data)
        assert event.description is None


def test_identical_same_day_replay_is_idempotent(app):
    with app.app_context():
        _owner, _advisor, driver, _unrelated, car = _context(suffix=2)
        occurred_at = datetime(2026, 9, 6, 9, 0, 0)

        first = DriverObservationService.record_checkin(
            car_id=car.id,
            actor_user_id=driver.id,
            vibration=True,
            notes="Light vibration observed.",
            occurred_at=occurred_at,
        )
        db.session.commit()

        second = DriverObservationService.record_checkin(
            car_id=car.id,
            actor_user_id=driver.id,
            vibration=True,
            notes="Light vibration observed.",
            occurred_at=datetime(2026, 9, 6, 17, 15, 0),
        )
        db.session.commit()

        assert second.created is False
        assert second.checkin.id == first.checkin.id
        assert second.event is not None
        assert second.event.id == first.event.id
        assert DriverCheckIn.query.filter_by(car_id=car.id, driver_id=driver.id).count() == 1
        assert len(_events(first.checkin.id)) == 1


def test_conflicting_same_day_replay_fails_closed(app):
    with app.app_context():
        _owner, _advisor, driver, _unrelated, car = _context(suffix=3)

        first = DriverObservationService.record_checkin(
            car_id=car.id,
            actor_user_id=driver.id,
            fuel_low=True,
            occurred_at=datetime(2026, 9, 6, 7, 0, 0),
        )
        db.session.commit()

        with pytest.raises(DriverObservationConflict):
            DriverObservationService.record_checkin(
                car_id=car.id,
                actor_user_id=driver.id,
                fuel_low=False,
                unusual_sound=True,
                occurred_at=datetime(2026, 9, 6, 12, 0, 0),
            )

        assert DriverCheckIn.query.filter_by(car_id=car.id, driver_id=driver.id).count() == 1
        assert bool(first.checkin.fuel_low) is True
        assert bool(first.checkin.unusual_sound) is False
        assert len(_events(first.checkin.id)) == 1


def test_non_assigned_authorities_cannot_record_driver_observation(app):
    with app.app_context():
        owner, advisor, _driver, unrelated_driver, car = _context(suffix=4)

        for actor in (owner, advisor, unrelated_driver):
            with pytest.raises(DriverObservationAuthorityError):
                DriverObservationService.record_checkin(
                    car_id=car.id,
                    actor_user_id=actor.id,
                    occurred_at=datetime(2026, 9, 6, 10, 0, 0),
                )

        assert DriverCheckIn.query.filter_by(car_id=car.id).count() == 0
        assert VehicleEvent.query.filter_by(subject_type="driver_checkin").count() == 0


def test_event_failure_rolls_back_checkin_with_outer_transaction(app, monkeypatch):
    with app.app_context():
        _owner, _advisor, driver, _unrelated, car = _context(suffix=5)

        def fail_event(**_kwargs):
            raise RuntimeError("forced canonical event failure")

        monkeypatch.setattr(
            "services.driver_observation.emit_driver_checkin_recorded",
            fail_event,
        )

        with pytest.raises(RuntimeError, match="forced canonical event failure"):
            DriverObservationService.record_checkin(
                car_id=car.id,
                actor_user_id=driver.id,
                dashboard_light=True,
                occurred_at=datetime(2026, 9, 6, 11, 0, 0),
            )

        db.session.rollback()
        assert DriverCheckIn.query.filter_by(car_id=car.id, driver_id=driver.id).count() == 0
        assert VehicleEvent.query.filter_by(subject_type="driver_checkin").count() == 0


def test_historical_same_day_row_is_not_given_synthetic_event(app):
    with app.app_context():
        _owner, _advisor, driver, _unrelated, car = _context(suffix=6)
        historical = DriverCheckIn(
            car_id=car.id,
            driver_id=driver.id,
            tyre_warning=False,
            fuel_low=False,
            dashboard_light=False,
            vibration=False,
            unusual_sound=False,
            notes="Historical row before Wave 2.4B.",
            created_at=datetime(2026, 9, 5, 8, 0, 0),
        )
        db.session.add(historical)
        db.session.commit()

        result = DriverObservationService.record_checkin(
            car_id=car.id,
            actor_user_id=driver.id,
            notes="Historical row before Wave 2.4B.",
            occurred_at=datetime(2026, 9, 5, 18, 0, 0),
        )
        db.session.commit()

        assert result.created is False
        assert result.checkin.id == historical.id
        assert result.event is None
        assert _events(historical.id) == []
