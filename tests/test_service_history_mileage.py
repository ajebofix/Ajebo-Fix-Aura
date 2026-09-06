from __future__ import annotations

from extensions import db
from models import Car, CarOwnership, User, VehicleEvent, VehicleHealthAlert
from cars.routes import create_service_event
from services.service_history_route_cutover import _record_service_with_monitoring
from services.vehicle_intelligence import (
    calculate_vehicle_health,
    resolve_current_mileage,
)


def _create_user(*, suffix: str) -> User:
    user = User(
        name=f"Service History Owner {suffix}",
        email=f"service-history-{suffix}@example.com",
        phone_number=f"0800200{suffix.zfill(4)}",
        role="user",
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _create_owned_car(*, suffix: str, current_mileage: int = 64000):
    owner = _create_user(suffix=suffix)
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin=f"W1NSERVHIST{suffix.zfill(6)}",
        current_mileage=current_mileage,
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"SH-{suffix.zfill(3)}-LA",
        mileage_at_transfer=current_mileage,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return owner, car, ownership


def _record_service(
    *,
    owner: User,
    car: Car,
    ownership: CarOwnership,
    mileage: int,
    service_date: str,
    service_type: str = "Routine service",
):
    create_service_event(
        car=car,
        ownership=ownership,
        service_type=service_type,
        mileage=mileage,
        description="Synthetic service-history regression record.",
        service_date=service_date,
        performed_by=owner.id,
        source="client",
    )


def test_service_routes_use_historical_odometer_cutover(app):
    assert (
        app.view_functions["admin.admin_add_service"].__module__
        == "services.service_history_route_cutover"
    )
    assert (
        app.view_functions["cars.add_service_record"].__module__
        == "services.service_history_route_cutover"
    )


def test_historical_service_preserves_authoritative_current_odometer(app):
    with app.app_context():
        owner, car, ownership = _create_owned_car(suffix="1")

        _record_service(
            owner=owner,
            car=car,
            ownership=ownership,
            mileage=10000,
            service_date="2025-01-15",
        )

        db.session.refresh(car)
        event = VehicleEvent.query.filter_by(
            car_id=car.id,
            event_type="service",
        ).one()

        assert event.mileage == 10000
        assert car.current_mileage == 64000
        assert resolve_current_mileage(car, ownership) == 64000


def test_newer_service_advances_authoritative_current_odometer(app):
    with app.app_context():
        owner, car, ownership = _create_owned_car(suffix="2")

        _record_service(
            owner=owner,
            car=car,
            ownership=ownership,
            mileage=65500,
            service_date="2026-09-06",
        )

        db.session.refresh(car)
        assert car.current_mileage == 65500
        assert resolve_current_mileage(car, ownership) == 65500


def test_health_engine_uses_current_odometer_against_historical_service(app):
    with app.app_context():
        owner, car, ownership = _create_owned_car(suffix="3")

        _record_service(
            owner=owner,
            car=car,
            ownership=ownership,
            mileage=10000,
            service_date="2025-01-15",
        )

        health = calculate_vehicle_health(car, ownership)

        assert "No maintenance history on record" not in health["risk_reasons"]
        assert "4 maintenance interval(s) overdue" in health["risk_reasons"]


def test_latest_service_is_the_interval_baseline(app):
    with app.app_context():
        owner, car, ownership = _create_owned_car(suffix="4")

        _record_service(
            owner=owner,
            car=car,
            ownership=ownership,
            mileage=10000,
            service_date="2025-01-15",
            service_type="Routine service",
        )
        _record_service(
            owner=owner,
            car=car,
            ownership=ownership,
            mileage=52000,
            service_date="2026-07-15",
            service_type="Routine service",
        )

        health = calculate_vehicle_health(car, ownership)

        assert not any("maintenance interval" in reason for reason in health["risk_reasons"])
        assert car.current_mileage == 64000


def test_service_creation_refreshes_maintenance_monitoring_signal(app):
    with app.app_context():
        owner, car, ownership = _create_owned_car(suffix="5")

        refreshed = _record_service_with_monitoring(
            car=car,
            ownership=ownership,
            service_type="Routine service",
            mileage=10000,
            description="Synthetic maintenance-monitoring smoke record.",
            service_date="2025-01-15",
            performed_by=owner.id,
            source="client",
        )

        assert refreshed is True
        db.session.refresh(car)
        assert car.current_mileage == 64000

        signal = VehicleHealthAlert.query.filter_by(
            car_id=car.id,
            ownership_id=ownership.id,
            alert_type="maintenance_monitoring",
            is_active=True,
        ).one()

        assert signal.status == "new"
        assert signal.severity == "low"
        assert signal.message == (
            "Routine maintenance monitoring is recommended "
            "based on current vehicle data."
        )

        event = VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert",
            subject_id=signal.id,
            event_type="care_signal.raised",
        ).one()

        assert event.actor_type == "system"
        assert event.actor_user_id is None
        assert event.new_state == "new"
        assert event.data["alert_type"] == "maintenance_monitoring"
        assert event.data["source_classification"] == (
            "deterministic_rule:event_created"
        )
