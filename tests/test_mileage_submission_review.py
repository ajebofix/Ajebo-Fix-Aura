from __future__ import annotations

import pytest
from flask_login import login_user

from extensions import db
from mileage.models import MileageObservation
from models import Car, CarDriver, CarOwnership, User
from services.mileage_observations import (
    MileageObservationError,
    MileageObservationService,
)


def _user(*, suffix: str, role: str = "user") -> User:
    role_digit = {
        "user": "1",
        "admin": "9",
        "advisor": "8",
        "driver": "7",
    }.get(role, "6")
    user = User(
        name=f"Mileage Review {role.title()} {suffix}",
        email=f"mileage-review-{role}-{suffix}@example.com",
        phone_number=f"08126{role_digit}{suffix.zfill(5)}",
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
        vin=f"W1NREVIEW{suffix.zfill(8)}",
        current_mileage=current_mileage,
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"MR-{suffix.zfill(3)}-LA",
        mileage_at_transfer=current_mileage,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return owner, car, ownership


def _establish_advisor_baseline(car, ownership, *, suffix: str):
    advisor = _user(suffix=suffix, role="admin")
    db.session.commit()
    MileageObservationService.record_advisor_observation(
        car=car,
        odometer_km=car.current_mileage,
        advisor_user_id=advisor.id,
        ownership=ownership,
    )
    return advisor


def test_client_report_is_pending_and_does_not_advance_current_odometer(app):
    with app.app_context():
        owner, car, ownership = _owned_car(suffix="1")
        _establish_advisor_baseline(car, ownership, suffix="11")

        report = MileageObservationService.record_reported_observation(
            car=car,
            odometer_km=64500,
            actor_user_id=owner.id,
            actor_type="client",
            ownership=ownership,
            note="Dashboard reading supplied by owner.",
        )

        db.session.refresh(car)
        snapshot = MileageObservationService.snapshot(car)

        assert car.current_mileage == 64000
        assert report.source == "client_report"
        assert report.verification_status == "client_reported"
        assert report.review_status == "pending"
        assert report.is_historical is False
        assert snapshot.odometer_km == 64000
        assert snapshot.verification_status == "advisor_verified"


def test_driver_report_is_pending_and_does_not_advance_current_odometer(app):
    with app.app_context():
        _owner, car, ownership = _owned_car(suffix="2")
        driver = _user(suffix="2", role="driver")
        db.session.add(CarDriver(car_id=car.id, user_id=driver.id, is_active=True))
        db.session.commit()
        _establish_advisor_baseline(car, ownership, suffix="22")

        report = MileageObservationService.record_reported_observation(
            car=car,
            odometer_km=64620,
            actor_user_id=driver.id,
            actor_type="driver",
            ownership=ownership,
        )

        db.session.refresh(car)
        assert car.current_mileage == 64000
        assert report.source == "driver_report"
        assert report.verification_status == "driver_reported"
        assert report.review_status == "pending"


def test_actor_cannot_stack_multiple_pending_reports_for_same_vehicle(app):
    with app.app_context():
        owner, car, ownership = _owned_car(suffix="3")

        MileageObservationService.record_reported_observation(
            car=car,
            odometer_km=64100,
            actor_user_id=owner.id,
            actor_type="client",
            ownership=ownership,
        )

        with pytest.raises(MileageObservationError, match="already awaiting"):
            MileageObservationService.record_reported_observation(
                car=car,
                odometer_km=64200,
                actor_user_id=owner.id,
                actor_type="client",
                ownership=ownership,
            )

        assert MileageObservation.query.filter_by(
            car_id=car.id,
            review_status="pending",
        ).count() == 1


def test_advisor_acceptance_advances_odometer_and_preserves_report_source(app):
    with app.app_context():
        owner, car, ownership = _owned_car(suffix="4")
        advisor = _establish_advisor_baseline(car, ownership, suffix="44")

        report = MileageObservationService.record_reported_observation(
            car=car,
            odometer_km=64750,
            actor_user_id=owner.id,
            actor_type="client",
            ownership=ownership,
        )

        accepted = MileageObservationService.accept_report(
            observation=report,
            advisor_user_id=advisor.id,
            review_note="Reading accepted after advisor review.",
        )

        db.session.refresh(car)
        snapshot = MileageObservationService.snapshot(car)

        assert car.current_mileage == 64750
        assert accepted.source == "client_report"
        assert accepted.review_status == "accepted"
        assert accepted.verification_status == "advisor_verified"
        assert accepted.reviewed_by_user_id == advisor.id
        assert accepted.reviewed_at is not None
        assert snapshot.odometer_km == 64750
        assert snapshot.source == "client_report"
        assert snapshot.verification_status == "advisor_verified"


def test_advisor_rejection_preserves_report_but_never_changes_current_mileage(app):
    with app.app_context():
        owner, car, ownership = _owned_car(suffix="5")
        advisor = _establish_advisor_baseline(car, ownership, suffix="55")

        report = MileageObservationService.record_reported_observation(
            car=car,
            odometer_km=64900,
            actor_user_id=owner.id,
            actor_type="client",
            ownership=ownership,
        )

        rejected = MileageObservationService.reject_report(
            observation=report,
            advisor_user_id=advisor.id,
            review_note="Reading could not be confirmed.",
        )

        db.session.refresh(car)
        snapshot = MileageObservationService.snapshot(car)

        assert car.current_mileage == 64000
        assert rejected.review_status == "rejected"
        assert rejected.verification_status == "client_reported"
        assert rejected.reviewed_by_user_id == advisor.id
        assert snapshot.odometer_km == 64000
        assert MileageObservation.query.filter_by(id=rejected.id).one() is rejected


def test_report_below_latest_recorded_odometer_is_rejected(app):
    with app.app_context():
        owner, car, ownership = _owned_car(suffix="6")

        with pytest.raises(MileageObservationError, match="cannot move"):
            MileageObservationService.record_reported_observation(
                car=car,
                odometer_km=63000,
                actor_user_id=owner.id,
                actor_type="client",
                ownership=ownership,
            )

        assert MileageObservation.query.filter_by(car_id=car.id).count() == 0
        assert car.current_mileage == 64000


def test_owner_submission_route_records_pending_report(app):
    with app.test_request_context(
        "/mileage/cars/1/report",
        method="POST",
        data={
            "odometer_km": "64320",
            "note": "Owner dashboard reading.",
        },
    ):
        owner, car, _ownership = _owned_car(suffix="7")
        login_user(owner)

        response = app.view_functions["mileage.report_odometer"](car.id)

        assert response.status_code == 302
        assert response.location.endswith(f"/cars/{car.id}")
        report = MileageObservation.query.filter_by(
            car_id=car.id,
            source="client_report",
        ).one()
        assert report.review_status == "pending"
        db.session.refresh(car)
        assert car.current_mileage == 64000


def test_driver_submission_route_records_pending_report(app):
    with app.test_request_context(
        "/mileage/cars/1/report",
        method="POST",
        data={"odometer_km": "64410"},
    ):
        _owner, car, _ownership = _owned_car(suffix="8")
        driver = _user(suffix="8", role="driver")
        db.session.add(CarDriver(car_id=car.id, user_id=driver.id, is_active=True))
        db.session.commit()
        login_user(driver)

        response = app.view_functions["mileage.report_odometer"](car.id)

        assert response.status_code == 302
        assert response.location.endswith(f"/driver/cars/{car.id}")
        report = MileageObservation.query.filter_by(
            car_id=car.id,
            source="driver_report",
        ).one()
        assert report.review_status == "pending"
        db.session.refresh(car)
        assert car.current_mileage == 64000


def test_advisor_accept_route_is_registered_and_refreshes_monitoring(app, monkeypatch):
    with app.test_request_context(
        "/admin/cars/1/odometer-reports/1/accept",
        method="POST",
    ):
        owner, car, ownership = _owned_car(suffix="9")
        advisor = _establish_advisor_baseline(car, ownership, suffix="99")
        report = MileageObservationService.record_reported_observation(
            car=car,
            odometer_km=65000,
            actor_user_id=owner.id,
            actor_type="client",
            ownership=ownership,
        )
        login_user(advisor)

        calls = []
        monkeypatch.setattr(
            "services.mileage_routes.CareSignalService.evaluate",
            lambda car_id, trigger: calls.append((car_id, trigger)),
        )

        response = app.view_functions["admin.accept_odometer_report"](
            car.id,
            report.id,
        )

        assert response.status_code == 302
        db.session.refresh(car)
        db.session.refresh(report)
        assert car.current_mileage == 65000
        assert report.review_status == "accepted"
        assert calls == [(car.id, "mileage_observed")]
