from __future__ import annotations

from datetime import datetime

import pytest

from evidence.models import EvidenceLink, VehicleEvidence
from extensions import db
from models import Car, CarOwnership, TreatmentPlan, User, VehicleEvent
from services.treatment_outcome_recording import (
    TreatmentOutcomeAuthorityError,
    TreatmentOutcomeIdempotencyConflict,
    TreatmentOutcomeProvenanceError,
    TreatmentOutcomeRecordingService,
)
from treatment.models import TreatmentOutcome


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Outcome {role} {suffix}",
        email=f"outcome-{role}-{suffix}@example.com",
        phone_number=f"+2348981{suffix:06d}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _context(*, suffix: int, plan_status: str = "in_progress"):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 1000, role="admin")
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1NOUTCOME{suffix:08d}",
        current_mileage=25000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"OC-{suffix:03d}-LA",
            mileage_at_transfer=24000,
            is_active=True,
        )
    )
    plan = TreatmentPlan(
        car_id=car.id,
        advisor_id=advisor.id,
        title="Vehicle Treatment Plan",
        client_summary="Authorized care pathway.",
        internal_instructions="Advisor-only plan context",
        status=plan_status,
    )
    db.session.add(plan)
    db.session.commit()
    return owner, advisor, car, plan


def _evidence(
    *,
    car: Car,
    uploader: User,
    suffix: int,
    visibility: str = "client",
    review_status: str = "accepted",
    storage_state: str = "available",
) -> VehicleEvidence:
    evidence = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=uploader.id,
        evidence_type="image",
        purpose="treatment_evidence",
        source_channel="web",
        visibility=visibility,
        review_status=review_status,
        storage_provider="r2",
        storage_state=storage_state,
        object_key=f"treatment/outcome/{car.id}/{suffix}.jpg",
        safe_display_name=f"outcome-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=1024,
        sha256=(f"{suffix:064x}"[-64:]),
        consent_basis="client_submission",
        lawful_purpose="vehicle care evidence",
        reviewed_by_user_id=(uploader.id if review_status == "accepted" else None),
        reviewed_at=(datetime(2026, 8, 29, 18, 0, 0) if review_status == "accepted" else None),
        review_reason_code=("advisor_verified" if review_status == "accepted" else None),
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def _events(*, event_type: str, subject_id: int):
    return (
        VehicleEvent.query.filter_by(event_type=event_type, subject_id=subject_id)
        .order_by(VehicleEvent.id.asc())
        .all()
    )


def test_reviewed_evidence_outcome_links_and_emits_atomically(app):
    with app.app_context():
        owner, advisor, car, plan = _context(suffix=1)
        evidence = _evidence(car=car, uploader=owner, suffix=1)

        outcome = TreatmentOutcomeRecordingService.record(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            recording_key="follow-up-1",
            progression_direction="improving",
            summary="Post-treatment operating behavior is improving.",
            provenance_kind="reviewed_evidence",
            evidence_ids=[evidence.id],
            observed_at=datetime(2026, 8, 30, 10, 0, 0),
            occurred_at=datetime(2026, 8, 30, 10, 5, 0),
        )
        db.session.commit()

        assert outcome.id is not None
        assert plan.status == "in_progress"
        link = EvidenceLink.query.filter_by(
            evidence_id=evidence.id,
            subject_type="treatment_outcome",
            subject_id=outcome.id,
            relationship_type="supports",
        ).one()
        assert link.car_id == car.id

        outcome_events = _events(
            event_type="treatment.outcome_recorded",
            subject_id=plan.id,
        )
        assert len(outcome_events) == 1
        event = outcome_events[0]
        assert event.previous_state == "in_progress"
        assert event.new_state == "in_progress"
        assert event.progression_direction == "improving"
        assert event.data["outcome_id"] == outcome.id
        assert event.data["provenance_kind"] == "reviewed_evidence"
        assert event.evidence_refs == [
            {"type": "vehicle_evidence", "id": evidence.id}
        ]

        linked_events = _events(
            event_type="evidence.linked",
            subject_id=evidence.id,
        )
        assert len(linked_events) == 1
        assert linked_events[0].data["linked_subject_type"] == "treatment_outcome"
        assert linked_events[0].data["linked_subject_id"] == outcome.id


def test_outcome_recording_is_idempotent_and_conflict_safe(app):
    with app.app_context():
        owner, advisor, car, plan = _context(suffix=2)
        evidence = _evidence(car=car, uploader=owner, suffix=2)
        kwargs = dict(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            recording_key="stable-follow-up",
            progression_direction="stable",
            summary="Observed condition remains stable.",
            provenance_kind="reviewed_evidence",
            evidence_ids=[evidence.id],
            observed_at=datetime(2026, 8, 30, 12, 0, 0),
            occurred_at=datetime(2026, 8, 30, 12, 5, 0),
        )

        first = TreatmentOutcomeRecordingService.record(**kwargs)
        db.session.commit()
        second = TreatmentOutcomeRecordingService.record(**kwargs)
        db.session.commit()

        assert second.id == first.id
        assert TreatmentOutcome.query.filter_by(treatment_plan_id=plan.id).count() == 1
        assert EvidenceLink.query.filter_by(
            subject_type="treatment_outcome",
            subject_id=first.id,
        ).count() == 1
        assert len(_events(event_type="treatment.outcome_recorded", subject_id=plan.id)) == 1

        with pytest.raises(TreatmentOutcomeIdempotencyConflict):
            TreatmentOutcomeRecordingService.record(
                **{**kwargs, "summary": "Different semantics must fail closed."}
            )


def test_owner_cannot_record_professional_outcome(app):
    with app.app_context():
        owner, _advisor, _car, plan = _context(suffix=3)

        with pytest.raises(TreatmentOutcomeAuthorityError):
            TreatmentOutcomeRecordingService.record(
                plan_id=plan.id,
                actor_user_id=owner.id,
                recording_key="owner-blocked",
                progression_direction="stable",
                summary="Owner must not create professional outcome facts.",
                provenance_kind="professional_observation",
                provenance_data={"observation_source": "client_follow_up"},
            )

        assert TreatmentOutcome.query.filter_by(treatment_plan_id=plan.id).count() == 0


def test_rejected_evidence_rolls_back_flushed_outcome(app):
    with app.app_context():
        owner, advisor, car, plan = _context(suffix=4)
        evidence = _evidence(
            car=car,
            uploader=owner,
            suffix=4,
            review_status="pending_review",
        )

        with pytest.raises(TreatmentOutcomeProvenanceError, match="advisor-accepted"):
            TreatmentOutcomeRecordingService.record(
                plan_id=plan.id,
                actor_user_id=advisor.id,
                recording_key="rejected-evidence",
                progression_direction="improving",
                summary="This should not persist.",
                provenance_kind="reviewed_evidence",
                evidence_ids=[evidence.id],
            )
        db.session.rollback()

        assert TreatmentOutcome.query.filter_by(treatment_plan_id=plan.id).count() == 0
        assert EvidenceLink.query.filter_by(subject_type="treatment_outcome").count() == 0
        assert _events(event_type="treatment.outcome_recorded", subject_id=plan.id) == []


def test_cross_vehicle_evidence_fails_closed_without_half_record(app):
    with app.app_context():
        owner, advisor, _car, plan = _context(suffix=5)
        other_owner, _other_advisor, other_car, _other_plan = _context(suffix=105)
        evidence = _evidence(car=other_car, uploader=other_owner, suffix=5)

        with pytest.raises(Exception, match="same vehicle"):
            TreatmentOutcomeRecordingService.record(
                plan_id=plan.id,
                actor_user_id=advisor.id,
                recording_key="wrong-car",
                progression_direction="stable",
                summary="Cross-vehicle evidence must fail closed.",
                provenance_kind="reviewed_evidence",
                evidence_ids=[evidence.id],
            )
        db.session.rollback()

        assert TreatmentOutcome.query.filter_by(treatment_plan_id=plan.id).count() == 0


def test_client_visible_outcome_rejects_advisor_only_evidence_reference(app):
    with app.app_context():
        owner, advisor, car, plan = _context(suffix=6)
        evidence = _evidence(
            car=car,
            uploader=owner,
            suffix=6,
            visibility="advisor",
        )

        with pytest.raises(TreatmentOutcomeProvenanceError, match="Client-visible"):
            TreatmentOutcomeRecordingService.record(
                plan_id=plan.id,
                actor_user_id=advisor.id,
                recording_key="visibility-leak",
                progression_direction="stable",
                summary="Client event must not expose advisor-only evidence.",
                provenance_kind="reviewed_evidence",
                evidence_ids=[evidence.id],
                visibility="client",
            )
        db.session.rollback()

        assert TreatmentOutcome.query.filter_by(treatment_plan_id=plan.id).count() == 0


def test_professional_observation_can_record_without_evidence(app):
    with app.app_context():
        _owner, advisor, _car, plan = _context(suffix=7)

        outcome = TreatmentOutcomeRecordingService.record(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            recording_key="road-test-observation",
            progression_direction="stable",
            summary="Advisor road test shows stable operating behavior.",
            provenance_kind="professional_observation",
            provenance_data={
                "observation_source": "road_test",
                "reference": "post-treatment road test",
            },
        )
        db.session.commit()

        assert outcome.provenance_kind == "professional_observation"
        assert outcome.provenance_data["observation_source"] == "road_test"
        assert EvidenceLink.query.filter_by(
            subject_type="treatment_outcome",
            subject_id=outcome.id,
        ).count() == 0
        event = _events(
            event_type="treatment.outcome_recorded",
            subject_id=plan.id,
        )[0]
        assert event.evidence_refs == []


def test_insufficient_evidence_outcome_does_not_claim_improvement(app):
    with app.app_context():
        _owner, advisor, _car, plan = _context(suffix=8)

        outcome = TreatmentOutcomeRecordingService.record(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            recording_key="insufficient-follow-up",
            progression_direction="insufficient_evidence",
            summary="There is not yet enough accepted evidence to determine direction.",
            provenance_kind="insufficient_evidence",
        )
        db.session.commit()

        assert outcome.progression_direction == "insufficient_evidence"
        event = _events(
            event_type="treatment.outcome_recorded",
            subject_id=plan.id,
        )[0]
        assert event.progression_direction == "insufficient_evidence"
        assert plan.status == "in_progress"
