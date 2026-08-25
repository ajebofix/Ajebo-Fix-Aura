from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import Car, CarOwnership, Consultation, User, VehicleAssessment, VehicleEvent
from models_assessment_addendum import VehicleAssessmentAddendum
from services.assessment_lifecycle import (
    AssessmentLifecycleError,
    AssessmentLifecycleService,
)
from services.assessment_report_builder import build_assessment_report


def _user(*, name: str, email: str, phone: str, role: str = "user") -> User:
    user = User(
        name=name,
        email=email,
        phone_number=phone,
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _context(*, suffix: str):
    owner = _user(
        name=f"Correction Owner {suffix}",
        email=f"correction-owner-{suffix}@example.com",
        phone=f"0800600{suffix.zfill(4)}",
    )
    advisor = _user(
        name=f"Correction Advisor {suffix}",
        email=f"correction-advisor-{suffix}@example.com",
        phone=f"0800700{suffix.zfill(4)}",
        role="admin",
    )
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1NCORRECTION{suffix.zfill(4)}",
        engine_number=f"M256-C-{suffix}",
        engine_type="M256",
        transmission_type="9G-TRONIC",
        current_mileage=32000,
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"CR-{suffix.zfill(3)}-LA",
        mileage_at_transfer=30000,
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
        scheduled_for=datetime(2026, 8, 24, 18, 0, 0),
        started_at=datetime(2026, 8, 24, 18, 1, 0),
    )
    db.session.add(consultation)
    db.session.commit()
    return owner, advisor, car, ownership, consultation


def _finalized(*, suffix: str):
    owner, advisor, car, ownership, consultation = _context(suffix=suffix)
    assessment = AssessmentLifecycleService.start_or_resume(
        consultation_id=consultation.id,
        actor_user_id=advisor.id,
        occurred_at=datetime(2026, 8, 24, 18, 5, 0),
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
            "professional_recommendation": "Original professional recommendation.",
        },
    )
    AssessmentLifecycleService.finalize(
        assessment_id=assessment.id,
        actor_user_id=advisor.id,
        finalized_at=datetime(2026, 8, 24, 18, 10, 0),
    )
    db.session.commit()
    return owner, advisor, car, ownership, consultation, assessment


def test_client_visible_correction_is_additive_evented_and_reported(app):
    with app.app_context():
        _owner, advisor, _car, _ownership, _consultation, assessment = _finalized(
            suffix="1"
        )
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
            reason="Clarify the recorded maintenance interval",
            visibility="client",
            client_text="The recommended review interval is 5,000 km, not 5,000 miles.",
            internal_text="Confirmed against advisor notes.",
            idempotency_key="correction-1",
            occurred_at=datetime(2026, 8, 24, 18, 20, 0),
        )
        db.session.commit()

        event = VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.corrected",
        ).one()

        assert addendum.assessment_id == assessment.id
        assert assessment.status == "finalized"
        assert assessment.is_finalized is True
        assert assessment.professional_recommendation == original_recommendation
        assert event.previous_state == "finalized"
        assert event.new_state == "finalized"
        assert event.progression_direction == "not_applicable"
        assert event.visibility == "client"
        assert event.correction_of_event_id == finalized_event.id
        assert event.data == {
            "addendum_id": addendum.id,
            "category": "clarification",
            "visibility": "client",
        }
        assert addendum.client_text not in (event.description or "")
        assert addendum.client_text not in str(event.data)
        assert addendum.internal_text not in (event.description or "")
        assert addendum.internal_text not in str(event.data)

        report = build_assessment_report(assessment=assessment)
        assert [item["id"] for item in report["addenda"]] == [addendum.id]
        assert report["addenda"][0]["statement"] == addendum.client_text


def test_internal_addendum_never_appears_on_owner_report(app):
    with app.app_context():
        _owner, advisor, _car, _ownership, _consultation, assessment = _finalized(
            suffix="2"
        )

        AssessmentLifecycleService.add_correction(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            category="additional_information",
            reason="Preserve restricted professional context",
            visibility="internal",
            internal_text="Advisor-only detail that must not reach the owner report.",
            idempotency_key="correction-internal-2",
        )
        db.session.commit()

        report = build_assessment_report(assessment=assessment)
        assert report["addenda"] == []


def test_owner_cannot_record_professional_correction(app):
    with app.app_context():
        owner, _advisor, _car, _ownership, _consultation, assessment = _finalized(
            suffix="3"
        )

        with pytest.raises(AssessmentLifecycleError, match="advisor authority"):
            AssessmentLifecycleService.add_correction(
                assessment_id=assessment.id,
                actor_user_id=owner.id,
                category="correction",
                reason="Owner mutation attempt",
                visibility="client",
                client_text="This must not be accepted.",
                idempotency_key="owner-correction-3",
            )

        assert VehicleAssessmentAddendum.query.count() == 0


def test_draft_assessment_cannot_receive_addendum(app):
    with app.app_context():
        _owner, advisor, _car, _ownership, consultation = _context(suffix="4")
        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
        )
        db.session.commit()

        with pytest.raises(AssessmentLifecycleError, match="finalized"):
            AssessmentLifecycleService.add_correction(
                assessment_id=assessment.id,
                actor_user_id=advisor.id,
                category="clarification",
                reason="Too early",
                visibility="client",
                client_text="Drafts cannot be supplemented.",
                idempotency_key="draft-correction-4",
            )

        assert VehicleAssessmentAddendum.query.count() == 0


def test_correction_is_idempotent_for_same_submission_key(app):
    with app.app_context():
        _owner, advisor, _car, _ownership, _consultation, assessment = _finalized(
            suffix="5"
        )
        kwargs = dict(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            category="correction",
            reason="Correct a unit label",
            visibility="client",
            client_text="The recorded quantity should be read in kilometres.",
            idempotency_key="same-correction-5",
        )

        first = AssessmentLifecycleService.add_correction(**kwargs)
        db.session.commit()
        second = AssessmentLifecycleService.add_correction(**kwargs)
        db.session.commit()

        assert second.id == first.id
        assert VehicleAssessmentAddendum.query.filter_by(
            assessment_id=assessment.id
        ).count() == 1
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.corrected",
        ).count() == 1


def test_addendum_and_event_roll_back_together(app, monkeypatch):
    with app.app_context():
        _owner, advisor, _car, _ownership, _consultation, assessment = _finalized(
            suffix="6"
        )

        def fail_event(**_kwargs):
            raise RuntimeError("simulated correction event failure")

        monkeypatch.setattr("services.assessment_lifecycle.emit_vehicle_event", fail_event)

        with pytest.raises(RuntimeError, match="correction event failure"):
            AssessmentLifecycleService.add_correction(
                assessment_id=assessment.id,
                actor_user_id=advisor.id,
                category="correction",
                reason="Rollback probe",
                visibility="client",
                client_text="This row must roll back with the event.",
                idempotency_key="rollback-correction-6",
            )

        db.session.rollback()
        assert VehicleAssessmentAddendum.query.filter_by(
            assessment_id=assessment.id
        ).count() == 0
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.corrected",
        ).count() == 0


def test_published_addendum_cannot_be_updated_or_deleted(app):
    with app.app_context():
        _owner, advisor, _car, _ownership, _consultation, assessment = _finalized(
            suffix="7"
        )
        addendum = AssessmentLifecycleService.add_correction(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            category="clarification",
            reason="Immutable row probe",
            visibility="client",
            client_text="Original additive statement.",
            idempotency_key="immutable-correction-7",
        )
        db.session.commit()

        addendum.client_text = "Silent rewrite attempt."
        with pytest.raises(ValueError, match="immutable"):
            db.session.commit()
        db.session.rollback()

        persisted = db.session.get(VehicleAssessmentAddendum, addendum.id)
        assert persisted.client_text == "Original additive statement."

        db.session.delete(persisted)
        with pytest.raises(ValueError, match="cannot be deleted"):
            db.session.commit()
        db.session.rollback()
        assert db.session.get(VehicleAssessmentAddendum, addendum.id) is not None


def test_legacy_finalized_assessment_gets_no_synthetic_finalization_event(app):
    with app.app_context():
        _owner, advisor, car, _ownership, consultation = _context(suffix="8")
        assessment = VehicleAssessment(
            consultation_id=consultation.id,
            car_id=car.id,
            advisor_id=advisor.id,
            finalized_by=advisor.id,
            status="finalized",
            is_finalized=True,
            finalized_at=datetime(2026, 8, 1, 12, 0, 0),
            vin=car.vin,
            mileage_at_assessment=31000,
            engine_status="healthy",
            transmission_status="healthy",
            suspension_status="healthy",
            electrical_status="healthy",
            cooling_status="healthy",
        )
        db.session.add(assessment)
        db.session.commit()

        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.finalized",
        ).count() == 0

        AssessmentLifecycleService.add_correction(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            category="additional_information",
            reason="New information for legacy professional record",
            visibility="client",
            client_text="Additional reviewed information is now available.",
            idempotency_key="legacy-correction-8",
        )
        db.session.commit()

        event = VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.corrected",
        ).one()
        assert event.correction_of_event_id is None
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.finalized",
        ).count() == 0


def test_b3_advisor_route_is_registered(app):
    assert "admin.admin_assessment_addenda" in app.view_functions
