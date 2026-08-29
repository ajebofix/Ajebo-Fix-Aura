from __future__ import annotations

from datetime import datetime

from extensions import db
from models import Car, CarOwnership, TreatmentPlan, User, VehicleEvent


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Treatment Route {role} {suffix}",
        email=f"treatment-route-{role}-{suffix}@example.com",
        phone_number=f"+234896300{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 29, 9, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _fixture(*, suffix: int = 1):
    owner = _user(suffix=suffix)
    unrelated = _user(suffix=suffix + 100)
    driver = _user(suffix=suffix + 200, role="driver")
    advisor = _user(suffix=suffix + 300, role="admin")

    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1NTRROUTE{suffix:08d}",
        current_mileage=26000,
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"TP-{suffix:03d}-LA",
        mileage_at_transfer=25000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.flush()

    plan = TreatmentPlan(
        car_id=car.id,
        advisor_id=advisor.id,
        title="Front suspension care pathway",
        internal_instructions="ADVISOR ONLY: hidden execution detail",
        client_summary="A professional suspension treatment pathway is ready for review.",
        status="proposed",
    )
    db.session.add(plan)
    db.session.commit()
    return owner, unrelated, driver, advisor, car, plan


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


def test_owner_treatment_page_is_reachable_and_client_safe(app):
    client = app.test_client()
    with app.app_context():
        owner, _unrelated, _driver, _advisor, _car, plan = _fixture(suffix=1)
        owner_email = owner.email
        plan_id = plan.id

    _login(client, owner_email)
    response = client.get("/cars/treatment-plans")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Treatment Plans" in html
    assert "Front suspension care pathway" in html
    assert "Authorize Treatment Plan" in html
    assert "A professional suspension treatment pathway is ready for review." in html
    assert "ADVISOR ONLY: hidden execution detail" not in html
    assert f"/cars/treatment-plans/{plan_id}/authorize" in html


def test_owner_authorization_route_records_owner_fact_once(app):
    client = app.test_client()
    with app.app_context():
        owner, _unrelated, _driver, _advisor, _car, plan = _fixture(suffix=2)
        owner_email = owner.email
        owner_id = owner.id
        plan_id = plan.id

    _login(client, owner_email)
    response = client.post(
        f"/cars/treatment-plans/{plan_id}/authorize",
        data={"csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    assert response.headers["Location"].endswith("/cars/treatment-plans")

    # A browser retry/double-submit must not create another authorization fact.
    response = client.post(
        f"/cars/treatment-plans/{plan_id}/authorize",
        data={"csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        persisted = db.session.get(TreatmentPlan, plan_id)
        events = VehicleEvent.query.filter_by(
            subject_type="treatment_plan",
            subject_id=plan_id,
            event_type="treatment.authorized",
        ).all()

        assert persisted is not None
        assert persisted.status == "authorized"
        assert len(events) == 1
        assert events[0].actor_user_id == owner_id
        assert events[0].actor_authority == "owner"
        assert events[0].previous_state == "proposed"
        assert events[0].new_state == "authorized"
        assert events[0].visibility == "client"


def test_unrelated_owner_cannot_authorize_someone_elses_plan(app):
    client = app.test_client()
    with app.app_context():
        _owner, unrelated, _driver, _advisor, _car, plan = _fixture(suffix=3)
        unrelated_email = unrelated.email
        plan_id = plan.id

    _login(client, unrelated_email)
    response = client.post(
        f"/cars/treatment-plans/{plan_id}/authorize",
        data={"csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert response.status_code == 403

    with app.app_context():
        persisted = db.session.get(TreatmentPlan, plan_id)
        assert persisted is not None
        assert persisted.status == "proposed"
        assert VehicleEvent.query.filter_by(
            subject_type="treatment_plan",
            subject_id=plan_id,
        ).count() == 0


def test_driver_cannot_open_or_authorize_owner_treatment_plan(app):
    client = app.test_client()
    with app.app_context():
        _owner, _unrelated, driver, _advisor, _car, plan = _fixture(suffix=4)
        driver_email = driver.email
        plan_id = plan.id

    _login(client, driver_email)
    assert client.get("/cars/treatment-plans").status_code == 403
    response = client.post(
        f"/cars/treatment-plans/{plan_id}/authorize",
        data={"csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert response.status_code == 403

    with app.app_context():
        persisted = db.session.get(TreatmentPlan, plan_id)
        assert persisted is not None
        assert persisted.status == "proposed"
        assert VehicleEvent.query.filter_by(
            subject_type="treatment_plan",
            subject_id=plan_id,
        ).count() == 0
