"""Verify Wave 1.4 pending evidence interaction metadata on PostgreSQL.

No object-storage or provider network call occurs here. The verifier proves that
advisor pending-review metadata is authority-filtered and privacy-safe while
pending evidence remains absent from reviewed client/advisor timeline history.
"""

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
from evidence.review import review_evidence  # noqa: E402
from extensions import db  # noqa: E402
from models import Car, CarOwnership, User  # noqa: E402
from services.evidence_interaction import (  # noqa: E402
    EvidenceInteractionAccessError,
    get_advisor_pending_evidence_queue,
)
from services.evidence_timeline import (  # noqa: E402
    get_advisor_evidence_timeline,
    get_client_safe_evidence_timeline,
)


def _evidence(
    *,
    car_id: int,
    uploader_id: int,
    suffix: int,
    visibility: str = "client",
) -> VehicleEvidence:
    payload = f"postgres-interaction-evidence-{suffix}".encode()
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
        object_key=f"evidence/interaction/{suffix:032x}.jpg",
        safe_display_name=f"vehicle-evidence-interaction-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
        uploaded_at=datetime(2026, 8, 16, 23, 0, suffix),
    )
    db.session.add(row)
    db.session.commit()
    return row


def main() -> None:
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            raise SystemExit("This verifier must run against PostgreSQL")

        now = datetime(2026, 8, 16, 23, 0, 0)
        owner = User(
            name="Interaction PostgreSQL Owner",
            email="interaction-postgres-owner@example.com",
            phone_number="+2348000000194",
            role="user",
            is_active=True,
            email_verified_at=now,
        )
        owner.set_password("Password123")
        advisor = User(
            name="Interaction PostgreSQL Advisor",
            email="interaction-postgres-advisor@example.com",
            phone_number="+2348000000195",
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
            vin="W1NEVIDINTERACT194",
            current_mileage=18000,
        )
        db.session.add(car)
        db.session.flush()
        db.session.add(
            CarOwnership(
                user_id=owner.id,
                car_id=car.id,
                plate_number="EI-194-LA",
                mileage_at_transfer=18000,
                is_active=True,
            )
        )
        db.session.commit()

        reviewed = _evidence(
            car_id=car.id,
            uploader_id=owner.id,
            suffix=1,
        )
        pending = _evidence(
            car_id=car.id,
            uploader_id=owner.id,
            suffix=2,
        )
        advisor_pending = _evidence(
            car_id=car.id,
            uploader_id=advisor.id,
            suffix=3,
            visibility="advisor",
        )

        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=reviewed.id,
            decision="accepted",
            reason_code="advisor_verified",
        )

        queue = get_advisor_pending_evidence_queue(
            car_id=car.id,
            viewer_user_id=advisor.id,
        )
        queue_ids = [row.evidence_id for row in queue.records]
        if queue_ids != [pending.id, advisor_pending.id]:
            raise SystemExit(
                f"Unexpected advisor pending-evidence queue ordering/scope: {queue_ids}"
            )

        queue_payload = str(queue.to_dict()).lower()
        for forbidden in (
            "object_key",
            "safe_display_name",
            "sha256",
            "storage_provider",
            "storage_failure_reason_code",
        ):
            if forbidden in queue_payload:
                raise SystemExit(f"Pending evidence queue leaked {forbidden}")

        try:
            get_advisor_pending_evidence_queue(
                car_id=car.id,
                viewer_user_id=owner.id,
            )
        except EvidenceInteractionAccessError:
            pass
        else:
            raise SystemExit("Owner unexpectedly received advisor pending-evidence queue")

        owner_timeline = get_client_safe_evidence_timeline(
            car_id=car.id,
            viewer_user_id=owner.id,
        )
        owner_ids = {row.evidence_id for row in owner_timeline.records}
        if owner_ids != {reviewed.id}:
            raise SystemExit(
                f"Pending evidence leaked into reviewed owner timeline: {sorted(owner_ids)}"
            )

        advisor_timeline = get_advisor_evidence_timeline(
            car_id=car.id,
            viewer_user_id=advisor.id,
        )
        advisor_ids = {row.evidence_id for row in advisor_timeline.records}
        if advisor_ids != {reviewed.id}:
            raise SystemExit(
                f"Pending evidence leaked into reviewed advisor timeline: {sorted(advisor_ids)}"
            )

        print(
            "Wave 1.4 pending evidence interaction verified on PostgreSQL with "
            "advisor-only queue access, privacy-safe metadata and reviewed-timeline isolation."
        )


if __name__ == "__main__":
    main()
