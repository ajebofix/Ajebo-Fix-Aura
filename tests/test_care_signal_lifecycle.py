from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import Car, CarDriver, CarOwnership, User, VehicleEvent, VehicleHealthAlert
from services.care_signal_event_emission import (
    CareSignalEventAuthorityError,
    emit_care_signal_event,
)
from services.care_signal_lifecycle import (
    CareSignalAuthorityError,
    CareSignalIdempotencyConflict,
    CareSignalLifecycleService,
    CareSignalStateError,
)
from services.health_alert_service import CareSignalService


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Care Signal {role} {suffix}",
        email=f"care-signal-{role}-{suffix}@example.com",
        phone_number=f"+2348971{suffix:06d}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _context(*, suffix: int):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 1000, role="admin")
    driver = _user(suffix=suffix + 2000, role="driver")
    unrelated = _user(suffix=suffix + 3000)

    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1N24CS{suffix:010d}",
        current_mileage=26000,
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"CS-{suffix:03d}-LA",
        mileage_at_transfer=25000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.add(CarDriver(user_id=driver.id, car_id=car.id, is_active=True))
    db.session.commit()
    return owner, advisor, driver, unrelated, car, ownership


def _events(signal_id: int) -> list[VehicleEvent]:
    return (
        VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert",
            subject_id=signal_id,
        )
        .order_by(VehicleEvent.id.asc())
        .all()
    )


def _raise_system_signal(*, car_id: int, occurred_at: datetime, severity: str = "high"):
    return CareSignalLifecycleService.raise_signal(
        car_id=car_id,
        alert_type="low_health_status",
        severity=severity,
        message=(
            "Vehicle health status indicates elevated risk. "
            "An advisor review is recommended."
        ),
        source_classification="deterministic_rule:test",
        actor_type="system",
        actor_user_id=None,
        occurred_at=occurred_at,
    )


def test_system_raise_emits_bounded_canonical_event_without_fake_human(app):
    with app.app_context():
        _owner, _advisor, _driver, _unrelated, car, _ownership = _context(suffix=1)
        signal = _raise_system_signal(
            car_id=car.id,
            occurred_at=datetime(2026, 9, 6, 8, 0, 0),
        )
        db.session.commit()

        assert signal.status == "new"
        assert signal.is_active is True
        assert signal.resolved_at is None

        events = _events(signal.id)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "care_signal.raised"
        assert event.previous_state is None
        assert event.new_state == "new"
        assert event.progression_direction == "not_applicable"
        assert event.actor_type == "system"
        assert event.actor_user_id is None
        assert event.actor_authority == "system"
        assert event.created_by is None
        assert event.visibility == "client"
        assert event.data == {
            "alert_type": "low_health_status",
            "severity": "high",
            "source_classification": "deterministic_rule:test",
        }
        assert signal.message not in str(event.data)


def test_active_repeat_is_idempotent_and_semantic_change_fails_closed(app):
    with app.app_context():
        _owner, _advisor, _driver, _unrelated, car, _ownership = _context(suffix=2)
        first = _raise_system_signal(
            car_id=car.id,
            occurred_at=datetime(2026, 9, 6, 8, 0, 0),
        )
        db.session.commit()

        replay = _raise_system_signal(
            car_id=car.id,
            occurred_at=datetime(2026, 9, 6, 12, 0, 0),
        )
        db.session.commit()

        assert replay.id == first.id
        assert VehicleHealthAlert.query.filter_by(
            car_id=car.id,
            alert_type="low_health_status",
        ).count() == 1
        assert len(_events(first.id)) == 1

        with pytest.raises(CareSignalIdempotencyConflict):
            _raise_system_signal(
                car_id=car.id,
                occurred_at=datetime(2026, 9, 6, 13, 0, 0),
                severity="moderate",
            )
        db.session.rollback()
        assert len(_events(first.id)) == 1


def test_advisor_acknowledge_then_resolve_emits_state_events(app):
    with app.app_context():
        _owner, advisor, _driver, _unrelated, car, _ownership = _context(suffix=3)
        signal = _raise_system_signal(
            car_id=car.id,
            occurred_at=datetime(2026, 9, 6, 8, 0, 0),
        )
        db.session.commit()

        CareSignalLifecycleService.acknowledge(
            signal_id=signal.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 9, 6, 9, 0, 0),
        )
        db.session.commit()
        assert signal.status == "acknowledged"
        assert signal.is_active is True
        assert signal.acknowledged_by_id == advisor.id

        CareSignalLifecycleService.resolve(
            signal_id=signal.id,
            actor_type="user",
            actor_user_id=advisor.id,
            source_classification="advisor_resolution",
            occurred_at=datetime(2026, 9, 6, 10, 0, 0),
        )
        db.session.commit()

        assert signal.status == "resolved"
        assert signal.is_active is False
        assert signal.resolved_at == datetime(2026, 9, 6, 10, 0, 0)

        events = _events(signal.id)
        assert [event.event_type for event in events] == [
            "care_signal.raised",
            "care_signal.acknowledged",
            "care_signal.resolved",
        ]
        assert events[1].previous_state == "new"
        assert events[1].new_state == "acknowledged"
        assert events[1].actor_authority in {"advisor", "administrator"}
        assert events[1].visibility == "advisor"
        assert events[2].previous_state == "acknowledged"
        assert events[2].new_state == "resolved"
        assert events[2].visibility == "client"


def test_resolved_occurrence_is_terminal_and_recurrence_creates_new_row(app):
    with app.app_context():
        _owner, _advisor, _driver, _unrelated, car, _ownership = _context(suffix=4)
        first = _raise_system_signal(
            car_id=car.id,
            occurred_at=datetime(2026, 9, 6, 8, 0, 0),
        )
        db.session.commit()

        CareSignalLifecycleService.resolve(
            signal_id=first.id,
            actor_type="system",
            actor_user_id=None,
            source_classification="deterministic_rule:test",
            occurred_at=datetime(2026, 9, 6, 9, 0, 0),
        )
        db.session.commit()

        assert first.status == "resolved"
        with pytest.raises(CareSignalStateError):
            CareSignalLifecycleService.acknowledge(
                signal_id=first.id,
                actor_user_id=_advisor.id,
                occurred_at=datetime(2026, 9, 6, 9, 30, 0),
            )
        db.session.rollback()

        second = _raise_system_signal(
            car_id=car.id,
            occurred_at=datetime(2026, 9, 7, 8, 0, 0),
        )
        db.session.commit()

        assert second.id != first.id
        assert second.status == "new"
        occurrences = VehicleHealthAlert.query.filter_by(
            car_id=car.id,
            alert_type="low_health_status",
        ).order_by(VehicleHealthAlert.id.asc()).all()
        assert [item.id for item in occurrences] == [first.id, second.id]
        assert occurrences[0].status == "resolved"
        assert occurrences[1].is_active is True
        assert [event.event_type for event in _events(first.id)] == [
            "care_signal.raised",
            "care_signal.resolved",
        ]
        assert [event.event_type for event in _events(second.id)] == [
            "care_signal.raised"
        ]


def test_non_advisors_and_unapproved_actors_fail_closed(app):
    with app.app_context():
        owner, _advisor, driver, unrelated, car, _ownership = _context(suffix=5)
        signal = _raise_system_signal(
            car_id=car.id,
            occurred_at=datetime(2026, 9, 6, 8, 0, 0),
        )
        db.session.commit()

        for actor in (owner, driver, unrelated):
            with pytest.raises(CareSignalAuthorityError):
                CareSignalLifecycleService.acknowledge(
                    signal_id=signal.id,
                    actor_user_id=actor.id,
                    occurred_at=datetime(2026, 9, 6, 9, 0, 0),
                )
            db.session.rollback()

        with pytest.raises(CareSignalAuthorityError):
            CareSignalLifecycleService.raise_signal(
                car_id=car.id,
                alert_type="elevated_risk_indicator",
                severity="moderate",
                message="Legacy pseudo-predictive signal must not be canonicalized.",
                source_classification="deterministic_rule:test",
                actor_type="system",
                actor_user_id=None,
                occurred_at=datetime(2026, 9, 6, 10, 0, 0),
            )

        for prohibited_actor_type in ("provider", "rina"):
            with pytest.raises(CareSignalEventAuthorityError):
                emit_care_signal_event(
                    car_id=car.id,
                    signal_id=signal.id,
                    event_type="care_signal.resolved",
                    actor_type=prohibited_actor_type,
                    actor_user_id=None,
                    occurred_at=datetime(2026, 9, 6, 11, 0, 0),
                    previous_state="new",
                    new_state="resolved",
                    alert_type=signal.alert_type,
                    severity=signal.severity,
                    source_classification="negative_probe",
                    idempotency_key=f"negative:{prohibited_actor_type}:{signal.id}",
                )


def test_event_failure_rolls_back_signal_with_outer_transaction(app, monkeypatch):
    with app.app_context():
        _owner, _advisor, _driver, _unrelated, car, _ownership = _context(suffix=6)

        def fail_event(**_kwargs):
            raise RuntimeError("forced care-signal event failure")

        monkeypatch.setattr(
            "services.care_signal_lifecycle.emit_care_signal_event",
            fail_event,
        )

        with pytest.raises(RuntimeError, match="forced care-signal event failure"):
            _raise_system_signal(
                car_id=car.id,
                occurred_at=datetime(2026, 9, 6, 8, 0, 0),
            )

        db.session.rollback()
        assert VehicleHealthAlert.query.filter_by(car_id=car.id).count() == 0
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert"
        ).count() == 0


def test_rule_engine_does_not_create_pseudo_predictive_signal(app, monkeypatch):
    with app.app_context():
        _owner, _advisor, _driver, _unrelated, car, _ownership = _context(suffix=7)

        monkeypatch.setattr(
            "services.health_alert_service.calculate_vehicle_health",
            lambda _car, _ownership: {
                "health_score": 80,
                "risk_reasons": ["predicted component concern"],
            },
        )
        monkeypatch.setattr(
            "services.health_alert_service.HealthTrendService.analyze_car_trajectory",
            lambda _car_id: {"rapid_decline": False},
        )

        CareSignalService.evaluate(car.id, trigger="event_created")

        assert VehicleHealthAlert.query.filter_by(
            car_id=car.id,
            alert_type="elevated_risk_indicator",
        ).count() == 0
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert"
        ).count() == 0
