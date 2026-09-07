from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from flask_login import login_user

from extensions import db
from mileage.models import MileageObservation
from models import Car, CarOwnership, User
from services.mileage_observations import (
    MileageObservationError,
    MileageObservationService,
)


def _user(*, suffix: str, role: str = "user") -> User:
    user = User(
        name=f"Mileage {role.title()} {suffix}",
        email=f"mileage-{role}-{suffix}@example.com",
        phone_number=f"0811555{suffix.zfill(4)}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _owned_car(*, suffix: str, current_mileage: int = 64000):
    owner = _user(suffix=suffix)
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin=f"W1NMILEAGE{suffix.zfill(7)}",
        current_mileage=current_mileage,
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"MO-{suffix.zfill(3)}-LA",
        mileage_at_transfer=current_mileage,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return owner, car, ownership


def test_legacy_current_mileage_is_not_given_fake_provenance(app):
    with app.app_context():
        _owner, car, _ownership = _owned_car(suffix="1")

        snapshot = MileageObservationService.snapshot(car)

        assert snapshot.odometer_km == 64000
        assert snapshot.provenance_available is False
        assert snapshot.freshness_status == "unknown"
        assert snapshot.needs_update is True
        assert MileageObservation.query.filter_by(car_id=car.id).count() == 0


def test_advisor_observation_advances_current_odometer_and_records_provenance(app):
    with app.app_context():
        _owner, car, ownership = _owned_car(suffix="2")
        advisor = _user(suffix="2", role="admin")
        db.session.commit()

        observed_at = datetime.utcnow() - timedelta(hours=1)
        observation = MileageObservationService.record_advisor_observation(
            car=car,
            odometer_km=64500,
            advisor_user_id=advisor.id,
            ownership=ownership,
            observed_at=observed_at,
            evidence_reference="dashboard-photo-001",
            note="Cluster read directly during advisor review.",
        )

        db.session.refresh(car)
        snapshot = MileageObservationService.snapshot(car)

        assert car.current_mileage == 64500
        assert observation.source == "advisor_observation"
        assert observation.verification_status == "advisor_verified"
        assert observation.is_historical is False
        assert observation.evidence_reference == "dashboard-photo-001"
        assert snapshot.odometer_km == 64500
        assert snapshot.provenance_available is True
        assert snapshot.freshness_status == "fresh"
        assert snapshot.source_label == "Advisor-observed odometer"
        assert snapshot.verification_label == "Advisor verified"


def test_current_observation_cannot_move_odometer_backwards(app):
    with app.app_context():
        _owner, car, ownership = _owned_car(suffix="3")
        advisor = _user(suffix="3", role="admin")
        db.session.commit()

        with pytest.raises(MileageObservationError, match="cannot move"):
            MileageObservationService.record_advisor_observation(
                car=car,
                odometer_km=63000,
                advisor_user_id=advisor.id,
                ownership=ownership,
            )

        db.session.refresh(car)
        assert car.current_mileage == 64000
        assert MileageObservation.query.filter_by(car_id=car.id).count() == 0


def test_equal_reading_can_establish_freshness_without_changing_odometer(app):
    with app.app_context():
        _owner, car, ownership = _owned_car(suffix="4")
        advisor = _user(suffix="4", role="admin")
        db.session.commit()

        MileageObservationService.record_advisor_observation(
            car=car,
            odometer_km=64000,
            advisor_user_id=advisor.id,
            ownership=ownership,
        )

        db.session.refresh(car)
        snapshot = MileageObservationService.snapshot(car)

        assert car.current_mileage == 64000
        assert snapshot.provenance_available is True
        assert snapshot.freshness_status == "fresh"


def test_historical_service_snapshot_never_replaces_current_odometer(app):
    with app.app_context():
        owner, car, ownership = _owned_car(suffix="5")

        observation = MileageObservationService.record_service_snapshot(
            car=car,
            ownership=ownership,
            odometer_km=34000,
            service_date="2025-11-15",
            performed_by=owner.id,
            event_metadata={
                "record_mode": "historical",
                "verification_status": "unverified",
            },
            source_reference="service-fingerprint-001",
            entry_source="admin_historical",
        )

        db.session.refresh(car)
        snapshot = MileageObservationService.snapshot(car)

        assert observation.is_historical is True
        assert observation.odometer_km == 34000
        assert observation.verification_status == "unverified"
        assert car.current_mileage == 64000
        assert snapshot.odometer_km == 64000
        assert snapshot.provenance_available is False


def test_old_observation_is_marked_stale(app):
    with app.app_context():
        _owner, car, ownership = _owned_car(suffix="6")
        advisor = _user(suffix="6", role="admin")
        db.session.commit()

        MileageObservationService.record_advisor_observation(
            car=car,
            odometer_km=64000,
            advisor_user_id=advisor.id,
            ownership=ownership,
            observed_at=datetime.utcnow() - timedelta(days=120),
        )

        snapshot = MileageObservationService.snapshot(car)

        assert snapshot.freshness_status == "stale"
        assert snapshot.freshness_label == "Stale — update required"
        assert snapshot.needs_update is True


def test_advisor_odometer_route_is_registered_and_saves_observation(app, monkeypatch):
    with app.test_request_context(
        "/admin/cars/1/odometer",
        method="POST",
        data={
            "odometer_km": "64250",
            "note": "Verified from cluster.",
        },
    ):
        _owner, car, ownership = _owned_car(suffix="7")
        advisor = _user(suffix="7", role="admin")
        db.session.commit()
        login_user(advisor)

        monkeypatch.setattr(
            "services.mileage_routes.CareSignalService.evaluate",
            lambda *_args, **_kwargs: None,
        )

        assert app.view_functions["admin.update_odometer"].__module__ == (
            "services.mileage_routes"
        )

        response = app.view_functions["admin.update_odometer"](car.id)

        assert response.status_code == 302
        assert response.location.endswith(f"/admin/cars/{car.id}")
        db.session.refresh(car)
        observation = MileageObservation.query.filter_by(car_id=car.id).one()
        assert observation.odometer_km == 64250
        assert observation.ownership_id == ownership.id
        assert observation.recorded_by_user_id == advisor.id
        assert car.current_mileage == 64250
