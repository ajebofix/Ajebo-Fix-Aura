"""PostgreSQL verification for concern timeline/progression reconstruction."""

from __future__ import annotations

from datetime import datetime, timezone

from flask_login import login_user

from app import create_app
from extensions import db
from models import Car, CarFault, CarOwnership, User
from services.concern_progression import (
    get_client_safe_reported_concern_progression,
    get_reported_concern_progression,
)
from services.event_emission import emit_vehicle_event


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
    db.session.flush()
    return user


def verify_progression_and_visibility() -> None:
    with app.app_context():
        owner = _create_user(
            name="Postgres Progression Owner",
            email="progression-postgres-owner@example.com",
            phone="+2348000000301",
        )
        advisor = _create_user(
            name="Postgres Progression Advisor",
            email="progression-postgres-advisor@example.com",
            phone="+2348000000302",
            role="admin",
        )
        car = Car(
            brand="Mercedes-Benz",
            model="GLE 450 4MATIC",
            year=2024,
            vin="W1NPROGCI00000301",
            current_mileage=19000,
        )
        db.session.add(car)
        db.session.flush()
        db.session.add(
            CarOwnership(
                user_id=owner.id,
                car_id=car.id,
                plate_number="PG-301-LA",
                mileage_at_transfer=19000,
                is_active=True,
            )
        )
        db.session.commit()

        concern = CarFault(
            car_id=car.id,
            title="Electrical observation",
            category="electrical_electronics",
            description="A client observation used only as domain evidence.",
            status="reported",
            reported_by=owner.id,
            source="client",
            reported_at=datetime(2026, 8, 13, 8, 0, 0),
        )
        db.session.add(concern)
        db.session.commit()

        initial = get_client_safe_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )
        if initial.progression != "insufficient_evidence":
            raise SystemExit(
                f"Unexpected initial progression: {initial.progression}"
            )

        with app.test_request_context("/admin/progression", method="POST"):
            login_user(advisor)
            concern.status = "monitoring"
            db.session.commit()

            concern.status = "resolved"
            concern.resolved_by = advisor.id
            concern.resolved_at = datetime(2026, 8, 13, 10, 0, 0)
            db.session.commit()

        resolved = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=advisor.id,
        )
        if resolved.progression != "resolved":
            raise SystemExit(
                f"Expected resolved progression, got {resolved.progression}"
            )
        if [item.event_type for item in resolved.timeline] != [
            "concern.reported",
            "concern.monitoring_started",
            "concern.resolved",
        ]:
            raise SystemExit("PostgreSQL timeline reconstruction is not ordered")

        resolved_event = resolved.timeline[-1]
        correction = emit_vehicle_event(
            car_id=car.id,
            event_type="concern.corrected",
            subject_type="reported_concern",
            subject_id=concern.id,
            actor_type="user",
            actor_user_id=advisor.id,
            visibility="internal",
            source="postgres_ci.concern_progression",
            occurred_at=datetime(2026, 8, 13, 11, 0, 0),
            title="Internal canonical correction",
            progression_direction="not_applicable",
            idempotency_key="postgres-progression-correction-301",
            correction_of_event_id=resolved_event.event_id,
            evidence_refs=[
                {"type": "vehicle_event", "id": resolved_event.event_id}
            ],
        )
        db.session.commit()

        advisor_after_correction = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=advisor.id,
        )
        if advisor_after_correction.progression != "insufficient_evidence":
            raise SystemExit(
                "Advisor progression must abstain when decisive evidence is corrected"
            )
        if correction.id not in [
            item.event_id for item in advisor_after_correction.timeline
        ]:
            raise SystemExit("Advisor timeline did not include internal correction")

        owner_after_correction = get_client_safe_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )
        if correction.id in [item.event_id for item in owner_after_correction.timeline]:
            raise SystemExit("Internal correction leaked into client-safe timeline")
        if any(item.visibility != "client" for item in owner_after_correction.timeline):
            raise SystemExit("Client-safe timeline included non-client visibility")

        print("PostgreSQL concern progression and visibility verified.")


def verify_deterministic_recurrence() -> None:
    with app.app_context():
        owner = User.query.filter_by(
            email="progression-postgres-owner@example.com"
        ).one()
        car = Car(
            brand="Mercedes-Benz",
            model="E 450 4MATIC",
            year=2024,
            vin="W1NPROGCI00000302",
            current_mileage=12000,
        )
        db.session.add(car)
        db.session.flush()
        db.session.add(
            CarOwnership(
                user_id=owner.id,
                car_id=car.id,
                plate_number="PG-302-LA",
                mileage_at_transfer=12000,
                is_active=True,
            )
        )
        db.session.commit()

        concern = CarFault(
            car_id=car.id,
            title="Recurring observation evidence fixture",
            category="observation",
            description="Domain description is intentionally excluded from summaries.",
            status="reported",
            reported_by=owner.id,
            source="client",
        )
        db.session.add(concern)
        db.session.commit()

        resolved = emit_vehicle_event(
            car_id=car.id,
            event_type="concern.resolved",
            subject_type="reported_concern",
            subject_id=concern.id,
            actor_type="user",
            actor_user_id=owner.id,
            visibility="client",
            source="postgres_ci.concern_progression",
            occurred_at=datetime(2026, 8, 13, 12, 0, 0),
            title="Concern resolved",
            progression_direction="resolved",
            idempotency_key="postgres-recurrence-resolved-302",
            previous_state="reported",
            new_state="resolved",
            evidence_refs=[{"type": "reported_concern", "id": concern.id}],
        )
        db.session.flush()
        reopened = emit_vehicle_event(
            car_id=car.id,
            event_type="concern.reopened",
            subject_type="reported_concern",
            subject_id=concern.id,
            actor_type="user",
            actor_user_id=owner.id,
            visibility="client",
            source="postgres_ci.concern_progression",
            occurred_at=datetime(2026, 8, 13, 13, 0, 0),
            title="Concern reopened with recurrence evidence",
            progression_direction="recurring",
            idempotency_key="postgres-recurrence-reopened-302",
            previous_state="resolved",
            new_state="reported",
            evidence_refs=[
                {"type": "reported_concern", "id": concern.id},
                {"type": "vehicle_event", "id": resolved.id},
            ],
        )
        db.session.commit()

        summary = get_client_safe_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )
        if summary.progression != "recurring" or summary.recurrence is not True:
            raise SystemExit("Deterministic recurrence linkage was not reconstructed")
        if resolved.id not in summary.evidence_event_ids:
            raise SystemExit("Recurrence summary omitted prior resolution evidence")
        if reopened.id not in summary.evidence_event_ids:
            raise SystemExit("Recurrence summary omitted reopening evidence")

        print("PostgreSQL deterministic recurrence verified.")


if __name__ == "__main__":
    verify_progression_and_visibility()
    verify_deterministic_recurrence()
