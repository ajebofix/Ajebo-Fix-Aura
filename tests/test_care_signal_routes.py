from __future__ import annotations

from datetime import datetime

from extensions import db
from models import Car, CarOwnership, User, VehicleEvent, VehicleHealthAlert
from services.care_signal_lifecycle import CareSignalLifecycleService


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Care Signal Route {role} {suffix}",
        email=f"care-signal-route-{role}-{suffix}@example.com",
        phone_number=f"+2348961{suffix:06d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _fixture(*, suffix: int):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 1000, role="admin")
    car = Car(
        brand="Mercedes-Benz",
        model="C 300",
        year=2023,
        vin=f"W1N24CSR{suffix:09d}",
        current_mileage=22000,
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"CSR-{suffix:03d}-LA",
        mileage_at_transfer=21000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.flush()
    signal = CareSignalLifecycleService.raise_signal(
        car_id=car.id,
        alert_type="low_health_status",
        severity="high",
        message="Vehicle health status indicates elevated risk. An advisor review is recommended.",
        source_classification="deterministic_rule:test",
        actor_type="system",
        actor_user_id=None,
        occurred_at=datetime(2026, 9, 6, 8, 0, 0),
    )
    db.session.commit()
    return owner, advisor, car, signal


def _csrf(client) -> str:
    with client.session_transaction() as flask_session:
        return str(flask_session["_csrf_token"])


def _login(client, email: str) -> None:
    client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "email": email,
            "password": PASSWORD,
            "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    client.get("/")
    _csrf(client)


def _post(client, path: str):
    return client.post(
        path,
        data={"csrf_token": _csrf(client)},
        follow_redirects=False,
    )


def test_admin_alert_routes_are_cut_over_to_lifecycle_service(app):
    assert app.view_functions["admin.acknowledge_alert"].__name__ == (
        "acknowledge_alert_cutover"
    )
    assert app.view_functions["admin.resolve_alert"].__name__ == "resolve_alert_cutover"


def test_advisor_acknowledge_and_resolve_routes_emit_canonical_events(app):
    client = app.test_client()
    with app.app_context():
        _owner, advisor, _car, signal = _fixture(suffix=1)
        advisor_email = advisor.email
        signal_id = signal.id

    _login(client, advisor_email)
    acknowledge = _post(client, f"/admin/alerts/{signal_id}/acknowledge")
    assert acknowledge.status_code in {302, 303}

    with app.app_context():
        signal = db.session.get(VehicleHealthAlert, signal_id)
        assert signal is not None
        assert signal.status == "acknowledged"
        ack_event = VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert",
            subject_id=signal_id,
            event_type="care_signal.acknowledged",
        ).one()
        assert ack_event.actor_authority in {"advisor", "administrator"}
        assert ack_event.visibility == "advisor"

    resolve = _post(client, f"/admin/alerts/{signal_id}/resolve")
    assert resolve.status_code in {302, 303}

    with app.app_context():
        signal = db.session.get(VehicleHealthAlert, signal_id)
        assert signal is not None
        assert signal.status == "resolved"
        assert signal.is_active is False
        resolve_event = VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert",
            subject_id=signal_id,
            event_type="care_signal.resolved",
        ).one()
        assert resolve_event.previous_state == "acknowledged"
        assert resolve_event.new_state == "resolved"
        assert resolve_event.actor_authority in {"advisor", "administrator"}
        assert resolve_event.visibility == "client"


def test_owner_cannot_mutate_advisor_care_signal_routes(app):
    client = app.test_client()
    with app.app_context():
        owner, _advisor, _car, signal = _fixture(suffix=2)
        owner_email = owner.email
        signal_id = signal.id

    _login(client, owner_email)
    response = _post(client, f"/admin/alerts/{signal_id}/resolve")
    assert response.status_code in {302, 403}

    with app.app_context():
        signal = db.session.get(VehicleHealthAlert, signal_id)
        assert signal is not None
        assert signal.status == "new"
        assert signal.is_active is True
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert",
            subject_id=signal_id,
            event_type="care_signal.resolved",
        ).count() == 0
