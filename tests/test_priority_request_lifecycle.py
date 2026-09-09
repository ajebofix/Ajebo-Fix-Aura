from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from extensions import db
from models import Car, CarOwnership, Consultation, User, VehicleEvent
from priority.lifecycle import (
    PriorityRequestAuthorityError,
    PriorityRequestError,
    PriorityRequestLifecycleService,
)
from priority.models import PriorityRequest
from services.consultation_lifecycle import ConsultationLifecycleService


def _user(*, suffix: str, role: str = "user") -> User:
    user = User(
        name=f"User {suffix}",
        email=f"priority-{suffix}@example.com",
        phone_number=f"0809000{suffix.zfill(4)}",
        role=role,
        is_active=True,
    )
    user.set_password("safe-test-password")
    db.session.add(user)
    db.session.flush()
    return user


def _owned_car(owner: User, *, suffix: str = "1", care_plan: str = "priority_access"):
    car = Car(
        brand="Mercedes Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin=f"PRIORITYVIN{suffix.zfill(6)}",
        current_mileage=64100,
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"PR-{suffix}",
        is_active=True,
        care_plan=care_plan,
    )
    db.session.add(ownership)
    db.session.flush()
    return car, ownership


def _priority_events(request_id: int):
    return (
        VehicleEvent.query.filter_by(
            subject_type="priority_request",
            subject_id=request_id,
        )
        .order_by(VehicleEvent.id.asc())
        .all()
    )


def test_owner_request_is_durable_idempotent_and_emits_canonical_event(app):
    with app.app_context():
        owner = _user(suffix="1")
        car, ownership = _owned_car(owner, suffix="1")

        first = PriorityRequestLifecycleService.create_request(
            car_id=car.id,
            actor_user_id=owner.id,
            request_kind="priority",
            request_source="owner",
        )
        db.session.commit()

        replay = PriorityRequestLifecycleService.create_request(
            car_id=car.id,
            actor_user_id=owner.id,
            request_kind="priority",
            request_source="owner",
        )
        db.session.commit()

        assert first.id == replay.id
        assert first.ownership_id == ownership.id
        assert first.status == "requested"
        assert first.eligibility_at_request is True
        assert PriorityRequest.query.count() == 1

        events = _priority_events(first.id)
        assert [event.event_type for event in events] == ["priority.requested"]
        assert events[0].previous_state is None
        assert events[0].new_state == "requested"
        assert events[0].actor_authority == "owner"
        assert events[0].visibility == "client"
        assert events[0].progression_direction == "not_applicable"


def test_owner_without_entitlement_fails_closed(app):
    with app.app_context():
        owner = _user(suffix="2")
        car, _ownership = _owned_car(
            owner,
            suffix="2",
            care_plan="active_monitoring",
        )

        with pytest.raises(PriorityRequestAuthorityError):
            PriorityRequestLifecycleService.create_request(
                car_id=car.id,
                actor_user_id=owner.id,
                request_kind="priority",
                request_source="owner",
            )
        db.session.rollback()
        assert PriorityRequest.query.count() == 0


def test_professional_transition_chain_is_explicit_and_auditable(app):
    with app.app_context():
        owner = _user(suffix="3")
        advisor = _user(suffix="4", role="admin")
        car, _ownership = _owned_car(owner, suffix="3")
        row = PriorityRequestLifecycleService.create_request(
            car_id=car.id,
            actor_user_id=owner.id,
            request_kind="emergency_review",
            request_source="owner",
        )
        db.session.commit()

        PriorityRequestLifecycleService.start_review(
            request_id=row.id,
            actor_user_id=advisor.id,
            note="Reviewing current operational context.",
        )
        PriorityRequestLifecycleService.accept(
            request_id=row.id,
            actor_user_id=advisor.id,
        )
        PriorityRequestLifecycleService.resolve(
            request_id=row.id,
            actor_user_id=advisor.id,
        )
        db.session.commit()

        assert row.status == "resolved"
        assert row.review_started_at is not None
        assert row.accepted_at is not None
        assert row.resolved_at is not None
        assert row.advisor_review_note == "Reviewing current operational context."

        events = _priority_events(row.id)
        assert [event.event_type for event in events] == [
            "priority.requested",
            "priority.review_started",
            "priority.accepted",
            "priority.resolved",
        ]
        assert events[1].visibility == "advisor"
        assert all(event.progression_direction == "not_applicable" for event in events)


def test_accept_cannot_skip_advisor_review(app):
    with app.app_context():
        owner = _user(suffix="5")
        advisor = _user(suffix="6", role="admin")
        car, _ownership = _owned_car(owner, suffix="5")
        row = PriorityRequestLifecycleService.create_request(
            car_id=car.id,
            actor_user_id=owner.id,
            request_kind="priority",
            request_source="owner",
        )
        db.session.commit()

        with pytest.raises(PriorityRequestError):
            PriorityRequestLifecycleService.accept(
                request_id=row.id,
                actor_user_id=advisor.id,
            )
        db.session.rollback()
        assert db.session.get(PriorityRequest, row.id).status == "requested"


def test_requesting_owner_can_cancel_before_acceptance(app):
    with app.app_context():
        owner = _user(suffix="7")
        car, _ownership = _owned_car(owner, suffix="7")
        row = PriorityRequestLifecycleService.create_request(
            car_id=car.id,
            actor_user_id=owner.id,
            request_kind="priority",
            request_source="owner",
        )
        db.session.commit()

        PriorityRequestLifecycleService.cancel(
            request_id=row.id,
            actor_user_id=owner.id,
        )
        db.session.commit()

        assert row.status == "cancelled"
        events = _priority_events(row.id)
        assert events[-1].event_type == "priority.cancelled"
        assert events[-1].actor_authority == "owner"


def test_driver_cannot_create_or_mutate_priority_request(app):
    with app.app_context():
        owner = _user(suffix="8")
        driver = _user(suffix="9", role="driver")
        car, _ownership = _owned_car(owner, suffix="8")

        with pytest.raises(PriorityRequestAuthorityError):
            PriorityRequestLifecycleService.create_request(
                car_id=car.id,
                actor_user_id=driver.id,
                request_kind="priority",
                request_source="owner",
            )
        db.session.rollback()


def test_accepted_priority_request_links_real_consultation_without_merging_lifecycles(app):
    with app.app_context():
        owner = _user(suffix="10")
        advisor = _user(suffix="11", role="admin")
        car, _ownership = _owned_car(owner, suffix="10")
        row = PriorityRequestLifecycleService.create_request(
            car_id=car.id,
            actor_user_id=owner.id,
            request_kind="priority",
            request_source="owner",
        )
        db.session.commit()

        PriorityRequestLifecycleService.start_review(
            request_id=row.id,
            actor_user_id=advisor.id,
        )
        PriorityRequestLifecycleService.accept(
            request_id=row.id,
            actor_user_id=advisor.id,
        )
        consultation = ConsultationLifecycleService.create_scheduled(
            car_id=car.id,
            actor_user_id=advisor.id,
            scheduled_for=datetime.utcnow() + timedelta(days=1),
            source="priority.test",
        )
        PriorityRequestLifecycleService.link_consultation(
            request_id=row.id,
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
        )
        db.session.commit()

        assert row.consultation_id == consultation.id
        assert db.session.get(Consultation, consultation.id).status == "scheduled"

        priority_types = [event.event_type for event in _priority_events(row.id)]
        consultation_types = [
            event.event_type
            for event in VehicleEvent.query.filter_by(
                subject_type="consultation",
                subject_id=consultation.id,
            ).all()
        ]
        assert priority_types == [
            "priority.requested",
            "priority.review_started",
            "priority.accepted",
        ]
        assert consultation_types == ["consultation.scheduled"]


def test_wave_2_4d_cutover_replaces_legacy_owner_priority_endpoints(app):
    assert app.view_functions["cars.request_priority_scheduling"].__module__ == (
        "services.priority_route_cutover"
    )
    assert app.view_functions["cars.request_emergency_review"].__module__ == (
        "services.priority_route_cutover"
    )
    assert "admin.admin_priority_queue" in app.view_functions
    assert "cars.client_priority_status" in app.view_functions
