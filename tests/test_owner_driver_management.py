from __future__ import annotations

from datetime import datetime

from extensions import db
from models import AccessCode, Car, CarDriver, CarOwnership, User


def _create_user(
    *,
    name: str,
    email: str,
    phone: str,
    role: str = "user",
    verified: bool = True,
) -> User:
    user = User(
        name=name,
        email=email,
        phone_number=phone,
        role=role,
        email_verified_at=datetime.utcnow() if verified else None,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _create_owned_car(owner: User, *, suffix: str = "1") -> tuple[Car, CarOwnership]:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin=f"4JGFB5KB5MA4775{suffix.zfill(2)}",
        current_mileage=64000,
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"TES-{suffix.zfill(2)}-TAA",
        mileage_at_transfer=64000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return car, ownership


def _csrf_token_for(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200
    with client.session_transaction() as session:
        return session["_csrf_token"]


def _sign_in(client, user: User) -> None:
    token = _csrf_token_for(client, "/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "csrf_token": token,
            "email": user.email,
            "password": "Password123",
        },
    )
    assert response.status_code == 302


def test_verified_owner_can_create_driver_invitation(app, client):
    with app.app_context():
        owner = _create_user(
            name="Femi Adebayo",
            email="owner@example.com",
            phone="08000000101",
        )
        car, _ownership = _create_owned_car(owner)
        owner_id = owner.id
        car_id = car.id
        _sign_in(client, owner)

    token = _csrf_token_for(client, "/auth/sessions")
    response = client.post(
        f"/admin/cars/{car_id}/invite-driver",
        data={"csrf_token": token},
    )

    assert response.status_code == 302
    assert f"/cars/{car_id}?whatsapp_link=" in response.headers["Location"]

    with app.app_context():
        invitation = AccessCode.query.filter_by(
            car_id=car_id,
            owner_id=owner_id,
            role="driver",
            is_used=False,
        ).one()
        assert len(invitation.code) == 12
        assert invitation.expires_at > datetime.utcnow()


def test_owner_vehicle_page_receives_active_driver_context(app, client):
    with app.app_context():
        owner = _create_user(
            name="Femi Adebayo",
            email="owner2@example.com",
            phone="08000000102",
        )
        driver = _create_user(
            name="Kunle Driver",
            email="driver@example.com",
            phone="08000000103",
            role="driver",
        )
        car, _ownership = _create_owned_car(owner, suffix="2")
        assignment = CarDriver(
            car_id=car.id,
            user_id=driver.id,
            is_active=True,
        )
        db.session.add(assignment)
        db.session.commit()
        car_id = car.id
        assignment_id = assignment.id
        _sign_in(client, owner)

    response = client.get(f"/cars/{car_id}")

    assert response.status_code == 200
    assert b"Kunle Driver" in response.data
    assert f"/admin/drivers/remove/{assignment_id}".encode() in response.data


def test_verified_owner_can_remove_assigned_driver(app, client):
    with app.app_context():
        owner = _create_user(
            name="Femi Adebayo",
            email="owner3@example.com",
            phone="08000000104",
        )
        driver = _create_user(
            name="Assigned Driver",
            email="driver2@example.com",
            phone="08000000105",
            role="driver",
        )
        car, _ownership = _create_owned_car(owner, suffix="3")
        assignment = CarDriver(
            car_id=car.id,
            user_id=driver.id,
            is_active=True,
        )
        db.session.add(assignment)
        db.session.commit()
        car_id = car.id
        assignment_id = assignment.id
        _sign_in(client, owner)

    token = _csrf_token_for(client, "/auth/sessions")
    response = client.post(
        f"/admin/drivers/remove/{assignment_id}",
        data={"csrf_token": token},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/cars/{car_id}")

    with app.app_context():
        refreshed = db.session.get(CarDriver, assignment_id)
        assert refreshed.is_active is False


def test_unrelated_user_cannot_invite_driver_for_another_vehicle(app, client):
    with app.app_context():
        owner = _create_user(
            name="Vehicle Owner",
            email="real-owner@example.com",
            phone="08000000106",
        )
        outsider = _create_user(
            name="Unrelated User",
            email="outsider@example.com",
            phone="08000000107",
        )
        car, _ownership = _create_owned_car(owner, suffix="4")
        car_id = car.id
        _sign_in(client, outsider)

    token = _csrf_token_for(client, "/auth/sessions")
    response = client.post(
        f"/admin/cars/{car_id}/invite-driver",
        data={"csrf_token": token},
    )

    assert response.status_code == 404
    with app.app_context():
        assert AccessCode.query.filter_by(car_id=car_id).count() == 0


def test_unverified_owner_is_redirected_before_driver_management(app, client):
    with app.app_context():
        owner = _create_user(
            name="Unverified Owner",
            email="unverified@example.com",
            phone="08000000108",
            verified=False,
        )
        car, _ownership = _create_owned_car(owner, suffix="5")
        car_id = car.id
        _sign_in(client, owner)

    token = _csrf_token_for(client, "/auth/sessions")
    response = client.post(
        f"/admin/cars/{car_id}/invite-driver",
        data={"csrf_token": token},
    )

    assert response.status_code == 302
    assert "/auth/verification-required" in response.headers["Location"]
    with app.app_context():
        assert AccessCode.query.filter_by(car_id=car_id).count() == 0


def test_driver_invitation_link_prefills_access_code(app, client):
    response = client.get("/auth/signup?access_code=AURA1234")

    assert response.status_code == 200
    assert b'value="AURA1234"' in response.data
