from __future__ import annotations

from datetime import datetime

from cars.routes import create_service_event
from extensions import db
from mileage.backfill import backfill_legacy_service_mileage_observations
from mileage.models import MileageObservation
from models import Car, CarOwnership, User, VehicleEvent
from services.mileage_observations import MileageObservationService


def _user(*, suffix: str, role: str) -> User:
    user = User(
        name=f"Mileage Backfill {role.title()} {suffix}",
        email=f"mileage-backfill-{role}-{suffix}@example.com",
        phone_number=f"0822666{suffix.zfill(4)}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _fixture_vehicle(*, suffix: str = "1"):
    owner = _user(suffix=f"1{suffix}", role="user")
    advisor = _user(suffix=f"2{suffix}", role="admin")
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin=f"W1NBACKFILL{suffix.zfill(6)}",
        current_mileage=64000,
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"BF-{suffix.zfill(3)}-LA",
        mileage_at_transfer=64000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return owner, advisor, car, ownership


def _legacy_service(
    *,
    car: Car,
    ownership: CarOwnership,
    advisor: User,
    mileage: int,
    service_date: str,
    metadata: dict | None = None,
) -> VehicleEvent:
    create_service_event(
        car=car,
        ownership=ownership,
        service_type="Routine service",
        mileage=mileage,
        description=f"Legacy service at {mileage} km",
        service_date=service_date,
        performed_by=advisor.id,
        source="admin",
    )
    event = VehicleEvent.query.filter_by(
        car_id=car.id,
        event_type="service",
        mileage=mileage,
    ).one()
    if metadata is not None:
        event.data = metadata
        db.session.commit()
    return event


def _run_backfill():
    with db.engine.begin() as bind:
        return backfill_legacy_service_mileage_observations(bind)


def test_backfill_restores_legacy_service_mileage_without_moving_current_odometer(app):
    with app.app_context():
        _owner, advisor, car, ownership = _fixture_vehicle(suffix="1")

        MileageObservationService.record_advisor_observation(
            car=car,
            odometer_km=64000,
            advisor_user_id=advisor.id,
            ownership=ownership,
            observed_at=datetime(2026, 9, 7, 18, 3),
            note="Production-style current observation.",
        )

        event_10 = _legacy_service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            mileage=10000,
            service_date="2025-01-15",
        )
        event_22 = _legacy_service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            mileage=22000,
            service_date="2025-06-15",
        )
        event_34 = _legacy_service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            mileage=34000,
            service_date="2025-11-15",
            metadata={
                "record_mode": "historical",
                "verification_status": "unverified",
                "information_source": "client_provided",
            },
        )

        # The 34,000 km record represents the post-PR #119 service snapshot that
        # already exists. The backfill must not duplicate it.
        MileageObservationService.record(
            car=car,
            odometer_km=34000,
            source="service_record",
            verification_status="unverified",
            observed_at=datetime(2025, 11, 15),
            recorded_by_user_id=advisor.id,
            ownership_id=ownership.id,
            is_historical=True,
            source_reference=event_34.fingerprint,
            note="Main-odometer snapshot attached to a service record.",
        )

        result = _run_backfill()
        db.session.expire_all()

        assert result.scanned == 3
        assert result.inserted == 2
        assert result.skipped_existing == 1
        assert result.skipped_invalid == 0

        refreshed_car = db.session.get(Car, car.id)
        assert refreshed_car.current_mileage == 64000

        history = MileageObservationService.history(car.id, limit=10)
        assert [item.odometer_km for item in history] == [64000, 34000, 22000, 10000]
        assert [item.is_historical for item in history] == [False, True, True, True]

        by_mileage = {item.odometer_km: item for item in history}
        assert by_mileage[64000].source == "advisor_observation"
        assert by_mileage[64000].verification_status == "advisor_verified"
        assert by_mileage[34000].verification_status == "unverified"
        assert by_mileage[22000].verification_status == "legacy_unknown"
        assert by_mileage[10000].verification_status == "legacy_unknown"
        assert by_mileage[22000].source_reference == event_22.fingerprint
        assert by_mileage[10000].source_reference == event_10.fingerprint

        # Re-running the backfill must be a no-op.
        second = _run_backfill()
        db.session.expire_all()
        assert second.inserted == 0
        assert second.skipped_existing == 3
        assert MileageObservation.query.filter_by(car_id=car.id).count() == 4
        assert db.session.get(Car, car.id).current_mileage == 64000


def test_backfill_preserves_explicit_historical_verification_metadata(app):
    with app.app_context():
        _owner, advisor, car, ownership = _fixture_vehicle(suffix="2")

        event = _legacy_service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            mileage=42000,
            service_date="2025-08-10",
            metadata={
                "record_mode": "historical",
                "verification_status": "document_reviewed",
                "information_source": "invoice_receipt",
            },
        )

        result = _run_backfill()
        db.session.expire_all()

        assert result.inserted == 1
        observation = MileageObservation.query.filter_by(
            car_id=car.id,
            source="service_record",
            source_reference=event.fingerprint,
        ).one()
        assert observation.odometer_km == 42000
        assert observation.is_historical is True
        assert observation.verification_status == "document_reviewed"
        assert db.session.get(Car, car.id).current_mileage == 64000
