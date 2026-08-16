"""Verify the safe Wave 1.4 evidence timeline projection on PostgreSQL."""

from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402
from evidence.models import VehicleEvidence  # noqa: E402
from evidence.review import (  # noqa: E402
    link_evidence_to_reported_concern,
    review_evidence,
)
from extensions import db  # noqa: E402
from models import Car, CarDriver, CarFault, CarOwnership, User  # noqa: E402
from services.concern_progression import get_reported_concern_progression  # noqa: E402
from services.evidence_timeline import (  # noqa: E402
    get_advisor_evidence_timeline,
    get_client_safe_evidence_timeline,
)
from services.event_emission import emit_vehicle_event  # noqa: E402


def _evidence(
    *,
    car_id: int,
    uploader_id: int,
    suffix: int,
    visibility: str = "client",
) -> VehicleEvidence:
    payload = f"postgres-timeline-evidence-{suffix}".encode()
    row = VehicleEvidence(
        car_id=car_id,
        uploaded_by_user_id=uploader_id,
        evidence_type="image",
        purpose="concern_support",
        source_channel="web",
        visibility=visibility,
        review_status="pending_review",
        storage_provider="ci-private",
        storage_state="available",
        object_key=f"evidence/tl/{suffix:032x}.jpg",
        safe_display_name=f"vehicle-evidence-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
        uploaded_at=datetime(2026, 8, 16, 22, 0, suffix),
    )
    db.session.add(row)
    db.session.commit()
    return row


def main() -> None:
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            raise SystemExit("This verifier must run against PostgreSQL")

        now = datetime(2026, 8, 16, 22, 0, 0)
        owner = User(
            name="Timeline PostgreSQL Owner",
            email="timeline-postgres-owner@example.com",
            phone_number="+2348000000184",
            role="user",
            is_active=True,
            email_verified_at=now,
        )
        owner.set_password("Password123")
        driver = User(
            name="Timeline PostgreSQL Driver",
            email="timeline-postgres-driver@example.com",
            phone_number="+2348000000185",
            role="driver",
            is_active=True,
            email_verified_at=now,
        )
        driver.set_password("Password123")
        advisor = User(
            name="Timeline PostgreSQL Advisor",
            email="timeline-postgres-advisor@example.com",
            phone_number="+2348000000186",
            role="admin",
            is_active=True,
            email_verified_at=now,
        )
        advisor.set_password("Password123")
        db.session.add_all([owner, driver, advisor])
        db.session.flush()

        car = Car(
            brand="Mercedes-Benz",
            model="GLE 450",
            year=2024,
            vin="W1NEVIDTIMELINE184",
            current_mileage=14000,
        )
        db.session.add(car)
        db.session.flush()
        db.session.add(
            CarOwnership(
                user_id=owner.id,
                car_id=car.id,
                plate_number="ET-184-LA",
                mileage_at_transfer=14000,
                is_active=True,
            )
        )
        db.session.add(CarDriver(car_id=car.id, user_id=driver.id, is_active=True))
        db.session.commit()

        concern = CarFault(
            car_id=car.id,
            title="Timeline PostgreSQL concern",
            category="observation",
            description="Controlled PostgreSQL timeline concern.",
            status="reported",
            reported_by=owner.id,
            source="client",
            reported_at=now,
        )
        db.session.add(concern)
        db.session.flush()
        reported_event = emit_vehicle_event(
            car_id=car.id,
            event_type="concern.reported",
            subject_type="reported_concern",
            subject_id=concern.id,
            actor_type="user",
            actor_user_id=owner.id,
            visibility="client",
            source="verify.timeline",
            occurred_at=now,
            title="Concern reported",
            progression_direction="insufficient_evidence",
            idempotency_key=f"verify-timeline-concern:{concern.id}",
            previous_state=None,
            new_state="reported",
        )
        db.session.commit()

        owner_evidence = _evidence(
            car_id=car.id,
            uploader_id=owner.id,
            suffix=1,
        )
        driver_evidence = _evidence(
            car_id=car.id,
            uploader_id=driver.id,
            suffix=2,
        )
        internal_evidence = _evidence(
            car_id=car.id,
            uploader_id=advisor.id,
            suffix=3,
            visibility="internal",
        )

        for evidence in (owner_evidence, driver_evidence, internal_evidence):
            review_evidence(
                reviewer_user_id=advisor.id,
                evidence_id=evidence.id,
                decision="accepted",
                reason_code="advisor_verified",
            )

        link_evidence_to_reported_concern(
            reviewer_user_id=advisor.id,
            evidence_id=owner_evidence.id,
            concern_id=concern.id,
        )

        owner_projection = get_client_safe_evidence_timeline(
            car_id=car.id,
            viewer_user_id=owner.id,
        )
        if {row.evidence_id for row in owner_projection.records} != {
            owner_evidence.id,
            driver_evidence.id,
        }:
            raise SystemExit("Owner evidence timeline visibility is incorrect")

        driver_projection = get_client_safe_evidence_timeline(
            car_id=car.id,
            viewer_user_id=driver.id,
        )
        if [row.evidence_id for row in driver_projection.records] != [driver_evidence.id]:
            raise SystemExit("Driver evidence timeline exceeded own-evidence scope")

        advisor_projection = get_advisor_evidence_timeline(
            car_id=car.id,
            viewer_user_id=advisor.id,
        )
        if {row.evidence_id for row in advisor_projection.records} != {
            owner_evidence.id,
            driver_evidence.id,
            internal_evidence.id,
        }:
            raise SystemExit("Advisor evidence timeline visibility is incomplete")

        owner_payload = str(owner_projection.to_dict()).lower()
        for forbidden in (
            "object_key",
            "sha256",
            "storage_provider",
            "storage_state",
            "review_reason_code",
            "uploaded_by_user_id",
        ):
            if forbidden in owner_payload:
                raise SystemExit(f"Client evidence projection leaked {forbidden}")

        progression = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )
        if progression.progression != "insufficient_evidence":
            raise SystemExit("Evidence governance events changed concern progression")
        if tuple(item.event_id for item in progression.timeline) != (reported_event.id,):
            raise SystemExit("Evidence governance events entered concern progression timeline")

        print(
            "Wave 1.4 safe evidence timeline verified on PostgreSQL with "
            "client/advisor visibility and concern-progression isolation."
        )


if __name__ == "__main__":
    main()
