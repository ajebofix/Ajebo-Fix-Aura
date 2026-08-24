"""Verify Wave 2.2B Vehicle Assessment lifecycle on PostgreSQL."""

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
    TreatmentPlan,
    User,
    VehicleAssessment,
    VehicleEvent,
)
from services.assessment_lifecycle import AssessmentLifecycleService  # noqa: E402


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
    now = datetime(2026, 8, 24, 16, 0, 0)
    return VehicleEvent(
        car_id=car_id,
        ownership_id=ownership_id,
        event_type=event_type,
        severity="low",
        event_date=now.date(),
        title="PostgreSQL assessment event contract probe",
        description=None,
        mileage=18000,
        source="verify.assessment_events",
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
    raise SystemExit(f"PostgreSQL accepted invalid assessment event: {label}")


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
            "ck_vehicle_events_assessment_contract",
        }
        missing = required_checks - checks
        if missing:
            raise SystemExit(
                "Wave 2.2B VehicleEvent PostgreSQL CHECK constraints are missing: "
                f"{sorted(missing)}"
            )

        now = datetime(2026, 8, 24, 15, 0, 0)
        owner = User(
            name="Assessment PostgreSQL Owner",
            email="assessment-events-postgres-owner@example.com",
            phone_number="+2348000000285",
            role="user",
            is_active=True,
            email_verified_at=now,
        )
        owner.set_password("Password123")
        advisor = User(
            name="Assessment PostgreSQL Advisor",
            email="assessment-events-postgres-advisor@example.com",
            phone_number="+2348000000286",
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
            vin="W1NASSESSMENTPG0285",
            engine_number="M256-PG",
            engine_type="M256",
            transmission_type="9G-TRONIC",
            current_mileage=18000,
        )
        db.session.add(car)
        db.session.flush()
        ownership = CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number="AE-285-LA",
            mileage_at_transfer=17500,
            is_active=True,
        )
        db.session.add(ownership)
        db.session.flush()
        consultation = Consultation(
            car_id=car.id,
            ownership_id=ownership.id,
            advisor_id=advisor.id,
            client_id=owner.id,
            status="in_progress",
            scheduled_for=datetime(2026, 8, 24, 15, 10, 0),
            started_at=datetime(2026, 8, 24, 15, 11, 0),
        )
        db.session.add(consultation)
        db.session.commit()

        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 24, 15, 15, 0),
            source="verify.assessment_start",
        )
        db.session.commit()

        resumed = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 24, 15, 16, 0),
            source="verify.assessment_resume",
        )
        db.session.commit()
        if resumed.id != assessment.id:
            raise SystemExit("Assessment resume created a second assessment")

        AssessmentLifecycleService.save_draft(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            scalar_updates={
                "engine_status": "attention",
                "transmission_status": "stable",
                "suspension_status": "stable",
                "electrical_status": "monitoring",
                "cooling_status": "stable",
                "professional_recommendation": "Internal PostgreSQL verifier detail.",
            },
            risks=[
                {
                    "description": "Verifier risk",
                    "likely_cause": "Advisor-only verifier context",
                    "consequence_if_ignored": "Verifier consequence",
                    "urgency": "monitoring",
                }
            ],
            treatment_options=[
                {
                    "option_code": "A",
                    "title": "Verifier option",
                    "description": "Verifier treatment option detail",
                }
            ],
        )
        db.session.commit()

        AssessmentLifecycleService.finalize(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            finalized_at=datetime(2026, 8, 24, 15, 30, 0),
            source="verify.assessment_finalize",
        )
        db.session.commit()

        persisted = db.session.get(VehicleAssessment, assessment.id)
        if persisted is None or persisted.status != "finalized" or not persisted.is_finalized:
            raise SystemExit("Assessment lifecycle did not persist finalized state")

        events = (
            VehicleEvent.query.filter_by(
                subject_type="vehicle_assessment",
                subject_id=assessment.id,
            )
            .order_by(VehicleEvent.occurred_at.asc(), VehicleEvent.id.asc())
            .all()
        )
        if [event.event_type for event in events] != [
            "assessment.created",
            "assessment.finalized",
        ]:
            raise SystemExit(
                "Unexpected assessment canonical event chain: "
                f"{[event.event_type for event in events]!r}"
            )

        created, finalized = events
        if created.previous_state is not None or created.new_state != "draft":
            raise SystemExit("assessment.created persisted unsafe state semantics")
        if created.visibility != "advisor" or created.actor_authority != "advisor":
            raise SystemExit("assessment.created persisted unsafe authority/visibility")
        if finalized.previous_state != "draft" or finalized.new_state != "finalized":
            raise SystemExit("assessment.finalized persisted unsafe state semantics")
        if finalized.visibility != "client" or finalized.actor_authority != "advisor":
            raise SystemExit("assessment.finalized persisted unsafe authority/visibility")
        if any(event.progression_direction != "not_applicable" for event in events):
            raise SystemExit("Assessment workflow event changed mechanical progression")
        if "Internal PostgreSQL verifier detail" in str(finalized.data) or (
            "Internal PostgreSQL verifier detail" in (finalized.description or "")
        ):
            raise SystemExit("Internal professional assessment content leaked into event")
        if TreatmentPlan.query.filter_by(assessment_id=assessment.id).count() != 1:
            raise SystemExit("Assessment finalization did not create exactly one compatibility plan")

        _expect_constraint_failure(
            _event_row(
                car_id=car.id,
                ownership_id=ownership.id,
                actor_user_id=advisor.id,
                event_type="assessment.finalized",
                subject_type="consultation",
                subject_id=assessment.id,
                progression_direction="not_applicable",
                previous_state="draft",
                new_state="finalized",
            ),
            "assessment event paired with consultation subject",
        )

        _expect_constraint_failure(
            _event_row(
                car_id=car.id,
                ownership_id=ownership.id,
                actor_user_id=advisor.id,
                event_type="assessment.finalized",
                subject_type="vehicle_assessment",
                subject_id=assessment.id,
                progression_direction="not_applicable",
                previous_state=None,
                new_state="finalized",
            ),
            "unsafe assessment state transition",
        )

        _expect_constraint_failure(
            _event_row(
                car_id=car.id,
                ownership_id=ownership.id,
                actor_user_id=advisor.id,
                event_type="assessment.created",
                subject_type="vehicle_assessment",
                subject_id=assessment.id,
                progression_direction="stable",
                previous_state=None,
                new_state="draft",
            ),
            "assessment workflow event with mechanical progression",
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
            "Wave 2.2B assessment lifecycle verified on PostgreSQL: "
            "draft creation -> safe persistence -> finalization, with canonical "
            "subject/state constraints and compatibility TreatmentPlan rollback boundary."
        )


if __name__ == "__main__":
    main()
