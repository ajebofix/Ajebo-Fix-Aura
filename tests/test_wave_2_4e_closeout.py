from __future__ import annotations

from datetime import datetime

from extensions import db
from models import Car, CarDriver, CarOwnership, User, VehicleEvent
from priority.lifecycle import PriorityRequestLifecycleService
from services.care_signal_lifecycle import CareSignalLifecycleService
from services.driver_observation import DriverObservationService
from services.priority_scoring import PriorityScoringEngine


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Wave 2.4E {role} {suffix}",
        email=f"wave-2-4e-{role}-{suffix}@example.com",
        phone_number=f"+2348954{suffix:06d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 9, 9, 8, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _context(*, suffix: int):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 1000, role="admin")
    driver = _user(suffix=suffix + 2000, role="driver")

    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin=f"W1N24E{suffix:011d}",
        current_mileage=64000,
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"W24E-{suffix:03d}",
        mileage_at_transfer=63000,
        is_active=True,
        care_plan="priority_access",
    )
    db.session.add(ownership)
    db.session.add(CarDriver(user_id=driver.id, car_id=car.id, is_active=True))
    db.session.commit()
    return owner, advisor, driver, car, ownership


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


def test_wave_2_4_longitudinal_subjects_remain_distinct(app):
    """Driver, care-signal and priority facts coexist without semantic collapse."""

    with app.app_context():
        owner, _advisor, driver, car, ownership = _context(suffix=1)

        observation = DriverObservationService.record_checkin(
            car_id=car.id,
            actor_user_id=driver.id,
            dashboard_light=True,
            occurred_at=datetime(2026, 9, 9, 8, 15, 0),
        )

        care_signal = CareSignalLifecycleService.raise_signal(
            car_id=car.id,
            alert_type="maintenance_monitoring",
            severity="low",
            message=(
                "Routine maintenance monitoring is recommended based on "
                "current vehicle data."
            ),
            source_classification="deterministic_rule:closeout",
            actor_type="system",
            actor_user_id=None,
            occurred_at=datetime(2026, 9, 9, 8, 20, 0),
        )

        priority_request = PriorityRequestLifecycleService.create_request(
            car_id=car.id,
            actor_user_id=owner.id,
            request_kind="priority",
            request_source="owner",
            reason_summary="Owner requested priority coordination.",
        )
        db.session.commit()

        events = (
            VehicleEvent.query.filter_by(car_id=car.id)
            .order_by(VehicleEvent.id.asc())
            .all()
        )
        triples = {
            (event.event_type, event.subject_type, event.subject_id)
            for event in events
        }

        assert (
            "driver_observation.checkin_recorded",
            "driver_checkin",
            observation.checkin.id,
        ) in triples
        assert (
            "care_signal.raised",
            "vehicle_health_alert",
            care_signal.id,
        ) in triples
        assert (
            "priority.requested",
            "priority_request",
            priority_request.id,
        ) in triples

        assert observation.checkin.car_id == car.id
        assert care_signal.ownership_id == ownership.id
        assert priority_request.ownership_id == ownership.id

        assert not any(event.event_type.startswith("priority.score") for event in events)
        assert not any(event.event_type.startswith("monitoring.") for event in events)


def test_review_score_is_projection_and_does_not_emit_events(app):
    with app.app_context():
        _owner, _advisor, _driver, car, ownership = _context(suffix=2)
        before = VehicleEvent.query.filter_by(car_id=car.id).count()

        projection = PriorityScoringEngine.calculate(car, ownership)

        after = VehicleEvent.query.filter_by(car_id=car.id).count()
        assert after == before
        assert projection["record_kind"] == "projection"
        assert projection["workflow"] is None
        assert projection["band"] in {"low", "moderate", "high", "critical"}
        assert isinstance(projection["reasons"], list)


def test_duplicate_advisor_notice_route_redirects_to_alert_center(app):
    client = app.test_client()
    with app.app_context():
        _owner, advisor, _driver, _car, _ownership = _context(suffix=3)
        advisor_email = advisor.email

    _login(client, advisor_email)
    response = client.get(
        "/clinical_notices/advisor/health/notices",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/alerts")
