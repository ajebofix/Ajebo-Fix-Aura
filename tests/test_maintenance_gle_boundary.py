from __future__ import annotations

from datetime import datetime
import hashlib

import pytest

from extensions import db
from maintenance.knowledge_service import MaintenanceKnowledgeService
from maintenance.state_engine import MaintenanceStateEngine
from maintenance.state_engine import MaintenanceStateResult, MaintenanceVehicleEvaluation
from models import Car, CarOwnership, User, VehicleEvent, VehicleHealthAlert, VehicleProfile
from services.health_alert_service import CareSignalService
from services.mileage_observations import MileageObservationService


BASELINE_KM = 64_100
INTERVAL_KM = 16_093
NEXT_DUE_KM = 80_193


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Boundary {role} {suffix}",
        email=f"maintenance-boundary-{role}-{suffix}@example.com",
        phone_number=f"+2348774{suffix:06d}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _vehicle(*, suffix: int):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 1000, role="admin")
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2021,
        vin=f"W1NBOUND{suffix:009d}",
        engine_type="M256",
        transmission_type="9G-TRONIC",
        current_mileage=None,
        vehicle_identity_source="vin",
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"BD-{suffix:03d}-LA",
        mileage_at_transfer=0,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.add(
        VehicleProfile(
            car_id=car.id,
            trim="GLE 450 4MATIC",
            fuel_type="Gasoline",
            drive_type="AWD",
            engine_model="M256",
            vin_decoded=True,
            decoded_at=datetime(2026, 9, 1, 8, 0, 0),
            source="synthetic_boundary_test",
        )
    )
    db.session.commit()
    return owner, advisor, car, ownership


def _verified_schedule(*, advisor: User):
    rule = MaintenanceKnowledgeService.create_candidate(
        maintenance_item_key="scheduled_maintenance",
        display_name="Mercedes-Benz scheduled maintenance",
        interval_km=INTERVAL_KM,
        interval_months=12,
        source_type="oem",
        source_name="Synthetic copy of verified production schedule",
        source_reference=f"test://maintenance-boundary/{advisor.id}",
        source_version="boundary-v1",
        brand="Mercedes-Benz",
        model="GLE 450",
        year_start=2021,
        year_end=2021,
    )
    MaintenanceKnowledgeService.verify(
        rule_id=rule.id,
        actor_user_id=advisor.id,
        occurred_at=datetime(2026, 9, 14, 1, 0, 0),
    )
    db.session.commit()
    return rule


def _current_service(*, car, ownership, advisor):
    occurred_at = datetime(2026, 9, 14, 1, 0, 0)
    token = f"boundary|{car.id}|{BASELINE_KM}|{occurred_at}"
    event = VehicleEvent(
        car_id=car.id,
        ownership_id=ownership.id,
        event_type="service",
        title="Mercedes-Benz scheduled maintenance",
        description="Isolated synthetic maintenance boundary baseline.",
        mileage=BASELINE_KM,
        source="admin_current",
        data={
            "maintenance_item_key": "scheduled_maintenance",
            "record_mode": "current",
            "entered_by_role": "advisor",
        },
        fingerprint=hashlib.sha256(token.encode()).hexdigest(),
        created_by=advisor.id,
        is_deleted=False,
        created_at=occurred_at,
        schema_version=1,
        occurred_at=occurred_at,
        recorded_at=occurred_at,
        subject_type="service_record",
        actor_type="user",
        actor_user_id=advisor.id,
        actor_authority="advisor",
        visibility="internal",
        progression_direction="not_applicable",
    )
    db.session.add(event)
    db.session.flush()
    event.subject_id = event.id
    db.session.commit()
    return event


def _odometer(*, car, ownership, advisor, km: int):
    MileageObservationService.record(
        car=car,
        odometer_km=km,
        source="advisor_observation",
        verification_status="advisor_verified",
        observed_at=datetime(2026, 9, 20, 8, 0, 0),
        recorded_by_user_id=advisor.id,
        ownership_id=ownership.id,
        review_status="not_required",
        advance_current=True,
        commit=False,
    )
    db.session.commit()


@pytest.mark.parametrize(
    ("current_km", "expected"),
    [
        (79_192, "upcoming"),
        (79_193, "due"),
        (80_193, "due"),
        (80_194, "overdue"),
    ],
)
def test_gle_64100_boundary_states_are_exact(app, current_km, expected):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=current_km)
        rule = _verified_schedule(advisor=advisor)
        baseline = _current_service(
            car=car,
            ownership=ownership,
            advisor=advisor,
        )
        _odometer(
            car=car,
            ownership=ownership,
            advisor=advisor,
            km=current_km,
        )

        result = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=datetime(2026, 9, 20, 12, 0, 0),
        )

        assert result.state == expected
        assert result.latest_matching_service_event_id == baseline.id
        assert result.latest_matching_service_mileage == BASELINE_KM
        assert result.next_due_mileage == NEXT_DUE_KM
        assert result.next_due_date == "2027-09-14"
        assert result.unknown_reasons == ()


def _synthetic_evaluation(*, car_id: int, state: str):
    result = MaintenanceStateResult(
        maintenance_item_key="scheduled_maintenance",
        display_name="Mercedes-Benz scheduled maintenance",
        state=state,
        rule_id=1,
        rule_version="boundary-v1",
        rule_source={"source_type": "oem"},
        verification_status="advisor_verified",
        vehicle_identity_used={"car_id": car_id, "brand": "Mercedes-Benz"},
        current_odometer_km=80_194,
        odometer_provenance={
            "source": "advisor_observation",
            "verification_status": "advisor_verified",
            "freshness_status": "current",
            "provenance_available": True,
        },
        latest_matching_service_event_id=1,
        latest_matching_service_mileage=BASELINE_KM,
        latest_matching_service_date="2026-09-14",
        next_due_mileage=NEXT_DUE_KM,
        next_due_date="2027-09-14",
        evaluated_at="2026-09-20T12:00:00",
        unknown_reasons=(),
    )
    return MaintenanceVehicleEvaluation(
        car_id=car_id,
        evaluated_at="2026-09-20T12:00:00",
        results=(result,),
    )


def test_repeated_overdue_evaluation_does_not_duplicate_signal(app, monkeypatch):
    with app.app_context():
        _owner, _advisor, car, _ownership = _vehicle(suffix=900)
        monkeypatch.setattr(
            "services.health_alert_service.calculate_vehicle_health",
            lambda _car, _ownership: {"health_score": 80, "risk_reasons": []},
        )
        monkeypatch.setattr(
            "services.health_alert_service.HealthTrendService.analyze_car_trajectory",
            lambda _car_id: {"rapid_decline": False},
        )
        monkeypatch.setattr(
            "maintenance.care_signal.MaintenanceStateEngine.evaluate_vehicle",
            lambda **_kwargs: _synthetic_evaluation(car_id=car.id, state="overdue"),
        )

        CareSignalService.evaluate(car.id, trigger="mileage_observed")
        CareSignalService.evaluate(car.id, trigger="mileage_observed")

        signals = VehicleHealthAlert.query.filter_by(
            car_id=car.id,
            alert_type="maintenance_monitoring",
        ).all()
        assert len(signals) == 1
        assert signals[0].is_active is True
        assert signals[0].status == "new"

        events = VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert",
            subject_id=signals[0].id,
            event_type="care_signal.raised",
        ).all()
        assert len(events) == 1
