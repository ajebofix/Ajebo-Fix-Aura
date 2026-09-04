from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import Car, CarDriver, CarOwnership, TreatmentPlan, User, VehicleEvent
from services.treatment_action_lifecycle import (
    TreatmentActionAuthorityError,
    TreatmentActionIdempotencyConflict,
    TreatmentActionLifecycleService,
    TreatmentActionScopeError,
    TreatmentActionStateError,
)
from services.treatment_plan_lifecycle import TreatmentPlanLifecycleService
from treatment.models import TreatmentAction


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Action {role} {suffix}",
        email=f"action-{role}-{suffix}@example.com",
        phone_number=f"+2348971{suffix:06d}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _context(*, suffix: int, plan_status: str = "authorized"):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 1000, role="admin")
    driver = _user(suffix=suffix + 2000, role="driver")

    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1NACTION{suffix:09d}",
        current_mileage=25000,
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"AC-{suffix:03d}-LA",
        mileage_at_transfer=24000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.add(CarDriver(user_id=driver.id, car_id=car.id, is_active=True))
    db.session.flush()

    plan = TreatmentPlan(
        car_id=car.id,
        advisor_id=advisor.id,
        title="Vehicle Treatment Plan",
        client_summary="A professional treatment pathway is available.",
        internal_instructions="Advisor-only plan instructions",
        status=plan_status,
    )
    db.session.add(plan)
    db.session.commit()
    return owner, advisor, driver, car, ownership, plan


def _action_events(action_id: int) -> list[VehicleEvent]:
    return (
        VehicleEvent.query.filter_by(
            subject_type="treatment_action",
            subject_id=action_id,
        )
        .order_by(VehicleEvent.id.asc())
        .all()
    )


def test_advisor_creates_planned_action_with_canonical_event(app):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, plan = _context(suffix=1)

        action = TreatmentActionLifecycleService.create(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            creation_key="front-brakes-1",
            title="Front brake fluid service",
            client_summary="Refresh front braking-system fluid as planned.",
            internal_instructions="Advisor-only execution notes",
            occurred_at=datetime(2026, 8, 29, 18, 0, 0),
        )
        db.session.commit()

        events = _action_events(action.id)
        assert action.status == "planned"
        assert action.car_id == plan.car_id
        assert len(events) == 1
        assert events[0].event_type == "treatment_action.created"
        assert events[0].previous_state is None
        assert events[0].new_state == "planned"
        assert events[0].actor_authority == "advisor"
        assert events[0].progression_direction == "not_applicable"
        assert events[0].data == {"treatment_plan_id": plan.id}
        assert "Advisor-only execution notes" not in str(events[0].data)


def test_action_creation_key_is_idempotent_and_conflict_safe(app):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, plan = _context(suffix=2)

        first = TreatmentActionLifecycleService.create(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            creation_key="spark-plugs",
            title="Spark plug replacement",
            client_summary="Replace spark plugs within the approved care pathway.",
        )
        db.session.commit()

        second = TreatmentActionLifecycleService.create(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            creation_key="spark-plugs",
            title="Spark plug replacement",
            client_summary="Replace spark plugs within the approved care pathway.",
        )
        db.session.commit()

        assert second.id == first.id
        assert TreatmentAction.query.filter_by(treatment_plan_id=plan.id).count() == 1
        assert len(_action_events(first.id)) == 1

        with pytest.raises(TreatmentActionIdempotencyConflict):
            TreatmentActionLifecycleService.create(
                plan_id=plan.id,
                actor_user_id=advisor.id,
                creation_key="spark-plugs",
                title="Different intervention",
                client_summary="Different semantics must fail closed.",
            )


def test_owner_and_driver_cannot_create_professional_action(app):
    with app.app_context():
        owner, advisor, driver, _car, _ownership, plan = _context(suffix=3)

        for actor in (owner, driver):
            with pytest.raises(TreatmentActionAuthorityError):
                TreatmentActionLifecycleService.create(
                    plan_id=plan.id,
                    actor_user_id=actor.id,
                    creation_key=f"blocked-{actor.id}",
                    title="Blocked professional action",
                )

        assert TreatmentAction.query.filter_by(treatment_plan_id=plan.id).count() == 0
        assert advisor.role == "admin"


def test_action_cannot_be_scheduled_before_parent_plan_is_scheduled(app):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, plan = _context(
            suffix=4,
            plan_status="authorized",
        )
        action = TreatmentActionLifecycleService.create(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            creation_key="schedule-gate",
            title="Scheduled intervention",
        )
        db.session.commit()

        with pytest.raises(TreatmentActionStateError, match="parent Treatment Plan"):
            TreatmentActionLifecycleService.schedule(
                action_id=action.id,
                actor_user_id=advisor.id,
                scheduled_for=datetime(2026, 8, 30, 10, 0, 0),
            )

        assert action.status == "planned"
        assert [event.event_type for event in _action_events(action.id)] == [
            "treatment_action.created"
        ]


def test_action_flow_does_not_auto_complete_parent_plan(app):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, plan = _context(
            suffix=5,
            plan_status="authorized",
        )
        action = TreatmentActionLifecycleService.create(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            creation_key="fluid-service",
            title="Brake fluid service",
        )

        TreatmentPlanLifecycleService.schedule(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 29, 18, 10, 0),
        )
        TreatmentActionLifecycleService.schedule(
            action_id=action.id,
            actor_user_id=advisor.id,
            scheduled_for=datetime(2026, 8, 30, 10, 0, 0),
            occurred_at=datetime(2026, 8, 29, 18, 11, 0),
        )
        TreatmentPlanLifecycleService.start(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 30, 10, 0, 0),
        )
        TreatmentActionLifecycleService.start(
            action_id=action.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 30, 10, 1, 0),
        )
        TreatmentActionLifecycleService.complete(
            action_id=action.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 30, 11, 0, 0),
        )
        db.session.commit()

        assert action.status == "completed"
        assert plan.status == "in_progress"
        assert [event.event_type for event in _action_events(action.id)] == [
            "treatment_action.created",
            "treatment_action.scheduled",
            "treatment_action.started",
            "treatment_action.completed",
        ]


def test_cross_vehicle_action_plan_mismatch_fails_closed(app):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, plan = _context(
            suffix=6,
            plan_status="scheduled",
        )
        other_owner = _user(suffix=7006)
        other_car = Car(
            brand="Mercedes-Benz",
            model="GLC 300",
            year=2023,
            vin="W1NACTIONCROSS00001",
            current_mileage=20000,
        )
        db.session.add(other_car)
        db.session.flush()
        db.session.add(
            CarOwnership(
                user_id=other_owner.id,
                car_id=other_car.id,
                plate_number="CROSS-01",
                mileage_at_transfer=20000,
                is_active=True,
            )
        )
        bad_action = TreatmentAction(
            treatment_plan_id=plan.id,
            car_id=other_car.id,
            created_by_user_id=advisor.id,
            creation_key="bad-scope",
            title="Cross-vehicle action",
            status="planned",
            visibility="client",
        )
        db.session.add(bad_action)
        db.session.commit()

        with pytest.raises(TreatmentActionScopeError, match="vehicle scope disagree"):
            TreatmentActionLifecycleService.schedule(
                action_id=bad_action.id,
                actor_user_id=advisor.id,
                scheduled_for=datetime(2026, 8, 30, 10, 0, 0),
            )

        assert bad_action.status == "planned"
        assert _action_events(bad_action.id) == []


def test_terminal_parent_plan_rejects_new_action(app):
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership, plan = _context(
            suffix=7,
            plan_status="completed",
        )

        with pytest.raises(TreatmentActionStateError, match="parent plan"):
            TreatmentActionLifecycleService.create(
                plan_id=plan.id,
                actor_user_id=advisor.id,
                creation_key="too-late",
                title="Late action",
            )
