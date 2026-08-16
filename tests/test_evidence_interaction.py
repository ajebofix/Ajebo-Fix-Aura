from __future__ import annotations

from datetime import datetime
import hashlib

import pytest

from evidence.models import VehicleEvidence
from evidence.review import review_evidence
from extensions import db
from models import Car, CarOwnership, User
from services.evidence_interaction import (
    EvidenceInteractionAccessError,
    get_advisor_pending_evidence_queue,
)


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Evidence Interaction User {suffix}",
        email=f"evidence-interaction-{suffix}@example.com",
        phone_number=f"+234811000{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 16, 23, 30, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _owned_car(owner: User, *, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1NEVIDINTERACT{suffix:02d}",
        current_mileage=16000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"EI-{suffix:03d}-LA",
            mileage_at_transfer=16000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _evidence(
    *,
    car: Car,
    uploader: User,
    suffix: int,
    evidence_type: str = "image",
    storage_state: str = "available",
    visibility: str = "client",
) -> VehicleEvidence:
    payload = f"pending-evidence-interaction-{suffix}".encode()
    evidence = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=uploader.id,
        evidence_type=evidence_type,
        purpose="concern_support",
        source_channel="web",
        visibility=visibility,
        review_status="pending_review",
        storage_provider="test-private",
        storage_state=storage_state,
        storage_failure_reason_code=(
            "write_failed" if storage_state == "failed" else None
        ),
        object_key=f"evidence/interaction/{suffix:032x}.jpg",
        safe_display_name=f"vehicle-evidence-{suffix}.jpg",
        content_type="image/jpeg" if evidence_type == "image" else "application/pdf",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
        uploaded_at=datetime(2026, 8, 16, 23, 35, suffix % 60),
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def test_advisor_pending_queue_returns_only_available_pending_images(app):
    with app.app_context():
        owner = _user(suffix=1)
        advisor = _user(suffix=2, role="admin")
        car = _owned_car(owner, suffix=1)
        pending = _evidence(car=car, uploader=owner, suffix=1)
        failed = _evidence(
            car=car,
            uploader=owner,
            suffix=2,
            storage_state="failed",
        )
        document = _evidence(
            car=car,
            uploader=owner,
            suffix=3,
            evidence_type="document",
        )
        reviewed = _evidence(car=car, uploader=owner, suffix=4)
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
        assert [item.evidence_id for item in queue.records] == [pending.id]
        assert queue.records[0].uploader_label == owner.name
        assert queue.records[0].content_type == "image/jpeg"
        assert failed.id not in {item.evidence_id for item in queue.records}
        assert document.id not in {item.evidence_id for item in queue.records}
        assert reviewed.id not in {item.evidence_id for item in queue.records}


def test_pending_queue_payload_never_contains_private_storage_identifiers(app):
    with app.app_context():
        owner = _user(suffix=3)
        advisor = _user(suffix=4, role="admin")
        car = _owned_car(owner, suffix=2)
        evidence = _evidence(
            car=car,
            uploader=owner,
            suffix=5,
            visibility="advisor",
        )

        payload = get_advisor_pending_evidence_queue(
            car_id=car.id,
            viewer_user_id=advisor.id,
        ).to_dict()
        serialized = str(payload).lower()
        assert evidence.id in {row["evidence_id"] for row in payload["records"]}
        for forbidden in (
            "object_key",
            "safe_display_name",
            "sha256",
            "storage_provider",
            "storage_failure_reason_code",
        ):
            assert forbidden not in serialized


def test_owner_cannot_read_advisor_pending_evidence_queue(app):
    with app.app_context():
        owner = _user(suffix=5)
        car = _owned_car(owner, suffix=3)
        _evidence(car=car, uploader=owner, suffix=6)

        with pytest.raises(EvidenceInteractionAccessError):
            get_advisor_pending_evidence_queue(
                car_id=car.id,
                viewer_user_id=owner.id,
            )
