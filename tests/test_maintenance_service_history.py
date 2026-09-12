from __future__ import annotations

from datetime import datetime
import hashlib

import pytest

from extensions import db
from maintenance.knowledge_service import MaintenanceKnowledgeService
from maintenance.models import MaintenanceServiceClassification
from maintenance.reevaluation import MaintenanceReevaluationService
from maintenance.service_history import (
    MaintenanceServiceClassificationAuthorityError,
    MaintenanceServiceClassificationError,
    ServiceHistoryNormalizationService,
)
from maintenance.state_engine import MaintenanceStateEngine
from models import Car, CarOwnership, User, VehicleEvent
from services.mileage_observations import MileageObservationService


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"M4 {role} {suffix}",
        email=f"m4-{role}-{suffix}@example.com",
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
        vin=f"W1NM4{suffix:012d}",
        current_mileage=None,
        vehicle_identity_source="manual",
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"M4-{suffix:03d}-LA",
        mileage_at_transfer=0,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return owner, advisor, car, ownership


def _verified_rule(
    *,
    advisor: User,
    item_key: str = "engine_oil",
    display_name: str = "Engine oil service",
    car_id: int | None = None,
    interval_km: int | None = 10000,
    interval_months: int | None = None,
):
    rule = MaintenanceKnowledgeService.create_candidate(
        maintenance_item_key=item_key,
        display_name=display_name,
        interval_km=interval_km,
        interval_months=interval_months,
        source_type="oem",
        source_name="Reviewed manufacturer schedule reference",
        source_reference=f"reference://m4/{advisor.id}/{item_key}/{car_id or 'brand'}",
        source_version="v1",
        car_id=car_id,
        brand=None if car_id is not None else "Mercedes-Benz",
        model=None if car_id is not None else "GLE 450",
        year_start=None if car_id is not None else 2021,
        year_end=None if car_id is not None else 2021,
    )
    MaintenanceKnowledgeService.verify(
        rule_id=rule.id,
        actor_user_id=advisor.id,
        occurred_at=datetime(2026, 9, 12, 10, 0, 0),
    )
    db.session.commit()
    return rule


def _service(*, car: Car, ownership: CarOwnership, advisor: User, mileage: int = 50000):
    token = f"m4|{car.id}|{mileage}|{VehicleEvent.query.count()}"
    event = VehicleEvent(
        car_id=car.id,
        ownership_id=ownership.id,
        event_type="service",
        title="Engine service recorded",
        description="A saved service fact. Free text is not classification authority.",
        mileage=mileage,
        source="admin_current",
        data={"record_mode": "current", "entered_by_role": "advisor"},
        fingerprint=hashlib.sha256(token.encode()).hexdigest(),
        created_by=advisor.id,
        is_deleted=False,
        created_at=datetime(2026, 1, 15, 9, 0, 0),
        schema_version=1,
        occurred_at=datetime(2026, 1, 15, 9, 0, 0),
        recorded_at=datetime(2026, 1, 15, 9, 0, 0),
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


def _odometer(*, car: Car, ownership: CarOwnership, advisor: User, km: int):
    MileageObservationService.record(
        car=car,
        odometer_km=km,
        source="advisor_observation",
        verification_status="advisor_verified",
        observed_at=datetime(2026, 9, 12, 12, 0, 0),
        recorded_by_user_id=advisor.id,
        ownership_id=ownership.id,
        review_status="not_required",
        advance_current=True,
        commit=True,
    )


def test_only_advisor_can_use_verified_vehicle_vocabulary(app):
    with app.app_context():
        owner, advisor, car, ownership = _vehicle(suffix=1)
        rule = _verified_rule(advisor=advisor)
        event = _service(car=car, ownership=ownership, advisor=advisor)

        options = ServiceHistoryNormalizationService.classification_options(car)
        assert [(option.maintenance_item_key, option.knowledge_rule_id) for option in options] == [
            ("engine_oil", rule.id)
        ]

        with pytest.raises(MaintenanceServiceClassificationAuthorityError):
            ServiceHistoryNormalizationService.classify(
                service_event_id=event.id,
                actor_user_id=owner.id,
                maintenance_item_key="engine_oil",
                classification_source="advisor_review",
            )

        with pytest.raises(MaintenanceServiceClassificationError):
            ServiceHistoryNormalizationService.classify(
                service_event_id=event.id,
                actor_user_id=advisor.id,
                maintenance_item_key="invented_service",
                classification_source="advisor_review",
            )


def test_classification_is_idempotent_and_preserves_correction_history(app):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=2)
        _verified_rule(advisor=advisor, item_key="engine_oil")
        _verified_rule(
            advisor=advisor,
            item_key="brake_fluid",
            display_name="Brake fluid service",
            interval_km=None,
            interval_months=24,
        )
        event = _service(car=car, ownership=ownership, advisor=advisor)

        first = ServiceHistoryNormalizationService.classify(
            service_event_id=event.id,
            actor_user_id=advisor.id,
            maintenance_item_key="engine_oil",
            classification_source="advisor_review",
        )
        replay = ServiceHistoryNormalizationService.classify(
            service_event_id=event.id,
            actor_user_id=advisor.id,
            maintenance_item_key="engine_oil",
            classification_source="advisor_review",
        )
        db.session.commit()

        assert first.id == replay.id
        assert MaintenanceServiceClassification.query.count() == 1
        assert event.data["maintenance_item_key"] == "engine_oil"
        assert event.data["maintenance_item_classification_status"] == "advisor_verified"

        replacement = ServiceHistoryNormalizationService.classify(
            service_event_id=event.id,
            actor_user_id=advisor.id,
            maintenance_item_key="brake_fluid",
            classification_source="advisor_review",
        )
        db.session.commit()

        db.session.refresh(first)
        assert first.status == "superseded"
        assert first.superseded_by_id == replacement.id
        assert replacement.status == "active"
        assert event.data["maintenance_item_key"] == "brake_fluid"
        assert len(ServiceHistoryNormalizationService.history_for_event(event.id)) == 2

        removed = ServiceHistoryNormalizationService.clear(
            service_event_id=event.id,
            actor_user_id=advisor.id,
        )
        db.session.commit()

        assert removed.id == replacement.id
        assert removed.status == "removed"
        assert ServiceHistoryNormalizationService.active_for_event(event.id) is None
        assert "maintenance_item_key" not in event.data


def test_vehicle_specific_vocabulary_cannot_cross_vehicle_boundary(app):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=3)
        _other_owner, other_advisor, other_car, other_ownership = _vehicle(suffix=4)
        _verified_rule(advisor=advisor, car_id=car.id)
        other_event = _service(
            car=other_car,
            ownership=other_ownership,
            advisor=other_advisor,
        )

        assert ServiceHistoryNormalizationService.classification_options(other_car) == ()
        with pytest.raises(MaintenanceServiceClassificationError):
            ServiceHistoryNormalizationService.classify(
                service_event_id=other_event.id,
                actor_user_id=other_advisor.id,
                maintenance_item_key="engine_oil",
                classification_source="advisor_review",
            )


def test_m3_consumes_advisor_classification_and_clear_returns_to_unknown(app):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=5)
        rule = _verified_rule(advisor=advisor)
        event = _service(car=car, ownership=ownership, advisor=advisor, mileage=50000)
        _odometer(car=car, ownership=ownership, advisor=advisor, km=59000)

        before = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=datetime(2026, 9, 12, 14, 0, 0),
        )
        assert before.state == "unknown"
        assert before.unknown_reasons == ("no_matching_service_baseline",)

        ServiceHistoryNormalizationService.classify(
            service_event_id=event.id,
            actor_user_id=advisor.id,
            maintenance_item_key="engine_oil",
            classification_source="advisor_review",
        )
        db.session.commit()

        classified = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=datetime(2026, 9, 12, 14, 0, 0),
        )
        assert classified.state == "due"
        assert classified.latest_matching_service_event_id == event.id
        assert classified.next_due_mileage == 60000

        ServiceHistoryNormalizationService.clear(
            service_event_id=event.id,
            actor_user_id=advisor.id,
        )
        db.session.commit()

        cleared = MaintenanceStateEngine.evaluate_rule(
            car=car,
            rule=rule,
            evaluated_at=datetime(2026, 9, 12, 14, 0, 0),
        )
        assert cleared.state == "unknown"
        assert cleared.unknown_reasons == ("no_matching_service_baseline",)


def test_verified_knowledge_decision_requests_read_only_reevaluation(app, monkeypatch):
    with app.app_context():
        _owner, advisor, car, _ownership = _vehicle(suffix=6)
        calls = []

        monkeypatch.setattr(
            MaintenanceReevaluationService,
            "safe_evaluate_rule_change",
            staticmethod(lambda **kwargs: calls.append(kwargs) or ()),
        )

        rule = MaintenanceKnowledgeService.create_candidate(
            maintenance_item_key="engine_oil",
            display_name="Engine oil service",
            interval_km=10000,
            interval_months=None,
            source_type="oem",
            source_name="Reviewed manufacturer schedule reference",
            source_reference="reference://m4/reevaluation",
            source_version="v1",
            car_id=car.id,
        )
        MaintenanceKnowledgeService.verify(
            rule_id=rule.id,
            actor_user_id=advisor.id,
        )

        assert calls == [
            {
                "rule_id": rule.id,
                "trigger": "maintenance_knowledge_verified",
            }
        ]
