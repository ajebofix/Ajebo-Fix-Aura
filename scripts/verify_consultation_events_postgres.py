"""Verify Wave 2.2A consultation canonical events on PostgreSQL."""

from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import sys
import uuid

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402
from extensions import db  # noqa: E402
from models import (  # noqa: E402
    Car,
    CarOwnership,
    Consultation,
    User,
    VehicleAssessment,
    VehicleEvent,
)
from services.consultation_lifecycle import ConsultationLifecycleService  # noqa: E402


def _event_row(
    *,
    car_id: int,
    ownership_id: int,
    actor_user_id: int,
    event_type: str,
    subject_type: str | None,
    subject_id: int | None,
    progression_direction: str | None,
    previous_state: str | None,
    new_state: str | None,
) -> VehicleEvent:
    now = datetime(2026, 8, 21, 3, 0, 0)
    return VehicleEvent(
        car_id=car_id,
        ownership_id=ownership_id,
        event_type=event_type,
        severity="low",
        event_date=now.date(),
        title="PostgreSQL consultation event contract probe",
        description=None,
        mileage=None,
        source="verify.consultation_events",
        data={},
        fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        schema_version=1,
        occurred_at=now,
        recorded_at=now,
        subject_type=subject_type,
        subject_id=subject_id,
        actor_type="user",
        actor_user_id=actor_user_id,
        actor_authority="advisor",
        visibility="client",
        previous_state=previous_state,
        new_state=new_state,
        progression_direction=progression_direction,
        evidence_refs=[],
        correction_of_event_id=None,
        created_by=actor_user_id,
        is_deleted=False,
    )


def _expect_constraint_failure(row: VehicleEvent, label: str) -> None:
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return
    raise SystemExit(f"PostgreSQL accepted invalid consultation event: {label}")


def main() -> None:
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            raise SystemExit("This verifier must run against PostgreSQL")

        checks = {
            item["name"]
            for item in inspect(db.engine).get_check_constraints("vehicle_events")
        }
        required_checks = {
            "ck_vehicle_events_canonical_subject_event",
            "ck_vehicle_events_evidence_contract",
            "ck_vehicle_events_consultation_contract",
        }
        if not required_checks.issubset(checks):
            raise SystemExit(
                "Wave 2.2A consultation VehicleEvent PostgreSQL CHECK constraints are missing"
            )

        now = datetime(2026, 8, 21, 2, 30, 0)
        owner = User(
            name="Consultation PostgreSQL Owner",
            email="consultation-events-postgres-owner@example.com",
            phone_number="+2348000000274",
            role="user",
            is_active=True,
            email_verified_at=now,
        )
        owner.set_password("Password123")
        advisor = User(
            name="Consultation PostgreSQL Advisor",
            email="consultation-events-postgres-advisor@example.com",
            phone_number="+2348000000275",
            role="admin",
            is_active=True,
            email_verified_at=now,
        )
        advisor.set_password("Password123")
        db.session.add_all([owner, advisor])
        db.session.flush()

        car = Car(
            brand="Mercedes-Benz",
            model="GLE 450",
            year=2024,
            vin="W1NCONSULTEVENT274",
            current_mileage=15000,
        )
        db.session.add(car)
        db.session.flush()
        ownership = CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number="CE-274-LA",
            mileage_at_transfer=15000,
            is_active=True,
        )
        db.session.add(ownership)
        db.session.commit()

        consultation = ConsultationLifecycleService.request(
            car_id=car.id,
            actor_user_id=owner.id,
            preferred_for=datetime(2026, 8, 22, 9, 0, 0),
            occurred_at=datetime(2026, 8, 21, 2, 31, 0),
            source="verify.consultation_request",
        )
        db.session.commit()

        ConsultationLifecycleService.schedule(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            scheduled_for=datetime(2026, 8, 22, 10, 0, 0),
            occurred_at=datetime(2026, 8, 21, 2, 32, 0),
            source="verify.consultation_schedule",
        )
        db.session.commit()

        ConsultationLifecycleService.start(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            started_at=datetime(2026, 8, 22, 10, 1, 0),
            source="verify.consultation_start",
        )
        db.session.commit()

        assessment = VehicleAssessment(
            consultation_id=consultation.id,
            car_id=car.id,
            advisor_id=advisor.id,
            finalized_by=advisor.id,
            status="finalized",
            is_finalized=True,
            finalized_at=datetime(2026, 8, 22, 10, 30, 0),
            vin=car.vin,
            mileage_at_assessment=car.current_mileage or 0,
        )
        db.session.add(assessment)
        db.session.commit()

        ConsultationLifecycleService.complete(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            summary="Internal verification summary.",
            client_visible_summary="Consultation review completed.",
            completed_at=datetime(2026, 8, 22, 11, 0, 0),
            source="verify.consultation_complete",
        )
        db.session.commit()

        persisted = db.session.get(Consultation, consultation.id)
        if persisted is None or persisted.status != "completed":
            raise SystemExit("Consultation lifecycle did not persist completed state")

        events = (
            VehicleEvent.query.filter_by(
                subject_type="consultation",
                subject_id=consultation.id,
            )
            .order_by(VehicleEvent.occurred_at.asc(), VehicleEvent.id.asc())
            .all()
        )
        expected_types = [
            "consultation.requested",
            "consultation.scheduled",
            "consultation.started",
            "consultation.completed",
        ]
        if [event.event_type for event in events] != expected_types:
            raise SystemExit(
                "Unexpected consultation canonical event chain: "
                f"{[event.event_type for event in events]!r}"
            )

        expected_transitions = [
            (None, "requested", "owner"),
            ("requested", "scheduled", "advisor"),
            ("scheduled", "in_progress", "advisor"),
            ("in_progress", "completed", "advisor"),
        ]
        for event, (previous_state, new_state, authority) in zip(
            events, expected_transitions, strict=True
        ):
            if event.previous_state != previous_state or event.new_state != new_state:
                raise SystemExit(
                    f"Unsafe state transition persisted for {event.event_type}"
                )
            if event.actor_authority != authority:
                raise SystemExit(
                    f"Unexpected actor authority for {event.event_type}: "
                    f"{event.actor_authority!r}"
                )
            if event.progression_direction != "not_applicable":
                raise SystemExit(
                    f"Consultation event changed mechanical progression: {event.event_type}"
                )

        completion = events[-1]
        if completion.data != {"assessment_id": assessment.id}:
            raise SystemExit("Completion event lost its finalized assessment reference")
        if "Internal verification summary" in str(completion.data) or (
            "Internal verification summary" in (completion.description or "")
        ):
            raise SystemExit("Internal advisor summary leaked into canonical event")

        _expect_constraint_failure(
            _event_row(
                car_id=car.id,
                ownership_id=ownership.id,
                actor_user_id=advisor.id,
                event_type="consultation.completed",
                subject_type="vehicle_evidence",
                subject_id=consultation.id,
                progression_direction="not_applicable",
                previous_state="in_progress",
                new_state="completed",
            ),
            "consultation event paired with vehicle_evidence subject",
        )

        _expect_constraint_failure(
            _event_row(
                car_id=car.id,
                ownership_id=ownership.id,
                actor_user_id=advisor.id,
                event_type="consultation.completed",
                subject_type="consultation",
                subject_id=consultation.id,
                progression_direction="stable",
                previous_state="scheduled",
                new_state="completed",
            ),
            "unsafe consultation progression/state contract",
        )

        legacy = _event_row(
            car_id=car.id,
            ownership_id=ownership.id,
            actor_user_id=advisor.id,
            event_type="legacy.service_record",
            subject_type=None,
            subject_id=None,
            progression_direction=None,
            previous_state=None,
            new_state=None,
        )
        db.session.add(legacy)
        db.session.commit()
        if legacy.id is None:
            raise SystemExit("Legacy subject-less VehicleEvent compatibility was broken")

        print(
            "Wave 2.2A consultation canonical events verified on PostgreSQL: "
            "request -> schedule -> start -> complete, with DB subject/state constraints enforced."
        )


if __name__ == "__main__":
    main()
