"""PostgreSQL verification for Reported Concern canonical progression.

The first domain migration must prove that concern state and event history are
one transaction on Aura's production dialect. This script verifies creation,
advisor transitions, and rollback when canonical emission cannot be satisfied.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask_login import login_user

from app import create_app
from extensions import db
from models import Car, CarFault, CarOwnership, User, VehicleEvent
from services.reported_concern_session_events import ReportedConcernIntegrationError


app = create_app()


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _create_user(*, name: str, email: str, phone: str, role: str = "user") -> User:
    user = User(
        name=name,
        email=email,
        phone_number=phone,
        role=role,
        is_active=True,
        email_verified_at=_utcnow_naive(),
    )
    user.set_password("CI-only-password")
    db.session.add(user)
    db.session.commit()
    return user


def _create_car(*, vin: str) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLS 450 4MATIC",
        year=2024,
        vin=vin,
        current_mileage=17000,
    )
    db.session.add(car)
    db.session.commit()
    return car


def _events(concern_id: int) -> list[VehicleEvent]:
    return (
        VehicleEvent.query.filter_by(
            subject_type="reported_concern",
            subject_id=concern_id,
        )
        .order_by(VehicleEvent.id.asc())
        .all()
    )


def verify_progression() -> None:
    with app.app_context():
        owner = _create_user(
            name="Postgres Concern Owner",
            email="concern-postgres-owner@example.com",
            phone="+2348000000201",
        )
        advisor = _create_user(
            name="Postgres Aura Advisor",
            email="concern-postgres-advisor@example.com",
            phone="+2348000000202",
            role="admin",
        )
        car = _create_car(vin="W1NCI000000000201")
        db.session.add(
            CarOwnership(
                user_id=owner.id,
                car_id=car.id,
                plate_number="RC-201-LA",
                mileage_at_transfer=17000,
                is_active=True,
            )
        )
        db.session.commit()

        concern = CarFault(
            car_id=car.id,
            title="Electrical observation",
            category="electrical_electronics",
            description="A dashboard display interruption was reported.",
            status="reported",
            reported_by=owner.id,
            source="client",
            reported_at=datetime(2026, 8, 13, 9, 0, 0),
        )
        db.session.add(concern)
        db.session.commit()

        with app.test_request_context(
            f"/admin/concerns/{concern.id}/review",
            method="POST",
        ):
            login_user(advisor)

            concern.status = "under_review"
            concern.reviewed_at = datetime(2026, 8, 13, 9, 30, 0)
            concern.reviewed_by = advisor.id
            db.session.commit()

            concern.status = "monitoring"
            db.session.commit()

            concern.status = "resolved"
            concern.resolved_at = datetime(2026, 8, 13, 10, 30, 0)
            concern.resolved_by = advisor.id
            db.session.commit()

        events = _events(concern.id)
        event_types = [event.event_type for event in events]
        expected = [
            "concern.reported",
            "concern.review_started",
            "concern.monitoring_started",
            "concern.resolved",
        ]
        if event_types != expected:
            raise SystemExit(
                f"Unexpected Reported Concern progression: {event_types!r}"
            )

        if events[0].actor_authority != "owner":
            raise SystemExit("Initial concern event did not preserve owner authority")

        if any(event.actor_authority != "advisor" for event in events[1:]):
            raise SystemExit("Advisor transitions did not preserve advisor authority")

        if events[-1].progression_direction != "resolved":
            raise SystemExit("Resolved concern event did not preserve resolution semantics")

        if any(event.description for event in events):
            raise SystemExit("Raw concern descriptions leaked into canonical events")

        print("PostgreSQL Reported Concern progression verified.")


def verify_atomic_failure() -> None:
    with app.app_context():
        owner = User.query.filter_by(
            email="concern-postgres-owner@example.com"
        ).one()
        orphan_car = _create_car(vin="W1NCI000000000202")

        concern = CarFault(
            car_id=orphan_car.id,
            title="Unassigned vehicle observation",
            category="observation",
            description="This concern must not survive without canonical history.",
            status="reported",
            reported_by=owner.id,
            source="client",
        )
        db.session.add(concern)

        try:
            db.session.commit()
        except ReportedConcernIntegrationError:
            db.session.rollback()
        except Exception as exc:
            # The canonical writer may surface its more specific envelope error;
            # any failure here is acceptable only if the domain row rolls back.
            db.session.rollback()
            if "active vehicle ownership" not in str(exc):
                raise
        else:
            raise SystemExit(
                "Concern without active ownership committed without canonical event"
            )

        if CarFault.query.filter_by(
            car_id=orphan_car.id,
            title="Unassigned vehicle observation",
        ).count():
            raise SystemExit("Reported Concern survived a canonical event failure")

        print("PostgreSQL Reported Concern atomic rollback verified.")


if __name__ == "__main__":
    verify_progression()
    verify_atomic_failure()
