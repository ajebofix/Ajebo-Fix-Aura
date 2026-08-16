from __future__ import annotations

from datetime import datetime
import hashlib

import pytest

from evidence.models import EvidenceLink, VehicleEvidence
from evidence.review import (
    EvidenceReviewError,
    link_evidence_to_reported_concern,
    review_evidence,
)
from extensions import db
from models import Car, CarFault, CarOwnership, User, VehicleEvent
from services.event_emission import (
    EventAuthorityError,
    EventEmissionError,
    emit_vehicle_event,
)


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Evidence Event User {suffix}",
        email=f"evidence-event-{suffix}@example.com",
        phone_number=f"+234866000{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 16, 21, 0, 0),
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
        vin=f"W1NEVIDEVENT{suffix:05d}",
        current_mileage=12000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"EE-{suffix:03d}-LA",
            mileage_at_transfer=12000,
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
    visibility: str = "client",
) -> VehicleEvidence:
    payload = f"canonical-evidence-event-{suffix}".encode()
    evidence = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=uploader.id,
        evidence_type="image",
        purpose="concern_support",
        source_channel="web",
        visibility=visibility,
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
        uploaded_at=datetime(2026, 8, 16, 21, 5, 0),
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def _concern(*, car: Car, reporter: User, suffix: int) -> CarFault:
    concern = CarFault(
        car_id=car.id,
        title=f"Evidence event concern {suffix}",
        category="observation",
        description="Controlled concern for evidence event tests.",
        status="reported",
        reported_by=reporter.id,
        source="client",
        reported_at=datetime(2026, 8, 16, 21, 10, 0),
    )
    db.session.add(concern)
    db.session.commit()
    return concern


def test_evidence_review_emits_one_non_diagnostic_canonical_event(app):
    with app.app_context():
        owner = _user(suffix=1)
        advisor = _user(suffix=2, role="admin")
        car = _owned_car(owner, suffix=1)
        evidence = _evidence(car=car, uploader=owner, suffix=1)

        result = review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )

        event = VehicleEvent.query.filter_by(
            subject_type="vehicle_evidence",
            subject_id=evidence.id,
            event_type="evidence.reviewed",
        ).one()
        assert result.review_status == "accepted"
        assert event.actor_user_id == advisor.id
        assert event.actor_authority == "advisor"
        assert event.visibility == "client"
        assert event.previous_state == "pending_review"
        assert event.new_state == "accepted"
        assert event.progression_direction == "not_applicable"
        assert event.data == {"review_reason_code": "advisor_verified"}
        assert event.evidence_refs == [
            {"type": "vehicle_evidence", "id": evidence.id}
        ]
        serialized = str(event.data).lower()
        assert "diagnosis" not in serialized
        assert "object_key" not in serialized
        assert "sha256" not in serialized


def test_rejected_evidence_review_emits_rejected_state_without_health_direction(app):
    with app.app_context():
        owner = _user(suffix=3)
        advisor = _user(suffix=4, role="admin")
        car = _owned_car(owner, suffix=2)
        evidence = _evidence(
            car=car,
            uploader=owner,
            suffix=2,
            visibility="advisor",
        )

        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="rejected",
            reason_code="insufficient_quality",
        )

        event = VehicleEvent.query.filter_by(
            subject_type="vehicle_evidence",
            subject_id=evidence.id,
            event_type="evidence.reviewed",
        ).one()
        assert event.visibility == "advisor"
        assert event.new_state == "rejected"
        assert event.progression_direction == "not_applicable"


def test_review_replay_is_event_idempotent(app):
    with app.app_context():
        owner = _user(suffix=5)
        advisor = _user(suffix=6, role="admin")
        car = _owned_car(owner, suffix=3)
        evidence = _evidence(car=car, uploader=owner, suffix=3)

        first = review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="sufficient_for_record",
        )
        second = review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )

        assert second.reviewed_at == first.reviewed_at
        assert second.review_reason_code == "sufficient_for_record"
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_evidence",
            subject_id=evidence.id,
            event_type="evidence.reviewed",
        ).count() == 1


def test_link_emits_one_canonical_event_and_replay_is_idempotent(app):
    with app.app_context():
        owner = _user(suffix=7)
        advisor = _user(suffix=8, role="admin")
        car = _owned_car(owner, suffix=4)
        evidence = _evidence(car=car, uploader=owner, suffix=4)
        concern = _concern(car=car, reporter=owner, suffix=1)
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
        assert second.link_id == first.link_id
        event = VehicleEvent.query.filter_by(
            subject_type="vehicle_evidence",
            subject_id=evidence.id,
            event_type="evidence.linked",
        ).one()
        assert event.progression_direction == "not_applicable"
        assert event.previous_state is None
        assert event.new_state is None
        assert event.data == {
            "link_id": first.link_id,
            "linked_subject_type": "reported_concern",
            "linked_subject_id": concern.id,
            "relationship_type": "supports",
        }
        assert VehicleEvent.query.filter_by(
            event_type="evidence.linked",
            subject_id=evidence.id,
        ).count() == 1


def test_owner_cannot_emit_professional_evidence_event_directly(app):
    with app.app_context():
        owner = _user(suffix=9)
        car = _owned_car(owner, suffix=5)
        evidence = _evidence(car=car, uploader=owner, suffix=5)

        with pytest.raises(EventAuthorityError, match="advisor authority"):
            emit_vehicle_event(
                car_id=car.id,
                event_type="evidence.reviewed",
                subject_type="vehicle_evidence",
                subject_id=evidence.id,
                actor_type="user",
                actor_user_id=owner.id,
                visibility="client",
                source="tests.evidence_event",
                occurred_at=datetime(2026, 8, 16, 21, 20, 0),
                title="Vehicle evidence reviewed",
                progression_direction="not_applicable",
                idempotency_key="owner-must-not-review-evidence",
                previous_state="pending_review",
                new_state="accepted",
            )


def test_evidence_event_rejects_wrong_subject_and_false_progression(app):
    with app.app_context():
        owner = _user(suffix=10)
        advisor = _user(suffix=11, role="admin")
        car = _owned_car(owner, suffix=6)
        evidence = _evidence(car=car, uploader=owner, suffix=6)

        with pytest.raises(EventEmissionError, match="vehicle_evidence"):
            emit_vehicle_event(
                car_id=car.id,
                event_type="evidence.reviewed",
                subject_type="reported_concern",
                subject_id=evidence.id,
                actor_type="user",
                actor_user_id=advisor.id,
                visibility="client",
                source="tests.evidence_event",
                occurred_at=datetime(2026, 8, 16, 21, 25, 0),
                title="Vehicle evidence reviewed",
                progression_direction="not_applicable",
                idempotency_key="wrong-evidence-subject",
                previous_state="pending_review",
                new_state="accepted",
            )

        with pytest.raises(EventEmissionError, match="progression direction"):
            emit_vehicle_event(
                car_id=car.id,
                event_type="evidence.reviewed",
                subject_type="vehicle_evidence",
                subject_id=evidence.id,
                actor_type="user",
                actor_user_id=advisor.id,
                visibility="client",
                source="tests.evidence_event",
                occurred_at=datetime(2026, 8, 16, 21, 25, 0),
                title="Vehicle evidence reviewed",
                progression_direction="improving",
                idempotency_key="false-evidence-progression",
                previous_state="pending_review",
                new_state="accepted",
            )


def test_review_rolls_back_if_canonical_event_fails(app, monkeypatch):
    with app.app_context():
        owner = _user(suffix=12)
        advisor = _user(suffix=13, role="admin")
        car = _owned_car(owner, suffix=7)
        evidence = _evidence(car=car, uploader=owner, suffix=7)
        evidence_id = evidence.id

        def fail_event(**_kwargs):
            raise EventEmissionError("synthetic canonical event failure")

        monkeypatch.setattr("evidence.review.emit_vehicle_event", fail_event)

        with pytest.raises(EvidenceReviewError, match="review and event"):
            review_evidence(
                reviewer_user_id=advisor.id,
                evidence_id=evidence_id,
                decision="accepted",
                reason_code="advisor_verified",
            )

        evidence = db.session.get(VehicleEvidence, evidence_id)
        assert evidence.review_status == "pending_review"
        assert evidence.reviewed_by_user_id is None
        assert evidence.reviewed_at is None
        assert VehicleEvent.query.filter_by(subject_id=evidence_id).count() == 0


def test_link_rolls_back_if_canonical_event_fails(app, monkeypatch):
    with app.app_context():
        owner = _user(suffix=14)
        advisor = _user(suffix=15, role="admin")
        car = _owned_car(owner, suffix=8)
        evidence = _evidence(car=car, uploader=owner, suffix=8)
        concern = _concern(car=car, reporter=owner, suffix=2)
        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )

        def fail_event(**_kwargs):
            raise EventEmissionError("synthetic canonical event failure")

        monkeypatch.setattr("evidence.review.emit_vehicle_event", fail_event)

        with pytest.raises(EvidenceReviewError, match="link and event"):
            link_evidence_to_reported_concern(
                reviewer_user_id=advisor.id,
                evidence_id=evidence.id,
                concern_id=concern.id,
            )

        assert EvidenceLink.query.filter_by(evidence_id=evidence.id).count() == 0
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_evidence",
            subject_id=evidence.id,
            event_type="evidence.linked",
        ).count() == 0
