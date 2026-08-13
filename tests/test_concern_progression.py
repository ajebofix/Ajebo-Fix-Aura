from __future__ import annotations

from datetime import datetime

import pytest
from flask_login import login_user
from werkzeug.exceptions import Forbidden

from admin.progression_routes import concern_progression as advisor_progression_view
from extensions import db
from models import Car, CarDriver, CarFault, CarOwnership, User
from services.concern_progression import (
    ConcernProgressionAccessError,
    ConcernProgressionNotFound,
    get_client_safe_reported_concern_progression,
    get_reported_concern_progression,
)
from services.event_emission import emit_vehicle_event


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
        email_verified_at=datetime(2026, 8, 13, 7, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _create_owned_car(owner: User, *, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2024,
        vin=f"W1NPROG00000{suffix:05d}",
        current_mileage=23000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"PG-{suffix:03d}-LA",
            mileage_at_transfer=23000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _create_concern(
    *,
    car: Car,
    reporter: User,
    suffix: int,
    status: str = "reported",
) -> CarFault:
    concern = CarFault(
        car_id=car.id,
        title=f"Reported observation {suffix}",
        category="observation",
        description="A calm client observation used only as domain test data.",
        status=status,
        reported_by=reporter.id,
        source="client",
        reported_at=datetime(2026, 8, 13, 8, suffix % 60, 0),
    )
    db.session.add(concern)
    db.session.commit()
    return concern


def _emit_transition(
    *,
    car: Car,
    concern: CarFault,
    actor: User,
    event_type: str,
    previous_state: str,
    new_state: str,
    direction: str,
    occurred_at: datetime,
    key: str,
    visibility: str = "client",
    evidence_refs: list[dict] | None = None,
):
    return emit_vehicle_event(
        car_id=car.id,
        event_type=event_type,
        subject_type="reported_concern",
        subject_id=concern.id,
        actor_type="user",
        actor_user_id=actor.id,
        visibility=visibility,
        source="tests.concern_progression",
        occurred_at=occurred_at,
        title=f"Progression event {event_type}",
        progression_direction=direction,
        idempotency_key=key,
        previous_state=previous_state,
        new_state=new_state,
        evidence_refs=evidence_refs or [
            {"type": "reported_concern", "id": concern.id}
        ],
    )


def test_report_only_abstains_with_evidence(app):
    with app.app_context():
        owner = _create_user(
            name="Report Owner",
            email="progression-owner-1@example.com",
            phone="08003000001",
        )
        car = _create_owned_car(owner, suffix=1)
        concern = _create_concern(car=car, reporter=owner, suffix=1)

        summary = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )

        assert summary.current_state == "reported"
        assert summary.timeline_state == "reported"
        assert summary.progression == "insufficient_evidence"
        assert summary.recurrence is None
        assert len(summary.timeline) == 1
        assert summary.timeline[0].event_type == "concern.reported"
        assert summary.evidence_event_ids == (summary.timeline[0].event_id,)
        assert "diagnosis" in summary.safety_note.lower()


def test_monitoring_and_resolved_rules_are_explicit(app):
    with app.app_context():
        owner = _create_user(
            name="State Owner",
            email="progression-owner-2@example.com",
            phone="08003000002",
        )
        advisor = _create_user(
            name="State Advisor",
            email="progression-advisor-2@example.com",
            phone="08003000003",
            role="admin",
        )
        car = _create_owned_car(owner, suffix=2)
        concern = _create_concern(car=car, reporter=owner, suffix=2)

        with app.test_request_context("/admin/progression", method="POST"):
            login_user(advisor)
            concern.status = "monitoring"
            db.session.commit()

        stable = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=advisor.id,
        )
        assert stable.progression == "stable"
        assert stable.current_state == "monitoring"
        assert stable.evidence_event_ids == (stable.timeline[-1].event_id,)

        with app.test_request_context("/admin/progression", method="POST"):
            login_user(advisor)
            concern.status = "resolved"
            concern.resolved_by = advisor.id
            concern.resolved_at = datetime(2026, 8, 13, 10, 0, 0)
            db.session.commit()

        resolved = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=advisor.id,
        )
        assert resolved.progression == "resolved"
        assert resolved.recurrence is False
        assert resolved.current_state == "resolved"
        assert resolved.timeline[-1].event_type == "concern.resolved"


def test_reopen_abstains_without_deterministic_recurrence_link(app):
    with app.app_context():
        owner = _create_user(
            name="Reopen Owner",
            email="progression-owner-3@example.com",
            phone="08003000004",
        )
        advisor = _create_user(
            name="Reopen Advisor",
            email="progression-advisor-3@example.com",
            phone="08003000005",
            role="admin",
        )
        car = _create_owned_car(owner, suffix=3)
        concern = _create_concern(car=car, reporter=owner, suffix=3)

        with app.test_request_context("/admin/progression", method="POST"):
            login_user(advisor)
            concern.status = "resolved"
            concern.resolved_by = advisor.id
            concern.resolved_at = datetime(2026, 8, 13, 10, 30, 0)
            db.session.commit()

            concern.status = "reported"
            db.session.commit()

        summary = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=advisor.id,
        )
        assert summary.timeline[-1].event_type == "concern.reopened"
        assert summary.progression == "insufficient_evidence"
        assert summary.recurrence is None
        assert "does not establish recurrence" in summary.explanation


def test_explicit_recurrence_requires_link_to_prior_resolved_event(app):
    with app.app_context():
        owner = _create_user(
            name="Recurring Owner",
            email="progression-owner-4@example.com",
            phone="08003000006",
        )
        car = _create_owned_car(owner, suffix=4)
        concern = _create_concern(car=car, reporter=owner, suffix=4)

        resolved = _emit_transition(
            car=car,
            concern=concern,
            actor=owner,
            event_type="concern.resolved",
            previous_state="reported",
            new_state="resolved",
            direction="resolved",
            occurred_at=datetime(2026, 8, 13, 11, 0, 0),
            key="progression-4-resolved",
        )
        db.session.flush()
        reopened = _emit_transition(
            car=car,
            concern=concern,
            actor=owner,
            event_type="concern.reopened",
            previous_state="resolved",
            new_state="reported",
            direction="recurring",
            occurred_at=datetime(2026, 8, 13, 12, 0, 0),
            key="progression-4-reopened",
            evidence_refs=[
                {"type": "reported_concern", "id": concern.id},
                {"type": "vehicle_event", "id": resolved.id},
            ],
        )
        assert reopened.id is not None
        db.session.commit()

        summary = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )
        assert summary.progression == "recurring"
        assert summary.recurrence is True
        assert resolved.id in summary.evidence_event_ids
        assert reopened.id in summary.evidence_event_ids


def test_hidden_correction_does_not_leak_into_client_timeline(app):
    with app.app_context():
        owner = _create_user(
            name="Correction Owner",
            email="progression-owner-5@example.com",
            phone="08003000007",
        )
        advisor = _create_user(
            name="Correction Advisor",
            email="progression-advisor-5@example.com",
            phone="08003000008",
            role="admin",
        )
        car = _create_owned_car(owner, suffix=5)
        concern = _create_concern(car=car, reporter=owner, suffix=5)

        owner_initial = get_client_safe_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )
        original_id = owner_initial.timeline[0].event_id

        correction = emit_vehicle_event(
            car_id=car.id,
            event_type="concern.corrected",
            subject_type="reported_concern",
            subject_id=concern.id,
            actor_type="user",
            actor_user_id=advisor.id,
            visibility="internal",
            source="tests.concern_progression",
            occurred_at=datetime(2026, 8, 13, 13, 0, 0),
            title="Canonical concern event corrected",
            progression_direction="not_applicable",
            idempotency_key="progression-5-correction",
            correction_of_event_id=original_id,
            evidence_refs=[{"type": "vehicle_event", "id": original_id}],
        )
        db.session.commit()

        owner_summary = get_client_safe_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )
        advisor_summary = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=advisor.id,
        )

        assert len(owner_summary.timeline) == 1
        assert owner_summary.timeline[0].corrected_by_event_id is None
        assert correction.id not in [item.event_id for item in owner_summary.timeline]

        assert len(advisor_summary.timeline) == 2
        assert advisor_summary.timeline[0].corrected_by_event_id == correction.id
        assert advisor_summary.timeline[1].event_type == "concern.corrected"
        assert advisor_summary.progression == "insufficient_evidence"


def test_owner_driver_and_advisor_visibility_boundaries(app):
    with app.app_context():
        owner = _create_user(
            name="Visibility Owner",
            email="progression-owner-6@example.com",
            phone="08003000009",
        )
        driver = _create_user(
            name="Visibility Driver",
            email="progression-driver-6@example.com",
            phone="08003000010",
            role="driver",
        )
        advisor = _create_user(
            name="Visibility Advisor",
            email="progression-advisor-6@example.com",
            phone="08003000011",
            role="admin",
        )
        car = _create_owned_car(owner, suffix=6)
        db.session.add(CarDriver(car_id=car.id, user_id=driver.id, is_active=True))
        db.session.commit()
        concern = _create_concern(car=car, reporter=owner, suffix=6)

        base = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=advisor.id,
        ).timeline[0]
        emit_vehicle_event(
            car_id=car.id,
            event_type="concern.corrected",
            subject_type="reported_concern",
            subject_id=concern.id,
            actor_type="user",
            actor_user_id=advisor.id,
            visibility="advisor",
            source="tests.concern_progression",
            occurred_at=datetime(2026, 8, 13, 14, 0, 0),
            title="Advisor-only correction evidence",
            progression_direction="not_applicable",
            idempotency_key="progression-6-advisor-correction",
            correction_of_event_id=base.event_id,
            evidence_refs=[{"type": "vehicle_event", "id": base.event_id}],
        )
        db.session.commit()

        owner_summary = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )
        driver_summary = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=driver.id,
        )
        advisor_summary = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=advisor.id,
        )

        assert owner_summary.viewer_authority == "owner"
        assert driver_summary.viewer_authority == "driver"
        assert advisor_summary.viewer_authority == "advisor"
        assert len(owner_summary.timeline) == 1
        assert len(driver_summary.timeline) == 1
        assert len(advisor_summary.timeline) == 2
        assert all(item.visibility == "client" for item in owner_summary.timeline)
        assert all(item.visibility == "client" for item in driver_summary.timeline)

        with pytest.raises(ConcernProgressionAccessError):
            get_client_safe_reported_concern_progression(
                car_id=car.id,
                concern_id=concern.id,
                viewer_user_id=advisor.id,
            )


def test_cross_vehicle_and_unrelated_user_are_isolated(app):
    with app.app_context():
        owner_one = _create_user(
            name="Isolation Owner One",
            email="progression-owner-7a@example.com",
            phone="08003000012",
        )
        owner_two = _create_user(
            name="Isolation Owner Two",
            email="progression-owner-7b@example.com",
            phone="08003000013",
        )
        outsider = _create_user(
            name="Isolation Outsider",
            email="progression-outsider-7@example.com",
            phone="08003000014",
        )
        car_one = _create_owned_car(owner_one, suffix=7)
        car_two = _create_owned_car(owner_two, suffix=8)
        concern_two = _create_concern(car=car_two, reporter=owner_two, suffix=7)

        with pytest.raises(ConcernProgressionNotFound):
            get_reported_concern_progression(
                car_id=car_one.id,
                concern_id=concern_two.id,
                viewer_user_id=owner_one.id,
            )

        with pytest.raises(ConcernProgressionAccessError):
            get_reported_concern_progression(
                car_id=car_two.id,
                concern_id=concern_two.id,
                viewer_user_id=outsider.id,
            )


def test_timeline_order_is_deterministic_when_timestamps_match(app):
    with app.app_context():
        owner = _create_user(
            name="Ordering Owner",
            email="progression-owner-8@example.com",
            phone="08003000015",
        )
        car = _create_owned_car(owner, suffix=9)
        concern = _create_concern(car=car, reporter=owner, suffix=8)
        same_time = datetime(2026, 8, 13, 15, 0, 0)

        review = _emit_transition(
            car=car,
            concern=concern,
            actor=owner,
            event_type="concern.review_started",
            previous_state="reported",
            new_state="under_review",
            direction="insufficient_evidence",
            occurred_at=same_time,
            key="progression-8-review",
        )
        monitoring = _emit_transition(
            car=car,
            concern=concern,
            actor=owner,
            event_type="concern.monitoring_started",
            previous_state="under_review",
            new_state="monitoring",
            direction="stable",
            occurred_at=same_time,
            key="progression-8-monitoring",
        )
        same_recorded = datetime(2026, 8, 13, 15, 1, 0)
        review.recorded_at = same_recorded
        monitoring.recorded_at = same_recorded
        db.session.commit()

        summary = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )
        event_ids = [item.event_id for item in summary.timeline]
        assert event_ids == sorted(event_ids)
        assert [item.event_type for item in summary.timeline[-2:]] == [
            "concern.review_started",
            "concern.monitoring_started",
        ]


def test_advisor_progression_api_returns_safe_evidence_surface(app):
    with app.app_context():
        owner = _create_user(
            name="API Owner",
            email="progression-owner-9@example.com",
            phone="08003000016",
        )
        advisor = _create_user(
            name="API Advisor",
            email="progression-advisor-9@example.com",
            phone="08003000017",
            role="admin",
        )
        car = _create_owned_car(owner, suffix=10)
        concern = _create_concern(car=car, reporter=owner, suffix=9)

        with app.test_request_context(
            f"/admin/concerns/{concern.id}/progression",
            method="GET",
        ):
            login_user(advisor)
            response, status = advisor_progression_view(concern.id)

        payload = response.get_json()
        assert status == 200
        assert payload["concern_id"] == concern.id
        assert payload["viewer_authority"] == "advisor"
        assert payload["progression"] == "insufficient_evidence"
        assert payload["evidence_event_ids"]
        assert payload["timeline"]
        assert "description" not in payload["timeline"][0]
        assert "data" not in payload["timeline"][0]

        with app.test_request_context(
            f"/admin/concerns/{concern.id}/progression",
            method="GET",
        ):
            login_user(owner)
            with pytest.raises(Forbidden):
                advisor_progression_view(concern.id)
