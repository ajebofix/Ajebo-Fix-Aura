from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import Car, CarOwnership, TreatmentPlan, User
from treatment.models import TreatmentOutcome


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Outcome {role} {suffix}",
        email=f"outcome-{role}-{suffix}@example.com",
        phone_number=f"+2348982{suffix:06d}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _outcome_fixture(*, suffix: int = 1) -> TreatmentOutcome:
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
            plate_number=f"OT-{suffix:03d}-LA",
            mileage_at_transfer=24000,
            is_active=True,
        )
    )
    plan = TreatmentPlan(
        car_id=car.id,
        advisor_id=advisor.id,
        title="Vehicle Treatment Plan",
        client_summary="Authorized professional care pathway.",
        status="monitoring",
    )
    db.session.add(plan)
    db.session.flush()
    outcome = TreatmentOutcome(
        treatment_plan_id=plan.id,
        car_id=car.id,
        recorded_by_user_id=advisor.id,
        recording_key=f"outcome-{suffix}",
        progression_direction="stable",
        summary="No material change observed during follow-up review.",
        visibility="client",
        provenance_kind="professional_observation",
        provenance_data={"source": "advisor_follow_up"},
        observed_at=datetime(2026, 8, 29, 19, 0, 0),
    )
    db.session.add(outcome)
    db.session.commit()
    return outcome


def test_published_treatment_outcome_cannot_be_updated(app):
    with app.app_context():
        outcome = _outcome_fixture(suffix=1)
        outcome.summary = "Attempted rewrite of historical outcome."

        with pytest.raises(ValueError, match="append-only"):
            db.session.commit()
        db.session.rollback()

        persisted = db.session.get(TreatmentOutcome, outcome.id)
        assert persisted is not None
        assert persisted.summary == "No material change observed during follow-up review."


def test_published_treatment_outcome_cannot_be_deleted(app):
    with app.app_context():
        outcome = _outcome_fixture(suffix=2)
        outcome_id = outcome.id
        db.session.delete(outcome)

        with pytest.raises(ValueError, match="cannot be deleted"):
            db.session.commit()
        db.session.rollback()

        assert db.session.get(TreatmentOutcome, outcome_id) is not None
