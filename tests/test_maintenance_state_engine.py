from __future__ import annotations

from datetime import datetime
import hashlib

import pytest

from extensions import db
from maintenance.knowledge_service import MaintenanceKnowledgeService
from maintenance.state_engine import (
    DUE_WINDOW_DAYS,
    DUE_WINDOW_KM,
    MaintenanceStateEngine,
)
from models import Car, CarOwnership, User, VehicleEvent, VehicleProfile
from services.mileage_observations import MileageObservationService


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Maintenance State {role} {suffix}",
        email=f"maintenance-state-{role}-{suffix}@example.com",
        phone_number=f"+2348663{suffix:06d}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _vehicle(*, suffix: int, with_profile: bool = True):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 1000, role="admin")
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2021,
        vin=f"W1NSTATE{suffix:009d}",
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
        plate_number=f"MS-{suffix:03d}-LA",
        mileage_at_transfer=0,
        is_active=True,
    )
    db.session.add(ownership)

    if with_profile:
        db.session.add(
            VehicleProfile(
                car_id=car.id,
                trim="GLE 450 4MATIC",
                fuel_type="Gasoline",
                drive_type="AWD",
                engine_model="M256",
                vin_decoded=True,
                decoded_at=datetime(2026, 1, 1, 8, 0, 0),
                source="vin_provider",
            )
        )

    db.session.commit()
    return owner, advisor, car, ownership


def _verified_rule(*, advisor: User, **overrides):
    payload = {
        "maintenance_item_key": "engine_oil",
        "display_name": "Engine oil service",
        "interval_km": 10000,
        "interval_months": None,
        "source_type": "oem",
        "source_name": "Reviewed manufacturer schedule reference",
        "source_reference": f"reference://m3/{advisor.id}/engine-oil",
        "source_version": "v1",
        "brand": "Mercedes-Benz",
        "model": "GLE 450",
        "year_start": 2021,
        "year_end": 2021,
    }
    payload.update(overrides)
    rule = MaintenanceKnowledgeService.create_candidate(**payload)
    MaintenanceKnowledgeService.verify(
        rule_id=rule.id,
        actor_user_id=advisor.id,
        occurred_at=datetime(2026, 9, 12, 8, 0, 0),
    )
    db.session.commit()
    return rule


def _odometer(
    *,
    car: Car,
    ownership: CarOwnership,
    advisor: User,
    km: int,
    observed_at: datetime,
    verification_status: str = "advisor_verified",
):
    observation = MileageObservationService.record(
        car=car,
        odometer_km=km,
        source="advisor_observation",
        verification_status=verification_status,
        observed_at=observed_at,
        recorded_by_user_id=advisor.id,
        ownership_id=ownership.id,
        review_status="not_required",
        advance_current=True,
        commit=False,
    )
    db.session.commit()
    return observation


def _service(
    *,
    car: Car,
    ownership: CarOwnership,
    advisor: User,
    item_key: str | None,
    mileage: int | None,
    occurred_at: datetime | None,
    trustworthy: bool = True,
):
    token = f"{car.id}|{item_key}|{mileage}|{occurred_at}|{VehicleEvent.query.count()}"
    metadata = {}
    if item_key is not None:
        metadata["maintenance_item_key"] = item_key
    if trustworthy:
        metadata.update(
            {
                "record_mode": "current",
                "entered_by_role": "advisor",
            }
        )

    event = VehicleEvent(
        car_id=car.id,
        ownership_id=ownership.id,
        event_type="service",
        title="Recorded service",
        description="Test service fact",
        mileage=mileage,
        source="admin_current" if trustworthy else "client",
        data=metadata,
        fingerprint=hashlib.sha256(token.encode()).hexdigest(),
        created_by=advisor.id,
        is_deleted=False,
        created_at=occurred_at or datetime(2026, 1, 1, 8, 0, 0),
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


def test_due_window_constants_are_explicit_product_policy():
    assert DUE_WINDOW_KM == 1000
    assert DUE_WINDOW_DAYS == 30


def test_vehicle_evaluation_abstains_when_no_verified_knowledge(app):
    with app.app_context():
        _owner, _advisor, car, _ownership = _vehicle(suffix=1)
        result = MaintenanceStateEngine.evaluate_vehicle(
            car=car,
            evaluated_at=datetime(2026, 9, 12, 12, 0, 0),
        )

        assert result.results == ()
        assert result.unknown_reasons == (
            "no_applicable_verified_maintenance_knowledge",
        )


@pytest.mark.parametrize(
    ("current_km", "expected"),
    [
        (58999, "upcoming"),
        (59000, "due"),
        (60000, "due"),
        (60001, "overdue"),
    ],
)
def test_mileage_state_boundaries_are_deterministic(app, current_km, expected):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=10 + current_km)
        rule = _verified_rule(advisor=advisor)
        _service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            item_key="engine_oil",
            mileage=50000,
            occurred_at=datetime(2026, 1, 15, 9, 0, 0),
        )
        _odometer(
            car=car,
            ownership=ownership,
            advisor=advisor,
            km=current_km,
            observed_at=datetime(2026, 9, 12, 8, 0, 0),
        )

        result = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=datetime(2026, 9, 12, 12, 0, 0),
        )

        assert result.state == expected
        assert result.next_due_mileage == 60000
        assert result.latest_matching_service_mileage == 50000
        assert result.unknown_reasons == ()


@pytest.mark.parametrize(
    ("evaluated_at", "expected"),
    [
        (datetime(2026, 12, 1, 12, 0, 0), "upcoming"),
        (datetime(2026, 12, 2, 12, 0, 0), "due"),
        (datetime(2027, 1, 1, 12, 0, 0), "due"),
        (datetime(2027, 1, 2, 12, 0, 0), "overdue"),
    ],
)
def test_calendar_month_rule_uses_exact_date_boundaries(app, evaluated_at, expected):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=20 + evaluated_at.day)
        rule = _verified_rule(
            advisor=advisor,
            maintenance_item_key="brake_fluid",
            display_name="Brake fluid",
            interval_km=None,
            interval_months=12,
            source_reference=f"reference://m3/{advisor.id}/brake-fluid",
        )
        _service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            item_key="brake_fluid",
            mileage=40000,
            occurred_at=datetime(2026, 1, 1, 9, 0, 0),
        )

        result = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=evaluated_at,
        )

        assert result.state == expected
        assert result.next_due_date == "2027-01-01"


def test_stale_odometer_abstains_unless_it_already_proves_overdue(app):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=31)
        rule = _verified_rule(advisor=advisor)
        _service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            item_key="engine_oil",
            mileage=10000,
            occurred_at=datetime(2025, 12, 1, 9, 0, 0),
        )
        _odometer(
            car=car,
            ownership=ownership,
            advisor=advisor,
            km=15000,
            observed_at=datetime(2026, 1, 1, 8, 0, 0),
        )

        uncertain = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=datetime(2026, 9, 12, 12, 0, 0),
        )
        assert uncertain.state == "unknown"
        assert "current_odometer_stale" in uncertain.unknown_reasons

        second_owner, second_advisor, second_car, second_ownership = _vehicle(suffix=32)
        second_rule = _verified_rule(
            advisor=second_advisor,
            source_reference=f"reference://m3/{second_advisor.id}/overdue",
        )
        _service(
            car=second_car,
            ownership=second_ownership,
            advisor=second_advisor,
            item_key="engine_oil",
            mileage=10000,
            occurred_at=datetime(2025, 12, 1, 9, 0, 0),
        )
        _odometer(
            car=second_car,
            ownership=second_ownership,
            advisor=second_advisor,
            km=21000,
            observed_at=datetime(2026, 1, 1, 8, 0, 0),
        )

        proven = MaintenanceStateEngine.evaluate_rule(
            car=second_car,
            rule=second_rule,
            evaluated_at=datetime(2026, 9, 12, 12, 0, 0),
        )
        assert proven.state == "overdue"
        assert proven.unknown_reasons == ()
        assert second_owner.id != _owner.id


def test_combined_rule_first_threshold_can_prove_overdue(app):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=40)
        rule = _verified_rule(
            advisor=advisor,
            interval_km=10000,
            interval_months=12,
        )
        _service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            item_key="engine_oil",
            mileage=10000,
            occurred_at=datetime(2025, 1, 1, 9, 0, 0),
        )
        _odometer(
            car=car,
            ownership=ownership,
            advisor=advisor,
            km=15000,
            observed_at=datetime(2025, 6, 1, 8, 0, 0),
        )

        result = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=datetime(2026, 1, 2, 12, 0, 0),
        )

        assert result.state == "overdue"
        assert result.next_due_mileage == 20000
        assert result.next_due_date == "2026-01-01"
        assert result.unknown_reasons == ()


def test_free_text_service_name_never_counts_as_matching_completion(app):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=50)
        rule = _verified_rule(advisor=advisor)
        _service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            item_key=None,
            mileage=50000,
            occurred_at=datetime(2026, 1, 1, 9, 0, 0),
        )
        _odometer(
            car=car,
            ownership=ownership,
            advisor=advisor,
            km=59000,
            observed_at=datetime(2026, 9, 12, 8, 0, 0),
        )

        result = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=datetime(2026, 9, 12, 12, 0, 0),
        )

        assert result.state == "unknown"
        assert result.unknown_reasons == ("no_matching_service_baseline",)


def test_unverified_service_baseline_and_legacy_odometer_fail_closed(app):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=60)
        rule = _verified_rule(advisor=advisor)
        _service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            item_key="engine_oil",
            mileage=50000,
            occurred_at=datetime(2026, 1, 1, 9, 0, 0),
            trustworthy=False,
        )
        car.current_mileage = 59000
        db.session.commit()

        result = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=datetime(2026, 9, 12, 12, 0, 0),
        )

        assert result.state == "unknown"
        assert result.unknown_reasons == ("matching_service_baseline_unverified",)


def test_verified_service_with_provenance_less_current_odometer_abstains(app):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=61)
        rule = _verified_rule(advisor=advisor)
        _service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            item_key="engine_oil",
            mileage=50000,
            occurred_at=datetime(2026, 1, 1, 9, 0, 0),
        )
        car.current_mileage = 59000
        db.session.commit()

        result = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=datetime(2026, 9, 12, 12, 0, 0),
        )

        assert result.state == "unknown"
        assert "current_odometer_provenance_unavailable" in result.unknown_reasons


def test_service_history_is_vehicle_isolated(app):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=70)
        rule = _verified_rule(advisor=advisor, brand="Mercedes-Benz", model=None)
        _odometer(
            car=car,
            ownership=ownership,
            advisor=advisor,
            km=59000,
            observed_at=datetime(2026, 9, 12, 8, 0, 0),
        )

        _other_owner, other_advisor, other_car, other_ownership = _vehicle(suffix=71)
        _service(
            car=other_car,
            ownership=other_ownership,
            advisor=other_advisor,
            item_key="engine_oil",
            mileage=50000,
            occurred_at=datetime(2026, 1, 1, 9, 0, 0),
        )

        result = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=datetime(2026, 9, 12, 12, 0, 0),
        )

        assert result.state == "unknown"
        assert result.latest_matching_service_event_id is None
        assert result.unknown_reasons == ("no_matching_service_baseline",)


def test_missing_required_vehicle_identity_returns_unknown(app):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=80, with_profile=False)
        rule = _verified_rule(
            advisor=advisor,
            trim="GLE 450 4MATIC",
        )
        _odometer(
            car=car,
            ownership=ownership,
            advisor=advisor,
            km=59000,
            observed_at=datetime(2026, 9, 12, 8, 0, 0),
        )

        result = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=datetime(2026, 9, 12, 12, 0, 0),
        )

        assert result.state == "unknown"
        assert "vehicle_identity_missing:trim" in result.unknown_reasons


def test_more_specific_rule_wins_and_equal_specificity_conflict_abstains(app):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=90)
        broad = _verified_rule(advisor=advisor, model=None)
        specific = _verified_rule(
            advisor=advisor,
            source_reference=f"reference://m3/{advisor.id}/specific",
            trim="GLE 450 4MATIC",
        )
        _service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            item_key="engine_oil",
            mileage=50000,
            occurred_at=datetime(2026, 1, 1, 9, 0, 0),
        )
        _odometer(
            car=car,
            ownership=ownership,
            advisor=advisor,
            km=59000,
            observed_at=datetime(2026, 9, 12, 8, 0, 0),
        )

        evaluation = MaintenanceStateEngine.evaluate_vehicle(
            car=car,
            evaluated_at=datetime(2026, 9, 12, 12, 0, 0),
        )
        assert len(evaluation.results) == 1
        assert evaluation.results[0].rule_id == specific.id
        assert evaluation.results[0].rule_id != broad.id

        peer = _verified_rule(
            advisor=advisor,
            source_reference=f"reference://m3/{advisor.id}/peer",
            source_version="v2",
            trim="GLE 450 4MATIC",
            interval_km=12000,
        )
        evaluation = MaintenanceStateEngine.evaluate_vehicle(
            car=car,
            evaluated_at=datetime(2026, 9, 12, 12, 0, 0),
        )
        assert len(evaluation.results) == 1
        assert evaluation.results[0].state == "unknown"
        assert evaluation.results[0].rule_id is None
        assert "conflicting_equally_specific_verified_rules" in (
            evaluation.results[0].unknown_reasons
        )
        assert peer.id is not None
