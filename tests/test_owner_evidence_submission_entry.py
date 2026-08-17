from __future__ import annotations

from datetime import datetime

from extensions import db
from models import Car, CarOwnership, User


PASSWORD = "Password123"


def _owner(*, suffix: int, verified: bool = True) -> User:
    user = User(
        name=f"Owner Evidence Entry {suffix}",
        email=f"owner-evidence-entry-{suffix}@example.com",
        phone_number=f"+234898100{suffix:04d}",
        role="user",
        is_active=True,
        email_verified_at=(datetime(2026, 8, 17, 22, 0, 0) if verified else None),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _owned_car(owner: User, *, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2021,
        vin=f"W1NOWNEREVID{suffix:05d}",
        current_mileage=42000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"OE-{suffix:03d}-LA",
            mileage_at_transfer=42000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


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


def test_verified_owner_sees_submission_entry_before_timeline_cutover(app, client):
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    app.config["EVIDENCE_TIMELINE_ENABLED"] = False

    with app.app_context():
        owner = _owner(suffix=1)
        car = _owned_car(owner, suffix=1)
        owner_email = owner.email
        car_id = car.id

    _login(client, owner_email)
    response = client.get(f"/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Supporting Evidence" in html
    assert "Submit Image for Review" in html
    assert f'/evidence/vehicles/{car_id}/submit' in html
    assert "Reviewed Evidence Record" not in html
    assert "No reviewed evidence has been added yet." not in html


def test_unverified_owner_does_not_see_submission_entry(app, client):
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    app.config["EVIDENCE_TIMELINE_ENABLED"] = False

    with app.app_context():
        owner = _owner(suffix=2, verified=False)
        car = _owned_car(owner, suffix=2)
        owner_email = owner.email
        car_id = car.id

    _login(client, owner_email)
    response = client.get(f"/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Supporting Evidence" not in html
    assert "Submit Image for Review" not in html
