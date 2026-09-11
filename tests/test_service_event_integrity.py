from __future__ import annotations

from datetime import datetime

from extensions import db
from models import Car, CarOwnership, User, VehicleEvent
from services.service_history_route_cutover import _record_service_with_monitoring


def _owner_and_car(*, suffix: str = "integrity"):
    owner = User(
        name="Service Event Integrity Owner",
        email=f"service-event-{suffix}@example.com",
        phone_number="08007770001",
        role="user",
        is_active=True,
    )
    owner.set_password("Password123")
    db.session.add(owner)
    db.session.flush()

    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin="W1NSRVCINT000001",
        current_mileage=64000,
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number="INT-001-LA",
        mileage_at_transfer=64000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return owner, car, ownership


def test_service_cutover_writes_integrity_complete_event_envelope(app):
    with app.app_context():
        owner, car, ownership = _owner_and_car()

        refreshed = _record_service_with_monitoring(
            car=car,
            ownership=ownership,
            service_type="Routine service",
            mileage=34000,
            description="Synthetic integrity regression record.",
            service_date="2025-11-15",
            performed_by=owner.id,
            source="client",
            event_metadata=None,
        )

        assert refreshed is True

        event = VehicleEvent.query.filter_by(
            car_id=car.id,
            ownership_id=ownership.id,
            event_type="service",
        ).one()

        assert event.schema_version == 1
        assert event.occurred_at == datetime(2025, 11, 15)
        assert event.recorded_at == datetime(2025, 11, 15)
        assert event.subject_type == "service_record"
        assert event.subject_id == event.id
        assert event.actor_type == "user"
        assert event.actor_user_id == owner.id
        assert event.visibility == "internal"
        assert event.progression_direction == "not_applicable"

        required = (
            event.car_id,
            event.event_type,
            event.subject_type,
            event.subject_id,
            event.occurred_at,
            event.schema_version,
            event.source,
            event.progression_direction,
            event.fingerprint,
        )
        assert all(value is not None for value in required)
