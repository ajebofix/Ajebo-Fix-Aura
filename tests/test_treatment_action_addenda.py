from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import Car, TreatmentPlan, User
from services.treatment_action_addenda import (
    TreatmentActionAddendumError,
    add_treatment_action_addendum,
)
from treatment.models import TreatmentAction, TreatmentActionAddendum


def _admin(*, suffix: int) -> User:
    user = User(
        name=f"Addendum Advisor {suffix}",
        email=f"addendum-advisor-{suffix}@example.com",
        phone_number=f"+23480991{suffix:05d}",
        role="admin",
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _completed_action(*, advisor: User, suffix: int) -> TreatmentAction:
    car = Car(
        brand="Mercedes-Benz",
        model="GL 450",
        year=2014,
        vin=f"4JGADDEND{suffix:08d}",
        current_mileage=120000,
    )
    db.session.add(car)
    db.session.flush()

    plan = TreatmentPlan(
        car_id=car.id,
        advisor_id=advisor.id,
        title="Historical service episode",
        client_summary="Historical completed work.",
        internal_instructions="Preserved historical record.",
        status="completed",
        record_origin="historical_document",
        created_at=datetime(2026, 8, 12, 14, 0, 0),
        updated_at=datetime(2026, 8, 12, 14, 0, 0),
    )
    db.session.add(plan)
    db.session.flush()

    action = TreatmentAction(
        treatment_plan_id=plan.id,
        car_id=car.id,
        created_by_user_id=advisor.id,
        creation_key=f"stud-plate-{suffix}",
        title="Stud-plate reconstruction",
        client_summary="Broken rear mounting stud/plate repaired.",
        internal_instructions="Historical completed-work record.",
        status="completed",
        visibility="advisor",
        completed_at=datetime(2026, 8, 12, 14, 15, 0),
        created_at=datetime(2026, 8, 12, 14, 0, 0),
        updated_at=datetime(2026, 8, 12, 14, 15, 0),
    )
    db.session.add(action)
    db.session.commit()
    return action


def test_treatment_action_addendum_is_append_only_and_does_not_rewrite_action(app):
    with app.app_context():
        advisor = _admin(suffix=1)
        action = _completed_action(advisor=advisor, suffix=1)
        original_title = action.title
        original_summary = action.client_summary
        original_completed_at = action.completed_at

        addendum = add_treatment_action_addendum(
            treatment_action_id=action.id,
            actor_user_id=advisor.id,
            category="additional_information",
            reason="Later historical detail established by the advisor",
            visibility="advisor",
            detail_text=(
                "The panel beater reconstructed and welded the broken left-rear "
                "mounting stud/plate."
            ),
            idempotency_key="stud-plate-detail-1",
            occurred_at=datetime(2026, 9, 24, 8, 45, 0),
        )
        db.session.commit()

        db.session.refresh(action)
        assert action.title == original_title
        assert action.client_summary == original_summary
        assert action.completed_at == original_completed_at
        assert TreatmentAction.query.filter_by(
            treatment_plan_id=action.treatment_plan_id
        ).count() == 1

        assert addendum.treatment_action_id == action.id
        assert "panel beater" in addendum.detail_text
        assert addendum.visibility == "advisor"

        replay = add_treatment_action_addendum(
            treatment_action_id=action.id,
            actor_user_id=advisor.id,
            category="additional_information",
            reason="Later historical detail established by the advisor",
            visibility="advisor",
            detail_text=(
                "The panel beater reconstructed and welded the broken left-rear "
                "mounting stud/plate."
            ),
            idempotency_key="stud-plate-detail-1",
            occurred_at=datetime(2026, 9, 24, 8, 45, 0),
        )
        db.session.commit()
        assert replay.id == addendum.id
        assert TreatmentActionAddendum.query.filter_by(
            treatment_action_id=action.id
        ).count() == 1

        addendum.detail_text = "Attempted rewrite"
        with pytest.raises(ValueError, match="immutable"):
            db.session.commit()
        db.session.rollback()

        preserved = db.session.get(TreatmentActionAddendum, addendum.id)
        assert "panel beater" in preserved.detail_text

        db.session.delete(preserved)
        with pytest.raises(ValueError, match="cannot be deleted"):
            db.session.commit()
        db.session.rollback()


def test_addendum_requires_completed_action_and_idempotency_conflicts_fail_closed(app):
    with app.app_context():
        advisor = _admin(suffix=2)
        action = _completed_action(advisor=advisor, suffix=2)

        add_treatment_action_addendum(
            treatment_action_id=action.id,
            actor_user_id=advisor.id,
            category="clarification",
            reason="Clarify historical method",
            visibility="advisor",
            detail_text="The repair involved welding and reconstruction.",
            idempotency_key="clarify-2",
        )
        db.session.commit()

        with pytest.raises(
            TreatmentActionAddendumError,
            match="different content",
        ):
            add_treatment_action_addendum(
                treatment_action_id=action.id,
                actor_user_id=advisor.id,
                category="clarification",
                reason="Clarify historical method",
                visibility="advisor",
                detail_text="Different later detail.",
                idempotency_key="clarify-2",
            )

        action.status = "in_progress"
        db.session.commit()
        with pytest.raises(
            TreatmentActionAddendumError,
            match="completed work",
        ):
            add_treatment_action_addendum(
                treatment_action_id=action.id,
                actor_user_id=advisor.id,
                category="additional_information",
                reason="Not allowed yet",
                visibility="advisor",
                detail_text="This must not be added before completion.",
                idempotency_key="blocked-in-progress",
            )
