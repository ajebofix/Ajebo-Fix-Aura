from __future__ import annotations

from datetime import datetime
import hashlib

import pytest

from evidence.models import EvidenceLink, VehicleEvidence
from evidence.review import (
    EvidenceReviewAccessError,
    EvidenceReviewConflict,
    link_evidence_to_reported_concern,
    review_evidence,
)
from extensions import db
from models import Car, CarFault, CarOwnership, User


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Evidence Review User {suffix}",
        email=f"evidence-review-{suffix}@example.com",
        phone_number=f"+234844000{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 16, 12, 0, 0),
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
        vin=f"W1NEVIDREVIEW{suffix:04d}",
        current_mileage=9000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"RW-{suffix:03d}-LA",
            mileage_at_transfer=9000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _evidence(*, car: Car, uploader: User, suffix: int) -> VehicleEvidence:
    payload = f"reviewed-evidence-{suffix}".encode()
    row = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=uploader.id,
        evidence_type="image",
        purpose="concern_support",
        source_channel="web",
        visibility="client",
        review_status="pending_review",
        storage_provider="test-private",
        storage_state="available",
        object_key=f"evidence/{suffix:02x}/{suffix:032x}.jpg",
        safe_display_name=f"vehicle-evidence-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
        uploaded_at=datetime(2026, 8, 16, 13, 0, 0),
    )
    db.session.add(row)
    db.session.commit()
    return row


def _concern(*, car: Car, reporter: User, suffix: int) -> CarFault:
    concern = CarFault(
        car_id=car.id,
        title=f"Reported concern {suffix}",
        category="observation",
        description="Client-reported observation for evidence-link testing.",
        status="reported",
        reported_by=reporter.id,
        source="client",
        reported_at=datetime(2026, 8, 16, 13, 30, 0),
    )
    db.session.add(concern)
    db.session.commit()
    return concern


def test_owner_cannot_accept_or_reject_evidence(app):
    with app.app_context():
        owner = _user(suffix=1)
        car = _owned_car(owner, suffix=1)
        evidence = _evidence(car=car, uploader=owner, suffix=1)

        with pytest.raises(EvidenceReviewAccessError):
            review_evidence(
                reviewer_user_id=owner.id,
                evidence_id=evidence.id,
                decision="accepted",
                reason_code="advisor_verified",
            )


def test_advisor_accepts_pending_available_evidence_without_diagnosis_state(app):
    with app.app_context():
        owner = _user(suffix=2)
        advisor = _user(suffix=3, role="admin")
        car = _owned_car(owner, suffix=2)
        evidence = _evidence(car=car, uploader=owner, suffix=2)

        result = review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )

        assert result.review_status == "accepted"
        assert result.reviewed_by_user_id == advisor.id
        db.session.refresh(evidence)
        assert evidence.review_status == "accepted"
        assert evidence.reviewed_by_user_id == advisor.id
        assert evidence.review_reason_code == "advisor_verified"
        assert evidence.storage_state == "available"
        assert not hasattr(evidence, "diagnosis")


def test_advisor_rejects_with_controlled_reason_and_cannot_overwrite_review(app):
    with app.app_context():
        owner = _user(suffix=4)
        advisor = _user(suffix=5, role="admin")
        car = _owned_car(owner, suffix=3)
        evidence = _evidence(car=car, uploader=owner, suffix=3)

        rejected = review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="rejected",
            reason_code="insufficient_quality",
        )
        assert rejected.review_status == "rejected"

        with pytest.raises(EvidenceReviewConflict, match="cannot be overwritten"):
            review_evidence(
                reviewer_user_id=advisor.id,
                evidence_id=evidence.id,
                decision="accepted",
                reason_code="advisor_verified",
            )


def test_invalid_review_reason_fails_before_mutation(app):
    with app.app_context():
        owner = _user(suffix=6)
        advisor = _user(suffix=7, role="admin")
        car = _owned_car(owner, suffix=4)
        evidence = _evidence(car=car, uploader=owner, suffix=4)

        with pytest.raises(EvidenceReviewConflict, match="approved"):
            review_evidence(
                reviewer_user_id=advisor.id,
                evidence_id=evidence.id,
                decision="accepted",
                reason_code="looks_like_bad_pump",
            )
        db.session.refresh(evidence)
        assert evidence.review_status == "pending_review"
        assert evidence.reviewed_by_user_id is None


def test_failed_or_deleted_evidence_cannot_be_accepted(app):
    with app.app_context():
        owner = _user(suffix=8)
        advisor = _user(suffix=9, role="admin")
        car = _owned_car(owner, suffix=5)
        evidence = _evidence(car=car, uploader=owner, suffix=5)
        evidence.storage_state = "failed"
        evidence.storage_failure_reason_code = "missing_object"
        db.session.commit()

        with pytest.raises(EvidenceReviewConflict, match="available"):
            review_evidence(
                reviewer_user_id=advisor.id,
                evidence_id=evidence.id,
                decision="accepted",
                reason_code="advisor_verified",
            )


def test_pending_or_rejected_evidence_cannot_be_linked(app):
    with app.app_context():
        owner = _user(suffix=10)
        advisor = _user(suffix=11, role="admin")
        car = _owned_car(owner, suffix=6)
        evidence = _evidence(car=car, uploader=owner, suffix=6)
        concern = _concern(car=car, reporter=owner, suffix=1)

        with pytest.raises(EvidenceReviewConflict, match="accepted"):
            link_evidence_to_reported_concern(
                reviewer_user_id=advisor.id,
                evidence_id=evidence.id,
                concern_id=concern.id,
            )


def test_accepted_evidence_links_only_to_same_vehicle_concern(app):
    with app.app_context():
        owner_one = _user(suffix=12)
        owner_two = _user(suffix=13)
        advisor = _user(suffix=14, role="admin")
        car_one = _owned_car(owner_one, suffix=7)
        car_two = _owned_car(owner_two, suffix=8)
        evidence = _evidence(car=car_one, uploader=owner_one, suffix=7)
        concern_one = _concern(car=car_one, reporter=owner_one, suffix=2)
        concern_two = _concern(car=car_two, reporter=owner_two, suffix=3)
        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="sufficient_for_record",
        )

        with pytest.raises(EvidenceReviewAccessError, match="same vehicle"):
            link_evidence_to_reported_concern(
                reviewer_user_id=advisor.id,
                evidence_id=evidence.id,
                concern_id=concern_two.id,
            )
        assert EvidenceLink.query.count() == 0

        result = link_evidence_to_reported_concern(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            concern_id=concern_one.id,
        )
        assert result.created is True
        assert result.relationship_type == "supports"
        link = db.session.get(EvidenceLink, result.link_id)
        assert link is not None
        assert link.car_id == car_one.id
        assert link.subject_type == "reported_concern"
        assert link.subject_id == concern_one.id
        assert link.created_by_user_id == advisor.id


def test_concern_link_creation_is_idempotent(app):
    with app.app_context():
        owner = _user(suffix=15)
        advisor = _user(suffix=16, role="admin")
        car = _owned_car(owner, suffix=9)
        evidence = _evidence(car=car, uploader=owner, suffix=8)
        concern = _concern(car=car, reporter=owner, suffix=4)
        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )

        first = link_evidence_to_reported_concern(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            concern_id=concern.id,
        )
        second = link_evidence_to_reported_concern(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            concern_id=concern.id,
        )

        assert first.created is True
        assert second.created is False
        assert first.link_id == second.link_id
        assert EvidenceLink.query.count() == 1
