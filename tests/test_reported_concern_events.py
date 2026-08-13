from __future__ import annotations

from datetime import datetime

import pytest
from flask_login import login_user

from extensions import db
from models import Car, CarFault, CarOwnership, User, VehicleEvent
from services.reported_concern_session_events import ReportedConcernIntegrationError


PASSWORD = "Password123"


def _create_user(
    *,
    name: str,
    email: str,
    phone: str,
    role: str = "user",
) -> User:
    user = User(
        name=name,
        email=email,
        phone_number=phone,
        role=role,
        is_active=True,
        email_verified_at=datetime.utcnow(),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _create_owned_car(owner: User, *, suffix: str) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLC 300 4MATIC",
        year=2023,
        vin=f"W1K253000000{suffix.zfill(5)}",
        current_mileage=28000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"RC-{suffix.zfill(3)}-LA",
            mileage_at_transfer=28000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _events_for(concern_id: int) -> list[VehicleEvent]:
    return (
        VehicleEvent.query.filter_by(
            subject_type="reported_concern",
            subject_id=concern_id,
        )
        .order_by(VehicleEvent.id.asc())
        .all()
    )


def test_new_client_concern_emits_one_canonical_reported_event(app):
    with app.app_context():
        owner = _create_user(
            name="Concern Owner",
            email="concern-owner-1@example.com",
            phone="08002000001",
        )
        car = _create_owned_car(owner, suffix="1")

        concern = CarFault(
            car_id=car.id,
            title="Electrical observation",
            category="electrical_electronics",
            description="Dashboard display went off briefly while driving.",
            status="reported",
            reported_by=owner.id,
            source="client",
            reported_at=datetime(2026, 8, 13, 8, 30, 0),
        )
        db.session.add(concern)
        db.session.commit()

        events = _events_for(concern.id)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "concern.reported"
        assert event.previous_state is None
        assert event.new_state == "reported"
        assert event.progression_direction == "insufficient_evidence"
        assert event.actor_authority == "owner"
        assert event.visibility == "client"
        assert event.source == "client"
        assert event.description is None
        assert event.data == {
            "category": "electrical_electronics",
            "reported_source": "client",
        }
        assert event.evidence_refs == [
            {"type": "reported_concern", "id": concern.id}
        ]


def test_new_advisor_concern_starting_under_review_emits_report_and_review(app):
    with app.app_context():
        owner = _create_user(
            name="Advisor Concern Owner",
            email="concern-owner-2@example.com",
            phone="08002000002",
        )
        advisor = _create_user(
            name="Aura Advisor",
            email="concern-advisor@example.com",
            phone="08002000003",
            role="admin",
        )
        car = _create_owned_car(owner, suffix="2")

        concern = CarFault(
            car_id=car.id,
            title="Advisor observation",
            category="suspension_steering",
            description="Observation recorded during professional review.",
            status="under_review",
            reported_by=advisor.id,
            source="admin",
            reported_at=datetime(2026, 8, 13, 9, 0, 0),
        )
        db.session.add(concern)
        db.session.commit()

        events = _events_for(concern.id)
        assert [event.event_type for event in events] == [
            "concern.reported",
            "concern.review_started",
        ]
        assert events[0].actor_authority == "advisor"
        assert events[1].actor_authority == "advisor"
        assert events[1].previous_state == "reported"
        assert events[1].new_state == "under_review"
        assert events[1].progression_direction == "insufficient_evidence"


def test_existing_concern_transitions_emit_ordered_progression(app):
    with app.app_context():
        owner = _create_user(
            name="Progression Owner",
            email="concern-owner-3@example.com",
            phone="08002000004",
        )
        advisor = _create_user(
            name="Progression Advisor",
            email="concern-advisor-2@example.com",
            phone="08002000005",
            role="admin",
        )
        car = _create_owned_car(owner, suffix="3")

        concern = CarFault(
            car_id=car.id,
            title="Steering observation",
            category="suspension_steering",
            description="Steering feel changed during a drive.",
            status="reported",
            reported_by=owner.id,
            source="client",
            reported_at=datetime(2026, 8, 13, 9, 30, 0),
        )
        db.session.add(concern)
        db.session.commit()

        with app.test_request_context("/admin/concerns/1/review", method="POST"):
            login_user(advisor)
            concern.status = "under_review"
            concern.reviewed_at = datetime(2026, 8, 13, 10, 0, 0)
            concern.reviewed_by = advisor.id
            db.session.commit()

            concern.status = "monitoring"
            db.session.commit()

            concern.status = "resolved"
            concern.resolved_at = datetime(2026, 8, 13, 11, 0, 0)
            concern.resolved_by = advisor.id
            db.session.commit()

        events = _events_for(concern.id)
        assert [event.event_type for event in events] == [
            "concern.reported",
            "concern.review_started",
            "concern.monitoring_started",
            "concern.resolved",
        ]
        assert events[1].previous_state == "reported"
        assert events[1].new_state == "under_review"
        assert events[2].previous_state == "under_review"
        assert events[2].new_state == "monitoring"
        assert events[2].progression_direction == "stable"
        assert events[3].previous_state == "monitoring"
        assert events[3].new_state == "resolved"
        assert events[3].progression_direction == "resolved"
        assert all(event.actor_authority == "advisor" for event in events[1:])


def test_resolved_concern_can_reopen_without_claiming_recurrence(app):
    with app.app_context():
        owner = _create_user(
            name="Reopen Owner",
            email="concern-owner-4@example.com",
            phone="08002000006",
        )
        advisor = _create_user(
            name="Reopen Advisor",
            email="concern-advisor-3@example.com",
            phone="08002000007",
            role="admin",
        )
        car = _create_owned_car(owner, suffix="4")
        concern = CarFault(
            car_id=car.id,
            title="Noise observation",
            category="observation",
            description="An intermittent sound was reported.",
            status="reported",
            reported_by=owner.id,
            source="client",
        )
        db.session.add(concern)
        db.session.commit()

        with app.test_request_context("/admin/concerns/reopen", method="POST"):
            login_user(advisor)
            concern.status = "resolved"
            concern.resolved_at = datetime(2026, 8, 13, 12, 0, 0)
            concern.resolved_by = advisor.id
            db.session.commit()

            concern.status = "reported"
            db.session.commit()

        events = _events_for(concern.id)
        reopened = events[-1]
        assert reopened.event_type == "concern.reopened"
        assert reopened.previous_state == "resolved"
        assert reopened.new_state == "reported"
        assert reopened.progression_direction == "insufficient_evidence"


def test_unsupported_status_transition_blocks_domain_commit(app):
    with app.app_context():
        owner = _create_user(
            name="Invalid Transition Owner",
            email="concern-owner-5@example.com",
            phone="08002000008",
        )
        advisor = _create_user(
            name="Invalid Transition Advisor",
            email="concern-advisor-4@example.com",
            phone="08002000009",
            role="admin",
        )
        car = _create_owned_car(owner, suffix="5")
        concern = CarFault(
            car_id=car.id,
            title="Observation",
            category="observation",
            description="A calm observation.",
            status="monitoring",
            reported_by=advisor.id,
            source="admin",
        )
        db.session.add(concern)
        db.session.commit()

        with app.test_request_context("/admin/concerns/invalid", method="POST"):
            login_user(advisor)
            concern.status = "under_review"
            with pytest.raises(ReportedConcernIntegrationError):
                db.session.commit()
            db.session.rollback()

        refreshed = db.session.get(CarFault, concern.id)
        assert refreshed.status == "monitoring"


def test_event_emission_failure_rolls_back_new_concern(app, monkeypatch):
    with app.app_context():
        owner = _create_user(
            name="Rollback Owner",
            email="concern-owner-6@example.com",
            phone="08002000010",
        )
        car = _create_owned_car(owner, suffix="6")

        import services.reported_concern_session_events as integration

        def fail_emission(**_kwargs):
            raise RuntimeError("forced canonical event failure")

        monkeypatch.setattr(integration, "emit_vehicle_event", fail_emission)

        concern = CarFault(
            car_id=car.id,
            title="Rollback observation",
            category="observation",
            description="This row must not survive without its event.",
            status="reported",
            reported_by=owner.id,
            source="client",
        )
        db.session.add(concern)

        with pytest.raises(RuntimeError, match="forced canonical event failure"):
            db.session.commit()
        db.session.rollback()

        assert CarFault.query.filter_by(title="Rollback observation").count() == 0
        assert VehicleEvent.query.filter_by(
            subject_type="reported_concern"
        ).count() == 0
