"""Verify Wave 1.4 evidence review and concern linking on PostgreSQL."""

from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402
from evidence.models import EvidenceLink, VehicleEvidence  # noqa: E402
from evidence.review import (  # noqa: E402
    EvidenceReviewAccessError,
    link_evidence_to_reported_concern,
    review_evidence,
)
from extensions import db  # noqa: E402
from models import Car, CarFault, CarOwnership, User  # noqa: E402


def main() -> None:
    with app.app_context():
        now = datetime(2026, 8, 16, 18, 0, 0)
        owner = User(
            name="Evidence Review PostgreSQL Owner",
            email="evidence-review-postgres-owner@example.com",
            phone_number="+2348000000174",
            role="user",
            is_active=True,
            email_verified_at=now,
        )
        owner.set_password("Password123")
        advisor = User(
            name="Evidence Review PostgreSQL Advisor",
            email="evidence-review-postgres-advisor@example.com",
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
            vin="W1NEVIDREVIEWPG174",
            current_mileage=9000,
        )
        other_car = Car(
            brand="Mercedes-Benz",
            model="GLC 300",
            year=2024,
            vin="W1NEVIDREVIEWPG175",
            current_mileage=8000,
        )
        db.session.add_all([car, other_car])
        db.session.flush()
        db.session.add_all(
            [
                CarOwnership(
                    user_id=owner.id,
                    car_id=car.id,
                    plate_number="ER-174-LA",
                    mileage_at_transfer=9000,
                    is_active=True,
                ),
                CarOwnership(
                    user_id=owner.id,
                    car_id=other_car.id,
                    plate_number="ER-175-LA",
                    mileage_at_transfer=8000,
                    is_active=True,
                ),
            ]
        )
        db.session.flush()

        concern = CarFault(
            car_id=car.id,
            title="PostgreSQL evidence-linked concern",
            category="observation",
            description="Test concern used only for Wave 1.4 PostgreSQL verification.",
            status="reported",
            reported_by=owner.id,
            source="client",
            reported_at=now,
        )
        other_concern = CarFault(
            car_id=other_car.id,
            title="Other vehicle concern",
            category="observation",
            description="Cross-vehicle control row.",
            status="reported",
            reported_by=owner.id,
            source="client",
            reported_at=now,
        )
        db.session.add_all([concern, other_concern])
        db.session.flush()

        payload = b"sanitized-postgres-review-evidence"
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
            object_key="evidence/aa/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
            safe_display_name="vehicle-evidence-pg.jpg",
            content_type="image/jpeg",
            byte_size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            consent_basis="explicit_web_upload",
            lawful_purpose="vehicle_care",
            uploaded_at=now,
        )
        db.session.add(evidence)
        db.session.commit()

        try:
            review_evidence(
                reviewer_user_id=owner.id,
                evidence_id=evidence.id,
                decision="accepted",
                reason_code="advisor_verified",
            )
        except EvidenceReviewAccessError:
            pass
        else:
            raise SystemExit("Owner unexpectedly acquired evidence-review authority")

        accepted = review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )
        if accepted.review_status != "accepted":
            raise SystemExit("Advisor review did not persist accepted state")

        try:
            link_evidence_to_reported_concern(
                reviewer_user_id=advisor.id,
                evidence_id=evidence.id,
                concern_id=other_concern.id,
            )
        except EvidenceReviewAccessError:
            pass
        else:
            raise SystemExit("Cross-vehicle EvidenceLink was not rejected")

        linked = link_evidence_to_reported_concern(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            concern_id=concern.id,
        )
        replay = link_evidence_to_reported_concern(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            concern_id=concern.id,
        )
        if not linked.created or replay.created or linked.link_id != replay.link_id:
            raise SystemExit("EvidenceLink idempotency contract failed")

        link = db.session.get(EvidenceLink, linked.link_id)
        if link is None or link.car_id != car.id or link.subject_id != concern.id:
            raise SystemExit("EvidenceLink persisted incorrect vehicle/subject scope")

        print("Wave 1.4 evidence review/link verified on PostgreSQL.")


if __name__ == "__main__":
    main()
