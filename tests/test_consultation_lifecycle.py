from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import Car, CarOwnership, Consultation, User, VehicleAssessment, VehicleEvent
from services.consultation_lifecycle import (
    ConsultationLifecycleError,
    ConsultationLifecycleService,
)
from services.event_emission import EventAuthorityError, emit_vehicle_event


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
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _create_owned_car(owner: User, *, suffix: str = "1") -> tuple[Car, CarOwnership]:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2022,
        vin=f"W1N1671591C22{suffix.zfill(4)}",
        current_mileage=42000,
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"CNS-{suffix.zfill(3)}-LA",
        mileage_at_transfer=42000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return car, ownership


def _create_finalized_assessment(
    consultation: Consultation,
    advisor: User,
) -> VehicleAssessment:
    assessment = VehicleAssessment(
        consultation_id=consultation.id,
        car_id=consultation.car_id,
        advisor_id=advisor.id,
        finalized_by=advisor.id,
        status="finalized",
        is_finalized=True,
        finalized_at=datetime(2026, 8, 20, 10, 30, 0),
        vin=consultation.car.vin,
        mileage_at_assessment=consultation.car.current_mileage or 0,
    )
    db.session.add(assessment)
    db.session.flush()
    return assessment


def test_owner_request_creates_requested_state_and_canonical_event(app):
    with app.app_context():
        owner = _create_user(
            name="Consultation Owner",
            email="consultation-owner@example.com",
            phone="08002000001",
        )
        car, ownership = _create_owned_car(owner)
        preferred_for = datetime(2026, 8, 22, 9, 0, 0)
        occurred_at = datetime(2026, 8, 20, 8, 0, 0)

        consultation = ConsultationLifecycleService.request(
            car_id=car.id,
            actor_user_id=owner.id,
            preferred_for=preferred_for,
            notes="Private review requested.",
            occurred_at=occurred_at,
            source="tests.consultation_request",
        )

        event = VehicleEvent.query.filter_by(
            subject_type="consultation",
            subject_id=consultation.id,
            event_type="consultation.requested",
        ).one()

        assert consultation.status == "requested"
        assert consultation.ownership_id == ownership.id
        assert consultation.client_id == owner.id
        assert consultation.advisor_id is None
        assert consultation.scheduled_for == preferred_for
        assert event.actor_authority == "owner"
        assert event.visibility == "client"
        assert event.previous_state is None
        assert event.new_state == "requested"
        assert event.progression_direction == "not_applicable"
        assert event.data == {"preferred_for": preferred_for.isoformat()}


def test_unrelated_user_cannot_request_consultation(app):
    with app.app_context():
        owner = _create_user(
            name="Actual Owner",
            email="consultation-owner-2@example.com",
            phone="08002000002",
        )
        outsider = _create_user(
            name="Unrelated User",
            email="consultation-outsider@example.com",
            phone="08002000003",
        )
        car, _ownership = _create_owned_car(owner, suffix="2")

        with pytest.raises(ConsultationLifecycleError, match="current vehicle owner"):
            ConsultationLifecycleService.request(
                car_id=car.id,
                actor_user_id=outsider.id,
                preferred_for=datetime(2026, 8, 22, 10, 0, 0),
            )

        assert Consultation.query.count() == 0
        assert VehicleEvent.query.filter_by(subject_type="consultation").count() == 0


def test_advisor_can_create_direct_scheduled_consultation(app):
    with app.app_context():
        owner = _create_user(
            name="Scheduled Owner",
            email="consultation-owner-3@example.com",
            phone="08002000004",
        )
        advisor = _create_user(
            name="Aura Advisor",
            email="consultation-advisor@example.com",
            phone="08002000005",
            role="admin",
        )
        car, ownership = _create_owned_car(owner, suffix="3")
        scheduled_for = datetime(2026, 8, 23, 11, 0, 0)

        consultation = ConsultationLifecycleService.create_scheduled(
            car_id=car.id,
            actor_user_id=advisor.id,
            scheduled_for=scheduled_for,
            occurred_at=datetime(2026, 8, 20, 8, 10, 0),
            source="tests.advisor_schedule",
        )

        event = VehicleEvent.query.filter_by(
            subject_id=consultation.id,
            event_type="consultation.scheduled",
        ).one()

        assert consultation.status == "scheduled"
        assert consultation.ownership_id == ownership.id
        assert consultation.client_id == owner.id
        assert consultation.advisor_id == advisor.id
        assert event.actor_authority == "advisor"
        assert event.previous_state is None
        assert event.new_state == "scheduled"


def test_advisor_schedules_requested_consultation_then_starts_it(app):
    with app.app_context():
        owner = _create_user(
            name="Requested Owner",
            email="consultation-owner-4@example.com",
            phone="08002000006",
        )
        advisor = _create_user(
            name="Assigned Advisor",
            email="consultation-advisor-2@example.com",
            phone="08002000007",
            role="admin",
        )
        car, _ownership = _create_owned_car(owner, suffix="4")

        consultation = ConsultationLifecycleService.request(
            car_id=car.id,
            actor_user_id=owner.id,
            preferred_for=datetime(2026, 8, 24, 9, 0, 0),
            occurred_at=datetime(2026, 8, 20, 8, 20, 0),
        )
        ConsultationLifecycleService.schedule(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            scheduled_for=datetime(2026, 8, 24, 13, 0, 0),
            occurred_at=datetime(2026, 8, 20, 8, 30, 0),
        )
        ConsultationLifecycleService.start(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            started_at=datetime(2026, 8, 24, 13, 1, 0),
        )

        assert consultation.status == "in_progress"
        assert consultation.advisor_id == advisor.id
        assert consultation.started_at == datetime(2026, 8, 24, 13, 1, 0)

        events = (
            VehicleEvent.query.filter_by(
                subject_type="consultation",
                subject_id=consultation.id,
            )
            .order_by(VehicleEvent.occurred_at.asc())
            .all()
        )
        assert [event.event_type for event in events] == [
            "consultation.requested",
            "consultation.scheduled",
            "consultation.started",
        ]
        assert events[1].previous_state == "requested"
        assert events[1].new_state == "scheduled"
        assert events[2].previous_state == "scheduled"
        assert events[2].new_state == "in_progress"


def test_illegal_start_fails_without_partial_mutation(app):
    with app.app_context():
        owner = _create_user(
            name="Illegal State Owner",
            email="consultation-owner-5@example.com",
            phone="08002000008",
        )
        advisor = _create_user(
            name="Illegal State Advisor",
            email="consultation-advisor-3@example.com",
            phone="08002000009",
            role="admin",
        )
        car, _ownership = _create_owned_car(owner, suffix="5")
        consultation = ConsultationLifecycleService.request(
            car_id=car.id,
            actor_user_id=owner.id,
            preferred_for=datetime(2026, 8, 25, 9, 0, 0),
        )

        with pytest.raises(ConsultationLifecycleError, match="Cannot start"):
            ConsultationLifecycleService.start(
                consultation_id=consultation.id,
                actor_user_id=advisor.id,
            )

        assert consultation.status == "requested"
        assert consultation.started_at is None
        assert VehicleEvent.query.filter_by(
            subject_id=consultation.id,
            event_type="consultation.started",
        ).count() == 0


def test_completion_requires_finalized_assessment_then_emits_event(app):
    with app.app_context():
        owner = _create_user(
            name="Completion Owner",
            email="consultation-owner-6@example.com",
            phone="08002000010",
        )
        advisor = _create_user(
            name="Completion Advisor",
            email="consultation-advisor-4@example.com",
            phone="08002000011",
            role="admin",
        )
        car, _ownership = _create_owned_car(owner, suffix="6")
        consultation = ConsultationLifecycleService.create_scheduled(
            car_id=car.id,
            actor_user_id=advisor.id,
            scheduled_for=datetime(2026, 8, 25, 14, 0, 0),
        )
        ConsultationLifecycleService.start(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            started_at=datetime(2026, 8, 25, 14, 0, 0),
        )

        with pytest.raises(ConsultationLifecycleError, match="without an assessment"):
            ConsultationLifecycleService.complete(
                consultation_id=consultation.id,
                actor_user_id=advisor.id,
            )

        assert consultation.status == "in_progress"
        assert consultation.completed_at is None

        assessment = _create_finalized_assessment(consultation, advisor)
        completed_at = datetime(2026, 8, 25, 15, 0, 0)
        ConsultationLifecycleService.complete(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            summary="Internal professional summary.",
            client_visible_summary="Consultation review completed.",
            completed_at=completed_at,
        )

        event = VehicleEvent.query.filter_by(
            subject_id=consultation.id,
            event_type="consultation.completed",
        ).one()

        assert consultation.status == "completed"
        assert consultation.completed_at == completed_at
        assert event.previous_state == "in_progress"
        assert event.new_state == "completed"
        assert event.data == {"assessment_id": assessment.id}
        assert "Internal professional summary" not in (event.description or "")
        assert "Internal professional summary" not in str(event.data)


def test_domain_mutation_can_be_rolled_back_when_event_emission_fails(app, monkeypatch):
    with app.app_context():
        owner = _create_user(
            name="Rollback Owner",
            email="consultation-owner-7@example.com",
            phone="08002000012",
        )
        car, _ownership = _create_owned_car(owner, suffix="7")

        def fail_event_emission(**_kwargs):
            raise RuntimeError("simulated canonical event failure")

        monkeypatch.setattr(
            "services.consultation_lifecycle.emit_vehicle_event",
            fail_event_emission,
        )

        with pytest.raises(RuntimeError, match="canonical event failure"):
            ConsultationLifecycleService.request(
                car_id=car.id,
                actor_user_id=owner.id,
                preferred_for=datetime(2026, 8, 26, 9, 0, 0),
            )

        db.session.rollback()

        assert Consultation.query.count() == 0
        assert VehicleEvent.query.filter_by(subject_type="consultation").count() == 0


def test_consultation_event_replay_is_idempotent(app):
    with app.app_context():
        owner = _create_user(
            name="Replay Owner",
            email="consultation-owner-8@example.com",
            phone="08002000013",
        )
        car, _ownership = _create_owned_car(owner, suffix="8")
        occurred_at = datetime(2026, 8, 20, 9, 0, 0)
        preferred_for = datetime(2026, 8, 27, 9, 0, 0)
        consultation = ConsultationLifecycleService.request(
            car_id=car.id,
            actor_user_id=owner.id,
            preferred_for=preferred_for,
            occurred_at=occurred_at,
            source="tests.replay",
        )

        first = VehicleEvent.query.filter_by(
            subject_id=consultation.id,
            event_type="consultation.requested",
        ).one()

        replay = emit_vehicle_event(
            car_id=car.id,
            event_type="consultation.requested",
            subject_type="consultation",
            subject_id=consultation.id,
            actor_type="user",
            actor_user_id=owner.id,
            visibility="client",
            source="tests.replay",
            occurred_at=occurred_at,
            title="Consultation requested",
            description="A private consultation request was recorded for this vehicle.",
            progression_direction="not_applicable",
            idempotency_key=(
                f"consultation:{consultation.id}:requested:"
                f"{occurred_at.isoformat(timespec='microseconds')}"
            ),
            previous_state=None,
            new_state="requested",
            evidence_refs=[{"type": "consultation", "id": consultation.id}],
            data={"preferred_for": preferred_for.isoformat()},
            mileage=None,
        )

        assert replay.id == first.id
        assert VehicleEvent.query.filter_by(fingerprint=first.fingerprint).count() == 1


def test_owner_cannot_emit_professional_consultation_transition(app):
    with app.app_context():
        owner = _create_user(
            name="Authority Owner",
            email="consultation-owner-9@example.com",
            phone="08002000014",
        )
        car, ownership = _create_owned_car(owner, suffix="9")
        consultation = Consultation(
            car_id=car.id,
            ownership_id=ownership.id,
            advisor_id=None,
            client_id=owner.id,
            status="scheduled",
            scheduled_for=datetime(2026, 8, 28, 9, 0, 0),
        )
        db.session.add(consultation)
        db.session.flush()

        with pytest.raises(EventAuthorityError, match="advisor authority"):
            emit_vehicle_event(
                car_id=car.id,
                event_type="consultation.started",
                subject_type="consultation",
                subject_id=consultation.id,
                actor_type="user",
                actor_user_id=owner.id,
                visibility="client",
                source="tests.authority",
                occurred_at=datetime(2026, 8, 28, 9, 1, 0),
                title="Consultation started",
                progression_direction="not_applicable",
                idempotency_key="owner-cannot-start-consultation",
                previous_state="scheduled",
                new_state="in_progress",
            )
