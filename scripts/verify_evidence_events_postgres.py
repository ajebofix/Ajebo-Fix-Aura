"""Verify Wave 1.4 evidence canonical events and PostgreSQL constraints."""

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
from evidence.models import EvidenceLink, VehicleEvidence  # noqa: E402
from evidence.review import (  # noqa: E402
    link_evidence_to_reported_concern,
    review_evidence,
)
from extensions import db  # noqa: E402
from models import (  # noqa: E402
    Car,
    CarFault,
    CarOwnership,
    User,
    VehicleEvent,
)


def _event_row(
    *,
    car_id: int,
    ownership_id: int,
    advisor_id: int,
    event_type: str,
    subject_type: str | None,
    subject_id: int | None,
    progression_direction: str | None,
    previous_state: str | None,
    new_state: str | None,
) -> VehicleEvent:
    now = datetime(2026, 8, 16, 22, 0, 0)
    return VehicleEvent(
        car_id=car_id,
        ownership_id=ownership_id,
        event_type=event_type,
        severity="low",
        event_date=now.date(),
        title="PostgreSQL event contract probe",
        description=None,
        mileage=None,
        source="verify.evidence_events",
        data={},
        fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        schema_version=1,
        occurred_at=now,
        recorded_at=now,
        subject_type=subject_type,
        subject_id=subject_id,
        actor_type="user",
        actor_user_id=advisor_id,
        actor_authority="advisor",
        visibility="client",
        previous_state=previous_state,
        new_state=new_state,
        progression_direction=progression_direction,
        evidence_refs=[],
        correction_of_event_id=None,
        created_by=advisor_id,
        is_deleted=False,
    )


def _expect_constraint_failure(row: VehicleEvent, label: str) -> None:
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return
    raise SystemExit(f"PostgreSQL accepted invalid canonical event: {label}")


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
        }
        if not required_checks.issubset(checks):
            raise SystemExit(
                "Wave 1.4 evidence event PostgreSQL CHECK constraints are missing"
            )

        now = datetime(2026, 8, 16, 21, 30, 0)
        owner = User(
            name="Evidence Event PostgreSQL Owner",
            email="evidence-events-postgres-owner@example.com",
            phone_number="+2348000000174",
            role="user",
            is_active=True,
            email_verified_at=now,
        )
        owner.set_password("Password123")
        advisor = User(
            name="Evidence Event PostgreSQL Advisor",
            email="evidence-events-postgres-advisor@example.com",
            phone_number="+2348000000175",
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
            vin="W1NEVIDENCEEVENT174",
            current_mileage=12500,
        )
        db.session.add(car)
        db.session.flush()
        ownership = CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number="EE-174-LA",
            mileage_at_transfer=12500,
            is_active=True,
        )
        db.session.add(ownership)
        db.session.flush()

        evidence_payload = b"postgres-evidence-event-object"
        evidence = VehicleEvidence(
            car_id=car.id,
            uploaded_by_user_id=owner.id,
            evidence_type="image",
            purpose="concern_support",
            source_channel="web",
            visibility="client",
            review_status="pending_review",
            storage_provider="ci-private",
            storage_state="available",
            object_key="evidence/ee/17400000000000000000000000000174.jpg",
            safe_display_name="vehicle-evidence-174.jpg",
            content_type="image/jpeg",
            byte_size=len(evidence_payload),
            sha256=hashlib.sha256(evidence_payload).hexdigest(),
            consent_basis="explicit_web_upload",
            lawful_purpose="vehicle_care",
            uploaded_at=now,
        )
        db.session.add(evidence)
        db.session.flush()

        concern = CarFault(
            car_id=car.id,
            title="PostgreSQL evidence event concern",
            category="observation",
            description="Controlled PostgreSQL evidence-event verification concern.",
            status="reported",
            reported_by=owner.id,
            source="client",
            reported_at=now,
        )
        db.session.add(concern)
        db.session.commit()

        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )
        review_event = VehicleEvent.query.filter_by(
            subject_type="vehicle_evidence",
            subject_id=evidence.id,
            event_type="evidence.reviewed",
        ).one_or_none()
        if review_event is None:
            raise SystemExit("Evidence review did not emit a canonical VehicleEvent")
        if (
            review_event.previous_state != "pending_review"
            or review_event.new_state != "accepted"
            or review_event.progression_direction != "not_applicable"
        ):
            raise SystemExit("Evidence review event has unsafe progression semantics")

        link_result = link_evidence_to_reported_concern(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            concern_id=concern.id,
        )
        link_event = VehicleEvent.query.filter_by(
            subject_type="vehicle_evidence",
            subject_id=evidence.id,
            event_type="evidence.linked",
        ).one_or_none()
        if link_event is None:
            raise SystemExit("Evidence linkage did not emit a canonical VehicleEvent")
        if link_event.progression_direction != "not_applicable":
            raise SystemExit("Evidence link event incorrectly changed progression")
        if EvidenceLink.query.filter_by(id=link_result.link_id).one_or_none() is None:
            raise SystemExit("EvidenceLink did not persist with its canonical event")

        _expect_constraint_failure(
            _event_row(
                car_id=car.id,
                ownership_id=ownership.id,
                advisor_id=advisor.id,
                event_type="evidence.reviewed",
                subject_type="reported_concern",
                subject_id=evidence.id,
                progression_direction="not_applicable",
                previous_state="pending_review",
                new_state="accepted",
            ),
            "evidence event paired with reported_concern subject",
        )

        _expect_constraint_failure(
            _event_row(
                car_id=car.id,
                ownership_id=ownership.id,
                advisor_id=advisor.id,
                event_type="evidence.reviewed",
                subject_type="vehicle_evidence",
                subject_id=evidence.id,
                progression_direction=None,
                previous_state=None,
                new_state="accepted",
            ),
            "incomplete evidence review state",
        )

        legacy = _event_row(
            car_id=car.id,
            ownership_id=ownership.id,
            advisor_id=advisor.id,
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
            raise SystemExit("Legacy subject-less event compatibility was broken")

        print(
            "Wave 1.4 evidence canonical events verified on PostgreSQL with "
            "subject/event and progression constraints enforced."
        )


if __name__ == "__main__":
    main()
