from __future__ import annotations

from datetime import datetime, timedelta

from extensions import db
from models import Car, CarOwnership, Consultation, User
from services.alert_service import AlertService
from services.care_signal_lifecycle import CareSignalLifecycleService


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Alert Center {role} {suffix}",
        email=f"alert-center-{role}-{suffix}@example.com",
        phone_number=f"+2348972{suffix:06d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _fixture(*, suffix: int, consultation_status: str = "requested"):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 1000, role="admin")
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin=f"W1N24AC{suffix:010d}",
        current_mileage=31000,
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"ACA-{suffix:03d}-LA",
        mileage_at_transfer=30000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.flush()

    signal = CareSignalLifecycleService.raise_signal(
        car_id=car.id,
        alert_type="low_health_status",
        severity="high",
        message="Durable care signal requires advisor review.",
        source_classification="deterministic_rule:test",
        actor_type="system",
        actor_user_id=None,
        occurred_at=datetime(2026, 9, 6, 8, 0, 0),
    )

    consultation = Consultation(
        car_id=car.id,
        ownership_id=ownership.id,
        advisor_id=advisor.id,
        client_id=owner.id,
        status=consultation_status,
        scheduled_for=datetime.utcnow() + timedelta(days=1),
        created_at=datetime.utcnow() - timedelta(days=6),
    )
    db.session.add(consultation)
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


def test_alert_service_marks_only_durable_care_signals_actionable(app):
    with app.app_context():
        _owner, _advisor, _car, signal = _fixture(suffix=1)
        alerts = AlertService.build_alert_center()

        care_signal = next(item for item in alerts if item["id"] == signal.id)
        consultation_delay = next(
            item for item in alerts if item["type"] == "consultation_delay"
        )

        assert care_signal["record_kind"] == "care_signal"
        assert care_signal["projection_source"] is None
        assert care_signal["actionable"] is True
        assert care_signal["vehicle"].id == signal.car_id

        assert consultation_delay["id"] is None
        assert consultation_delay["record_kind"] == "projection"
        assert consultation_delay["projection_source"] == "consultation"
        assert consultation_delay["actionable"] is False


def test_current_consultation_states_drive_delay_projection(app):
    with app.app_context():
        _fixture(suffix=20, consultation_status="scheduled")
        _fixture(suffix=21, consultation_status="deferred")
        alerts = AlertService.build_alert_center()
        projected_car_ids = {
            item["vehicle"].id
            for item in alerts
            if item["type"] == "consultation_delay"
        }

        scheduled = Car.query.filter_by(vin=f"W1N24AC{20:010d}").one()
        deferred = Car.query.filter_by(vin=f"W1N24AC{21:010d}").one()
        assert scheduled.id in projected_car_ids
        assert deferred.id in projected_car_ids


def test_alert_center_renders_actions_only_for_durable_care_signals(app):
    client = app.test_client()
    with app.app_context():
        _owner, advisor, _car, signal = _fixture(suffix=2)
        advisor_email = advisor.email
        signal_id = signal.id

    _login(client, advisor_email)
    response = client.get("/admin/alerts")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert "Durable care signal" in body
    assert "Consultation remains unresolved" in body
    assert "Computed operational projection · read-only" in body
    assert "Priority Requests" in body

    acknowledge_path = f"/admin/alerts/{signal_id}/acknowledge"
    resolve_path = f"/admin/alerts/{signal_id}/resolve"
    assert acknowledge_path in body
    assert resolve_path in body

    assert "/admin/alerts/None/acknowledge" not in body
    assert "/admin/alerts/None/resolve" not in body
