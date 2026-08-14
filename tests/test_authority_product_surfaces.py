from __future__ import annotations

from datetime import datetime
from pathlib import Path

from extensions import db
from models import Car, CarDriver, CarOwnership, User


PASSWORD = "Password123"
ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Authority Surface User {suffix}",
        email=f"authority-surface-{suffix}@example.com",
        phone_number=f"0800820{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 14, 8, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _car(*, suffix: int, model: str = "GLE 450 4MATIC") -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model=model,
        year=2021,
        vin=f"W1NAUTHSURF{suffix:06d}",
        current_mileage=45000 + suffix,
        vehicle_identity_source="manual",
    )
    db.session.add(car)
    db.session.flush()
    return car


def _own(*, owner: User, car: Car, suffix: int) -> CarOwnership:
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"AS-{suffix:03d}-LA",
        mileage_at_transfer=car.current_mileage,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.flush()
    return ownership


def _assign(*, driver: User, car: Car) -> CarDriver:
    assignment = CarDriver(
        user_id=driver.id,
        car_id=car.id,
        is_active=True,
    )
    db.session.add(assignment)
    db.session.flush()
    return assignment


def _csrf_token(client) -> str:
    client.get("/auth/login")
    with client.session_transaction() as flask_session:
        token = flask_session.get("_csrf_token")
        if not token:
            token = "authority-surface-test-csrf-token"
            flask_session["_csrf_token"] = token
        return str(token)


def _sign_in(client, user: User) -> None:
    response = client.post(
        "/auth/login",
        data={
            "csrf_token": _csrf_token(client),
            "email": user.email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 302


def test_driver_dashboard_uses_active_assignment_not_owner_empty_state(app, client):
    with app.app_context():
        owner = _user(suffix=1)
        driver = _user(suffix=2, role="driver")
        car = _car(suffix=1)
        _own(owner=owner, car=car, suffix=1)
        _assign(driver=driver, car=car)
        db.session.commit()
        car_id = car.id
        _sign_in(client, driver)

    response = client.get("/dashboard/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert "Mercedes-Benz GLE 450 4MATIC 2021" in body
    assert "Assigned Driver" in body
    assert "Open Driver Vehicle View" in body
    assert "Add Your First Vehicle" not in body
    assert f"/driver/cars/{car_id}" in body

    with client.session_transaction() as flask_session:
        assert flask_session.get("active_vehicle_id") == car_id
        # A presentation default still must not silently bind Rina.
        assert flask_session.get("rina_active_car_id") is None


def test_owner_dashboard_retains_owner_vehicle_management(app, client):
    with app.app_context():
        owner = _user(suffix=3)
        car = _car(suffix=3, model="S 450")
        _own(owner=owner, car=car, suffix=3)
        db.session.commit()
        _sign_in(client, owner)

    response = client.get("/dashboard/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert "Owner" in body
    assert "Vehicle Management" in body
    assert "Add Another Vehicle" in body
    assert "Open Driver Vehicle View" not in body


def test_driver_profile_is_presented_as_assignment_not_ownership(app, client):
    with app.app_context():
        owner = _user(suffix=4)
        driver = _user(suffix=5, role="driver")
        car = _car(suffix=4)
        _own(owner=owner, car=car, suffix=4)
        _assign(driver=driver, car=car)
        db.session.commit()
        _sign_in(client, driver)

    response = client.get("/profile/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert "Assigned Driver" in body
    assert "Assigned vehicles" in body
    assert "Vehicle relationship" in body
    assert "Owned vehicles" not in body
    assert "Assigned drivers" not in body


def test_admin_profile_and_base_navigation_use_administrator_label():
    profile_source = _source("templates/profiles/profile.html")
    base_source = _source("templates/base.html")

    assert "profile_role_label = 'Administrator'" in profile_source
    assert "aura_role_label = 'Administrator'" in base_source
    assert "current_user.role == 'admin'" in profile_source
    assert "{{ client_type }}" not in profile_source


def test_rina_chat_visibly_distinguishes_all_supported_authorities():
    source = _source("templates/components/rina_chat.html")

    for authority, label in (
        ("driver", "Assigned Driver"),
        ("owner", "Vehicle Owner"),
        ("advisor", "Advisor"),
        ("administrator", "Administrator"),
    ):
        assert f'authority === "{authority}"' in source
        assert f'label: "{label}"' in source

    assert "Owner financial and approval authority are not available" in source
    assert "selectedAuthority !== \"owner\"" in source
    assert "Rina is scoped to ${data.label} as ${presentation.label}." in source


def test_dashboard_selection_reauthorizes_owner_or_driver_scope():
    source = _source("dashboard/routes.py")

    assert "CarDriver.query.filter" in source
    assert "resolve_rina_authority" in source
    assert "AUTHORITY_OWNER" in source
    assert "AUTHORITY_DRIVER" in source
    assert "Select professional vehicle scope from the advisor workflow." in source
