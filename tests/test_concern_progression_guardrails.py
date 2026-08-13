from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import Car, CarFault, CarOwnership, User, VehicleEvent
from services.concern_progression import get_client_safe_reported_concern_progression


@pytest.mark.parametrize(
    ("direction", "description"),
    [
        ("improving", "The client says the observation feels much better now."),
        ("deteriorating", "The client says the observation feels much worse now."),
    ],
)
def test_progression_does_not_promote_unvalidated_strong_direction(
    app,
    direction: str,
    description: str,
):
    """Improving/deteriorating need an approved evidence rule, not free text.

    The canonical vocabulary reserves both values, but Wave 1.2's first-domain
    writer does not yet authorize either direction. The reconstruction service
    must therefore abstain even if a malformed/manual row carries the stronger
    label or the concern description sounds positive/negative.
    """

    with app.app_context():
        suffix = "1" if direction == "improving" else "2"
        owner = User(
            name=f"Guardrail Owner {suffix}",
            email=f"progression-guardrail-{suffix}@example.com",
            phone=f"0800400000{suffix}",
            role="user",
            is_active=True,
            email_verified_at=datetime(2026, 8, 13, 7, 0, 0),
        )
        owner.set_password("Password123")
        db.session.add(owner)
        db.session.flush()

        car = Car(
            brand="Mercedes-Benz",
            model="GLC 300 4MATIC",
            year=2024,
            vin=f"W1NGUARD00000000{suffix}",
            current_mileage=21000,
        )
        db.session.add(car)
        db.session.flush()
        ownership = CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"GD-{suffix}01-LA",
            mileage_at_transfer=21000,
            is_active=True,
        )
        db.session.add(ownership)
        db.session.commit()

        concern = CarFault(
            car_id=car.id,
            title="Guardrail observation",
            category="observation",
            description=description,
            status="monitoring",
            reported_by=owner.id,
            source="client",
            reported_at=datetime(2026, 8, 13, 8, 0, 0),
        )
        db.session.add(concern)
        db.session.commit()

        manual_event = VehicleEvent(
            car_id=car.id,
            ownership_id=ownership.id,
            event_type="concern.monitoring_started",
            severity="low",
            event_date=datetime(2026, 8, 13).date(),
            title=f"Unvalidated {direction} direction",
            description=None,
            mileage=None,
            source="tests.guardrail_fixture",
            data={},
            fingerprint=f"guardrail-{direction}-{concern.id}",
            created_by=owner.id,
            schema_version=1,
            occurred_at=datetime(2026, 8, 13, 9, 0, 0),
            recorded_at=datetime(2026, 8, 13, 9, 0, 0),
            subject_type="reported_concern",
            subject_id=concern.id,
            actor_type="user",
            actor_user_id=owner.id,
            actor_authority="owner",
            visibility="client",
            previous_state="reported",
            new_state="monitoring",
            progression_direction=direction,
            evidence_refs=[{"type": "reported_concern", "id": concern.id}],
        )
        db.session.add(manual_event)
        db.session.commit()

        summary = get_client_safe_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )

        assert summary.timeline[-1].event_id == manual_event.id
        assert summary.timeline[-1].progression_direction == direction
        assert summary.progression == "insufficient_evidence"
        assert summary.recurrence is None
        assert direction not in summary.explanation.lower()
        assert description not in summary.to_dict().__repr__()
