from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import (
    Car,
    CarDriver,
    CarFault,
    CarOwnership,
    Consultation,
    TreatmentPlan,
    User,
    VehicleAssessment,
    VehicleEvent,
)
from services.treatment_plan_lifecycle import (
    TreatmentPlanAuthorityError,
    TreatmentPlanLifecycleService,
    TreatmentPlanScopeError,
    TreatmentPlanStateError,
)


def _user(*, name: str, email: str, phone: str, role: str = "user") -> User:
    user = User(
        name=name,
        email=email,
        phone_number=phone,
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _context(*, suffix: str):
    owner = _user(
        name=f"Treatment Owner {suffix}",
        email=f"treatment-owner-{suffix}@example.com",
        phone=f"0811000{suffix.zfill(4)}",
    )
    advisor = _user(
        name=f"Treatment Advisor {suffix}",
        email=f"treatment-advisor-{suffix}@example.com",
        phone=f"0812000{suffix.zfill(4)}",
        role="admin",
    )
    driver = _user(
        name=f"Treatment Driver {suffix}",
        email=f"treatment-driver-{suffix}@example.com",
        phone=f"0813000{suffix.zfill(4)}",
        role="driver",
    )
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1NTREATMENT{suffix.zfill(5)}",
        engine_type="M256",
        transmission_type="9G-TRONIC",
        current_mileage=25000,
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"TR-{suffix.zfill(3)}-LA",
        mileage_at_transfer=24000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.flush()
    db.session.add(
        CarDriver(
            user_id=driver.id,
            car_id=car.id,
            is_active=True,
        )
    )
    consultation = Consultation(
        car_id=car.id,
        ownership_id=ownership.id,
        advisor_id=advisor.id,
        client_id=owner.id,
        status="completed",
        scheduled_for=datetime(2026, 8, 29, 8, 0, 0),
        started_at=datetime(2026, 8, 29, 8, 1, 0),
        completed_at=datetime(2026, 8, 29, 8, 30, 0),
    )
    db.session.add(consultation)
    db.session.flush()
    assessment = VehicleAssessment(
        car_id=car.id,
        consultation_id=consultation.id,
        advisor_id=advisor.id,
        vin=car.vin,
        mileage_at_assessment=car.current_mileage,
        status="finalized",
        is_finalized=True,
        engine_status="stable",
        transmission_status="stable",
        suspension_status="stable",
        electrical_status="stable",
        cooling_status="stable",
        finalized_at=datetime(2026, 8, 29, 8, 25, 0),
        finalized_by=advisor.id,
    )
    db.session.add(assessment)
    db.session.flush()
    db.session.commit()
    return owner, advisor, driver, car, ownership, consultation, assessment


def _plan(*, assessment: VehicleAssessment, advisor: User, status: str) -> TreatmentPlan:
    plan = TreatmentPlan(
        car_id=assessment.car_id,
        consultation_id=assessment.consultation_id,
        assessment_id=assessment.id,
        advisor_id=advisor.id,
        title="Vehicle Treatment Plan",
        internal_instructions="Advisor-only test instructions",
        client_summary="A professional treatment pathway is available.",
        status=status,
    )
    db.session.add(plan)
    db.session.flush()
    return plan


def _events(plan_id: int) -> list[VehicleEvent]:
    return (
        VehicleEvent.query.filter_by(
            subject_type="treatment_plan",
            subject_id=plan_id,
        )
        .order_by(VehicleEvent.id.asc())
        .all()
    )


def test_new_assessment_plan_becomes_proposed_with_canonical_event(app):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, _consultation, assessment = (
            _context(suffix="1")
        )
        plan = _plan(assessment=assessment, advisor=advisor, status="approved")

        result = TreatmentPlanLifecycleService.canonicalize_new_assessment_plan(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 29, 9, 0, 0),
            source="tests.treatment_propose",
        )
        db.session.commit()

        event = _events(plan.id)[0]
        assert result.id == plan.id
        assert result.status == "proposed"
        assert event.event_type == "treatment.proposed"
        assert event.previous_state is None
        assert event.new_state == "proposed"
        assert event.actor_authority == "advisor"
        assert event.visibility == "client"
        assert event.progression_direction == "not_applicable"
        assert event.data == {
            "assessment_id": assessment.id,
            "consultation_id": assessment.consultation_id,
        }
        assert "Advisor-only test instructions" not in str(event.data)


def test_active_owner_authorizes_proposed_plan_and_advisor_starts(app):
    with app.app_context():
        owner, advisor, _driver, _car, _ownership, _consultation, assessment = (
            _context(suffix="2")
        )
        plan = _plan(assessment=assessment, advisor=advisor, status="proposed")
        db.session.commit()

        TreatmentPlanLifecycleService.authorize(
            plan_id=plan.id,
            actor_user_id=owner.id,
            occurred_at=datetime(2026, 8, 29, 9, 5, 0),
        )
        TreatmentPlanLifecycleService.start(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 29, 9, 10, 0),
        )
        db.session.commit()

        events = _events(plan.id)
        assert plan.status == "in_progress"
        assert [event.event_type for event in events] == [
            "treatment.authorized",
            "treatment.started",
        ]
        assert events[0].actor_authority == "owner"
        assert events[0].previous_state == "proposed"
        assert events[0].new_state == "authorized"
        assert events[1].actor_authority == "advisor"
        assert events[1].previous_state == "authorized"
        assert events[1].new_state == "in_progress"


def test_advisor_cannot_fabricate_owner_authorization(app):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, _consultation, assessment = (
            _context(suffix="3")
        )
        plan = _plan(assessment=assessment, advisor=advisor, status="proposed")
        db.session.commit()

        with pytest.raises(TreatmentPlanAuthorityError, match="active owner"):
            TreatmentPlanLifecycleService.authorize(
                plan_id=plan.id,
                actor_user_id=advisor.id,
            )

        assert plan.status == "proposed"
        assert _events(plan.id) == []


def test_driver_cannot_authorize_or_start_treatment(app):
    with app.app_context():
        _owner, _advisor, driver, _car, _ownership, _consultation, assessment = (
            _context(suffix="4")
        )
        plan = TreatmentPlan(
            car_id=assessment.car_id,
            consultation_id=assessment.consultation_id,
            assessment_id=assessment.id,
            advisor_id=assessment.finalized_by,
            title="Vehicle Treatment Plan",
            client_summary="Client-safe summary",
            status="proposed",
        )
        db.session.add(plan)
        db.session.commit()

        with pytest.raises(TreatmentPlanAuthorityError):
            TreatmentPlanLifecycleService.authorize(
                plan_id=plan.id,
                actor_user_id=driver.id,
            )
        with pytest.raises(TreatmentPlanAuthorityError):
            TreatmentPlanLifecycleService.start(
                plan_id=plan.id,
                actor_user_id=driver.id,
            )

        assert plan.status == "proposed"
        assert _events(plan.id) == []


def test_proposed_plan_cannot_start_without_owner_authorization(app):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, _consultation, assessment = (
            _context(suffix="5")
        )
        plan = _plan(assessment=assessment, advisor=advisor, status="proposed")
        db.session.commit()

        with pytest.raises(TreatmentPlanStateError, match="cannot start"):
            TreatmentPlanLifecycleService.start(
                plan_id=plan.id,
                actor_user_id=advisor.id,
            )

        assert plan.status == "proposed"
        assert _events(plan.id) == []


def test_monitoring_completion_does_not_resolve_concern(app):
    with app.app_context():
        owner, advisor, _driver, car, _ownership, _consultation, assessment = (
            _context(suffix="6")
        )
        plan = _plan(assessment=assessment, advisor=advisor, status="proposed")
        concern = CarFault(
            car_id=car.id,
            reported_by=owner.id,
            category="other",
            description="Observe after treatment",
            status="monitoring",
        )
        db.session.add(concern)
        db.session.commit()

        TreatmentPlanLifecycleService.authorize(
            plan_id=plan.id,
            actor_user_id=owner.id,
        )
        TreatmentPlanLifecycleService.start(
            plan_id=plan.id,
            actor_user_id=advisor.id,
        )
        TreatmentPlanLifecycleService.start_monitoring(
            plan_id=plan.id,
            actor_user_id=advisor.id,
        )
        TreatmentPlanLifecycleService.complete(
            plan_id=plan.id,
            actor_user_id=advisor.id,
        )
        db.session.commit()

        assert plan.status == "completed"
        assert concern.status == "monitoring"
        assert [event.event_type for event in _events(plan.id)] == [
            "treatment.authorized",
            "treatment.started",
            "treatment.monitoring_started",
            "treatment.completed",
        ]
        completed = _events(plan.id)[-1]
        assert completed.progression_direction == "not_applicable"


def test_legacy_approved_plan_starts_without_synthetic_history(app):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, _consultation, assessment = (
            _context(suffix="7")
        )
        plan = _plan(assessment=assessment, advisor=advisor, status="approved")
        db.session.commit()

        TreatmentPlanLifecycleService.start(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 29, 9, 30, 0),
        )
        db.session.commit()

        events = _events(plan.id)
        assert plan.status == "in_progress"
        assert len(events) == 1
        assert events[0].event_type == "treatment.started"
        assert events[0].previous_state == "approved"
        assert events[0].new_state == "in_progress"


def test_repeated_start_is_idempotent_after_committed_transition(app):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, _consultation, assessment = (
            _context(suffix="8")
        )
        plan = _plan(assessment=assessment, advisor=advisor, status="approved")
        db.session.commit()

        TreatmentPlanLifecycleService.start(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 29, 9, 35, 0),
        )
        db.session.commit()
        TreatmentPlanLifecycleService.start(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 29, 9, 36, 0),
        )
        db.session.commit()

        assert plan.status == "in_progress"
        assert [event.event_type for event in _events(plan.id)] == [
            "treatment.started"
        ]


def test_event_failure_rolls_back_treatment_state(app, monkeypatch):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, _consultation, assessment = (
            _context(suffix="9")
        )
        plan = _plan(assessment=assessment, advisor=advisor, status="approved")
        db.session.commit()

        def fail_event(**_kwargs):
            raise RuntimeError("forced treatment event failure")

        monkeypatch.setattr(
            "services.treatment_plan_lifecycle.emit_treatment_plan_event",
            fail_event,
        )

        with pytest.raises(RuntimeError, match="forced treatment event failure"):
            TreatmentPlanLifecycleService.start(
                plan_id=plan.id,
                actor_user_id=advisor.id,
            )
        db.session.rollback()

        persisted = db.session.get(TreatmentPlan, plan.id)
        assert persisted.status == "approved"
        assert _events(plan.id) == []


def test_cross_vehicle_assessment_scope_fails_closed(app):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, _consultation, assessment = (
            _context(suffix="10")
        )
        other_owner, _other_advisor, _other_driver, other_car, *_rest = _context(
            suffix="11"
        )
        assert other_owner.id > 0

        plan = TreatmentPlan(
            car_id=other_car.id,
            consultation_id=assessment.consultation_id,
            assessment_id=assessment.id,
            advisor_id=advisor.id,
            title="Cross-scope plan",
            status="approved",
        )
        db.session.add(plan)
        db.session.commit()

        with pytest.raises(TreatmentPlanScopeError, match="vehicle scope"):
            TreatmentPlanLifecycleService.start(
                plan_id=plan.id,
                actor_user_id=advisor.id,
            )

        assert plan.status == "approved"
        assert _events(plan.id) == []


def test_runtime_treatment_endpoints_are_cut_over(app):
    expected_modules = {
        "admin.start_treatment_plan": "services.treatment_plan_route_cutover",
        "admin.complete_treatment_plan": "services.treatment_plan_route_cutover",
        "admin.defer_treatment_plan": "services.treatment_plan_route_cutover",
    }
    for endpoint, module_name in expected_modules.items():
        assert app.view_functions[endpoint].__module__ == module_name

    assert "cars.authorize_treatment_plan" in app.view_functions
