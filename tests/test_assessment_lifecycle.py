from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import (
    Car,
    CarOwnership,
    Consultation,
    TreatmentPlan,
    User,
    VehicleAssessment,
    VehicleAssessmentRisk,
    VehicleAssessmentTreatmentOption,
    VehicleEvent,
)
from services.assessment_lifecycle import (
    AssessmentLifecycleError,
    AssessmentLifecycleService,
)
from services.event_emission import EventAuthorityError, emit_vehicle_event


def _create_user(
    *,
    name: str,
    email: str,
    phone: str,
    role: str = "user",
) -> User:
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


def _create_context(*, suffix: str = "1"):
    owner = _create_user(
        name=f"Assessment Owner {suffix}",
        email=f"assessment-owner-{suffix}@example.com",
        phone=f"0800300{suffix.zfill(4)}",
    )
    advisor = _create_user(
        name=f"Assessment Advisor {suffix}",
        email=f"assessment-advisor-{suffix}@example.com",
        phone=f"0800400{suffix.zfill(4)}",
        role="admin",
    )
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1NASSESSMENT{suffix.zfill(4)}",
        engine_number=f"M256-{suffix}",
        engine_type="M256",
        transmission_type="9G-TRONIC",
        current_mileage=24000,
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"AS-{suffix.zfill(3)}-LA",
        mileage_at_transfer=22000,
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
        scheduled_for=datetime(2026, 8, 24, 9, 0, 0),
        started_at=datetime(2026, 8, 24, 9, 1, 0),
    )
    db.session.add(consultation)
    db.session.commit()
    return owner, advisor, car, ownership, consultation


def _complete_required_statuses(assessment: VehicleAssessment) -> None:
    assessment.engine_status = "attention"
    assessment.transmission_status = "stable"
    assessment.suspension_status = "stable"
    assessment.electrical_status = "monitoring"
    assessment.cooling_status = "stable"


def test_start_creates_draft_snapshot_and_advisor_event(app):
    with app.app_context():
        _owner, advisor, car, _ownership, consultation = _create_context(suffix="1")
        occurred_at = datetime(2026, 8, 24, 10, 0, 0)

        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            occurred_at=occurred_at,
            source="tests.assessment_start",
        )

        event = VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.created",
        ).one()

        assert assessment.status == "draft"
        assert assessment.is_finalized is False
        assert assessment.car_id == car.id
        assert assessment.vin == car.vin
        assert assessment.mileage_at_assessment == 24000
        assert assessment.engine_number == car.engine_number
        assert assessment.engine_type == car.engine_type
        assert assessment.transmission == car.transmission_type
        assert event.actor_authority == "advisor"
        assert event.visibility == "advisor"
        assert event.previous_state is None
        assert event.new_state == "draft"
        assert event.progression_direction == "not_applicable"
        assert event.data == {"consultation_id": consultation.id}


def test_start_or_resume_existing_draft_does_not_duplicate_event(app):
    with app.app_context():
        _owner, advisor, _car, _ownership, consultation = _create_context(suffix="2")
        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 24, 10, 5, 0),
        )
        db.session.commit()

        resumed = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 24, 10, 6, 0),
        )

        assert resumed.id == assessment.id
        assert VehicleAssessment.query.filter_by(
            consultation_id=consultation.id
        ).count() == 1
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.created",
        ).count() == 1


def test_owner_cannot_start_professional_assessment(app):
    with app.app_context():
        owner, _advisor, _car, _ownership, consultation = _create_context(suffix="3")

        with pytest.raises(AssessmentLifecycleError, match="advisor authority"):
            AssessmentLifecycleService.start_or_resume(
                consultation_id=consultation.id,
                actor_user_id=owner.id,
            )

        assert VehicleAssessment.query.count() == 0
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment"
        ).count() == 0


def test_assessment_requires_active_consultation(app):
    with app.app_context():
        _owner, advisor, _car, _ownership, consultation = _create_context(suffix="4")
        consultation.status = "completed"
        db.session.commit()

        with pytest.raises(AssessmentLifecycleError, match="active Consultation"):
            AssessmentLifecycleService.start_or_resume(
                consultation_id=consultation.id,
                actor_user_id=advisor.id,
            )


def test_save_draft_replaces_only_submitted_groups_and_emits_no_event(app):
    with app.app_context():
        _owner, advisor, _car, _ownership, consultation = _create_context(suffix="5")
        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
        )
        db.session.add(
            VehicleAssessmentRisk(
                assessment_id=assessment.id,
                description="Existing risk",
                likely_cause="Existing cause",
                consequence_if_ignored="Existing consequence",
                urgency="monitoring",
            )
        )
        db.session.add(
            VehicleAssessmentTreatmentOption(
                assessment_id=assessment.id,
                option_code="A",
                title="Existing option",
                description="Existing treatment description",
            )
        )
        assessment.professional_recommendation = "Preserve this recommendation"
        db.session.commit()

        before_events = VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
        ).count()

        AssessmentLifecycleService.save_draft(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            scalar_updates={"engine_status": "stable"},
            risks=[
                {
                    "description": "Updated risk",
                    "likely_cause": "Reviewed cause",
                    "consequence_if_ignored": "Progression risk",
                    "urgency": "preventive",
                }
            ],
            treatment_options=None,
        )
        db.session.commit()

        risks = VehicleAssessmentRisk.query.filter_by(
            assessment_id=assessment.id
        ).all()
        treatments = VehicleAssessmentTreatmentOption.query.filter_by(
            assessment_id=assessment.id
        ).all()

        assert [risk.description for risk in risks] == ["Updated risk"]
        assert [option.title for option in treatments] == ["Existing option"]
        assert assessment.engine_status == "stable"
        assert assessment.professional_recommendation == "Preserve this recommendation"
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
        ).count() == before_events


def test_invalid_draft_collection_is_rejected_before_destructive_replacement(app):
    with app.app_context():
        _owner, advisor, _car, _ownership, consultation = _create_context(suffix="6")
        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
        )
        existing = VehicleAssessmentRisk(
            assessment_id=assessment.id,
            description="Do not delete",
            likely_cause="Known context",
            consequence_if_ignored="Known consequence",
            urgency="monitoring",
        )
        db.session.add(existing)
        db.session.commit()

        with pytest.raises(AssessmentLifecycleError, match="Invalid assessment risk urgency"):
            AssessmentLifecycleService.save_draft(
                assessment_id=assessment.id,
                actor_user_id=advisor.id,
                risks=[
                    {
                        "description": "Malformed replacement",
                        "likely_cause": "Cause",
                        "consequence_if_ignored": "Consequence",
                        "urgency": "panic",
                    }
                ],
            )

        assert VehicleAssessmentRisk.query.filter_by(
            assessment_id=assessment.id,
            description="Do not delete",
        ).count() == 1


def test_finalize_emits_client_event_and_creates_one_compatibility_plan(app):
    with app.app_context():
        _owner, advisor, _car, _ownership, consultation = _create_context(suffix="7")
        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 24, 11, 0, 0),
        )
        _complete_required_statuses(assessment)
        assessment.professional_recommendation = "Internal professional detail."
        finalized_at = datetime(2026, 8, 24, 11, 30, 0)

        AssessmentLifecycleService.finalize(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            finalized_at=finalized_at,
            source="tests.assessment_finalize",
        )
        db.session.commit()

        event = VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.finalized",
        ).one()
        plans = TreatmentPlan.query.filter_by(assessment_id=assessment.id).all()

        assert assessment.status == "finalized"
        assert assessment.is_finalized is True
        assert assessment.finalized_at == finalized_at
        assert assessment.finalized_by == advisor.id
        assert event.actor_authority == "advisor"
        assert event.visibility == "client"
        assert event.previous_state == "draft"
        assert event.new_state == "finalized"
        assert event.progression_direction == "not_applicable"
        assert event.data == {"consultation_id": consultation.id}
        assert "Internal professional detail" not in (event.description or "")
        assert "Internal professional detail" not in str(event.data)
        assert len(plans) == 1
        assert plans[0].status == "approved"


def test_finalize_is_fail_closed_and_does_not_repeat_side_effects(app):
    with app.app_context():
        _owner, advisor, _car, _ownership, consultation = _create_context(suffix="8")
        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
        )
        _complete_required_statuses(assessment)
        AssessmentLifecycleService.finalize(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            finalized_at=datetime(2026, 8, 24, 12, 0, 0),
        )
        db.session.commit()

        with pytest.raises(AssessmentLifecycleError, match="already finalized"):
            AssessmentLifecycleService.finalize(
                assessment_id=assessment.id,
                actor_user_id=advisor.id,
                finalized_at=datetime(2026, 8, 24, 12, 1, 0),
            )

        assert TreatmentPlan.query.filter_by(assessment_id=assessment.id).count() == 1
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.finalized",
        ).count() == 1


def test_finalization_rolls_back_when_event_emission_fails(app, monkeypatch):
    with app.app_context():
        _owner, advisor, _car, _ownership, consultation = _create_context(suffix="9")
        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
        )
        _complete_required_statuses(assessment)
        db.session.commit()

        def fail_event_emission(**_kwargs):
            raise RuntimeError("simulated assessment event failure")

        monkeypatch.setattr(
            "services.assessment_lifecycle.emit_vehicle_event",
            fail_event_emission,
        )

        with pytest.raises(RuntimeError, match="assessment event failure"):
            AssessmentLifecycleService.finalize(
                assessment_id=assessment.id,
                actor_user_id=advisor.id,
            )

        db.session.rollback()
        persisted = db.session.get(VehicleAssessment, assessment.id)

        assert persisted.status == "draft"
        assert persisted.is_finalized is False
        assert persisted.finalized_at is None
        assert persisted.finalized_by is None
        assert TreatmentPlan.query.filter_by(assessment_id=assessment.id).count() == 0
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.finalized",
        ).count() == 0


def test_cross_vehicle_scope_mismatch_fails_closed(app):
    with app.app_context():
        _owner, advisor, car, _ownership, consultation = _create_context(suffix="10")
        other_owner, _other_advisor, other_car, _other_ownership, _other_consultation = (
            _create_context(suffix="11")
        )
        assert other_owner.id != consultation.client_id

        assessment = VehicleAssessment(
            consultation_id=consultation.id,
            car_id=other_car.id,
            advisor_id=advisor.id,
            vin=other_car.vin,
            mileage_at_assessment=other_car.current_mileage or 0,
            status="draft",
            is_finalized=False,
        )
        db.session.add(assessment)
        db.session.commit()

        with pytest.raises(AssessmentLifecycleError, match="different vehicle"):
            AssessmentLifecycleService.save_draft(
                assessment_id=assessment.id,
                actor_user_id=advisor.id,
                scalar_updates={"engine_status": "stable"},
            )

        assert assessment.engine_status is None
        assert assessment.car_id != car.id


def test_owner_cannot_emit_professional_assessment_event(app):
    with app.app_context():
        owner, advisor, _car, _ownership, consultation = _create_context(suffix="12")
        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 24, 13, 0, 0),
        )

        with pytest.raises(EventAuthorityError, match="advisor authority"):
            emit_vehicle_event(
                car_id=assessment.car_id,
                event_type="assessment.finalized",
                subject_type="vehicle_assessment",
                subject_id=assessment.id,
                actor_type="user",
                actor_user_id=owner.id,
                visibility="client",
                source="tests.assessment_authority",
                occurred_at=datetime(2026, 8, 24, 13, 30, 0),
                title="Vehicle Assessment finalized",
                progression_direction="not_applicable",
                idempotency_key="owner-cannot-finalize-assessment",
                previous_state="draft",
                new_state="finalized",
            )
