"""Verify Wave 2.2B3 immutable assessment corrections on PostgreSQL."""

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
    VehicleEvent,
)
from models_assessment_addendum import VehicleAssessmentAddendum  # noqa: E402
from services.assessment_lifecycle import AssessmentLifecycleService  # noqa: E402


def _invalid_correction_event(*, car, ownership, advisor, assessment) -> VehicleEvent:
    now = datetime(2026, 8, 24, 20, 0, 0)
    return VehicleEvent(
        car_id=car.id,
        ownership_id=ownership.id,
        event_type="assessment.corrected",
        severity="low",
        event_date=now.date(),
        title="Invalid correction probe",
        description=None,
        mileage=assessment.mileage_at_assessment,
        source="verify.assessment_correction",
        data={"addendum_id": 999, "category": "correction", "visibility": "client"},
        fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        schema_version=1,
        occurred_at=now,
        recorded_at=now,
        subject_type="vehicle_assessment",
        subject_id=assessment.id,
        actor_type="user",
        actor_user_id=advisor.id,
        actor_authority="advisor",
        visibility="client",
        previous_state="draft",
        new_state="finalized",
        progression_direction="not_applicable",
        evidence_refs=[],
        correction_of_event_id=None,
        created_by=advisor.id,
        is_deleted=False,
    )


def main() -> None:
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            raise SystemExit("This verifier must run against PostgreSQL")

        table_names = set(inspect(db.engine).get_table_names())
        if "vehicle_assessment_addenda" not in table_names:
            raise SystemExit("Vehicle Assessment addenda table is missing")

        checks = {
            item["name"]
            for item in inspect(db.engine).get_check_constraints("vehicle_events")
        }
        if "ck_vehicle_events_assessment_contract" not in checks:
            raise SystemExit("Assessment VehicleEvent PostgreSQL contract is missing")

        now = datetime(2026, 8, 24, 19, 0, 0)
        owner = User(
            name="B3 PostgreSQL Owner",
            email="b3-postgres-owner@example.com",
            phone_number="+2348000000391",
            role="user",
            is_active=True,
            email_verified_at=now,
        )
        owner.set_password("Password123")
        advisor = User(
            name="B3 PostgreSQL Advisor",
            email="b3-postgres-advisor@example.com",
            phone_number="+2348000000392",
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
            vin="W1NASSESSMENTB3PG0391",
            engine_number="M256-B3-PG",
            engine_type="M256",
            transmission_type="9G-TRONIC",
            current_mileage=28000,
        )
        db.session.add(car)
        db.session.flush()
        ownership = CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number="B3-391-LA",
            mileage_at_transfer=27000,
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
            scheduled_for=datetime(2026, 8, 24, 19, 5, 0),
            started_at=datetime(2026, 8, 24, 19, 6, 0),
        )
        db.session.add(consultation)
        db.session.commit()

        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 24, 19, 10, 0),
        )
        AssessmentLifecycleService.save_draft(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            scalar_updates={
                "engine_status": "healthy",
                "transmission_status": "healthy",
                "suspension_status": "healthy",
                "electrical_status": "healthy",
                "cooling_status": "healthy",
                "professional_recommendation": "Original immutable recommendation.",
            },
        )
        AssessmentLifecycleService.finalize(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            finalized_at=datetime(2026, 8, 24, 19, 20, 0),
        )
        db.session.commit()

        original_recommendation = assessment.professional_recommendation
        finalized_event = VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.finalized",
        ).one()

        addendum = AssessmentLifecycleService.add_correction(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            category="clarification",
            reason="PostgreSQL B3 contract verification",
            visibility="client",
            client_text="A dated clarification has been added without rewriting the report.",
            internal_text="Restricted verifier context.",
            idempotency_key="b3-postgres-correction-1",
            occurred_at=datetime(2026, 8, 24, 19, 30, 0),
        )
        db.session.commit()

        correction_event = VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.corrected",
        ).one()

        if assessment.professional_recommendation != original_recommendation:
            raise SystemExit("B3 correction rewrote the finalized assessment")
        if correction_event.previous_state != "finalized" or correction_event.new_state != "finalized":
            raise SystemExit("assessment.corrected persisted unsafe state semantics")
        if correction_event.progression_direction != "not_applicable":
            raise SystemExit("assessment.corrected changed mechanical progression")
        if correction_event.correction_of_event_id != finalized_event.id:
            raise SystemExit("assessment.corrected did not reference finalization fact")
        if addendum.client_text in str(correction_event.data) or addendum.internal_text in str(correction_event.data):
            raise SystemExit("Assessment correction free text leaked into canonical event data")
        if addendum.client_text in (correction_event.description or "") or addendum.internal_text in (correction_event.description or ""):
            raise SystemExit("Assessment correction free text leaked into event description")

        db.session.add(
            _invalid_correction_event(
                car=car,
                ownership=ownership,
                advisor=advisor,
                assessment=assessment,
            )
        )
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        else:
            raise SystemExit("PostgreSQL accepted unsafe assessment.corrected state semantics")

        replay = AssessmentLifecycleService.add_correction(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            category="clarification",
            reason="PostgreSQL B3 contract verification",
            visibility="client",
            client_text="A dated clarification has been added without rewriting the report.",
            internal_text="Restricted verifier context.",
            idempotency_key="b3-postgres-correction-1",
        )
        db.session.commit()
        if replay.id != addendum.id:
            raise SystemExit("Correction idempotency replay created a second addendum")
        if VehicleAssessmentAddendum.query.filter_by(assessment_id=assessment.id).count() != 1:
            raise SystemExit("Correction idempotency created duplicate addenda")
        if VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.corrected",
        ).count() != 1:
            raise SystemExit("Correction idempotency created duplicate canonical events")

        print(
            "Wave 2.2B3 assessment corrections verified on PostgreSQL: immutable "
            "finalized record + additive attributed addendum + canonical correction event."
        )


if __name__ == "__main__":
    main()
