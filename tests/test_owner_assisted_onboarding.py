from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

from extensions import db
from mileage.models import MileageObservation
from models import Car, CarOwnership, ClientInvitation, User
from services.client_onboarding import ClientOnboardingService


PASSWORD = "Password123"


def _create_user(
    *,
    name: str,
    email: str | None,
    phone: str,
    role: str,
    verified: bool = True,
) -> User:
    user = User(
        name=name,
        email=email,
        phone_number=phone,
        role=role,
        email_verified_at=datetime.utcnow() if verified and email else None,
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _sign_in(client, user: User) -> None:
    response = client.post(
        "/auth/login",
        data={
            "email": user.email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 302


def test_advisor_can_create_client_without_email_and_get_single_use_link(app, client):
    with app.app_context():
        advisor = _create_user(
            name="Aura Advisor",
            email="advisor@example.com",
            phone="+2348000000101",
            role="admin",
        )
        _sign_in(client, advisor)

    response = client.post(
        "/admin/clients/new",
        data={
            "name": "Pilot Owner",
            "phone_number": "+2348000000102",
            "email": "",
        },
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    match = re.search(r"/auth/activate/([A-Za-z0-9_-]+)", html)
    assert match is not None
    raw_token = match.group(1)

    with app.app_context():
        owner = User.query.filter_by(phone_number="+2348000000102").one()
        invitation = ClientInvitation.query.filter_by(user_id=owner.id).one()

        assert owner.role == "user"
        assert owner.email is None
        assert owner.check_password(PASSWORD) is False
        assert invitation.token_hash == hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()
        assert raw_token != invitation.token_hash
        assert invitation.accepted_at is None
        assert invitation.revoked_at is None


def test_activation_sets_owner_password_logs_in_and_cannot_be_replayed(app, client):
    with app.app_context():
        advisor = _create_user(
            name="Aura Advisor",
            email="advisor2@example.com",
            phone="+2348000000111",
            role="admin",
        )
        owner, issued = ClientOnboardingService.create_client(
            name="Invited Owner",
            phone_number="+2348000000112",
            email=None,
            created_by_user_id=advisor.id,
        )
        owner_id = owner.id
        invitation_id = issued.invitation.id
        token = issued.token

    response = client.post(
        f"/auth/activate/{token}",
        data={
            "password": "OwnerPassword123",
            "confirm_password": "OwnerPassword123",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/account/setup")

    with app.app_context():
        owner = db.session.get(User, owner_id)
        invitation = db.session.get(ClientInvitation, invitation_id)
        assert owner.check_password("OwnerPassword123") is True
        assert invitation.accepted_at is not None

    setup = client.get("/account/setup")
    assert setup.status_code == 200

    replay = client.get(f"/auth/activate/{token}")
    assert replay.status_code == 302
    assert replay.headers["Location"].endswith("/auth/login")


def test_expired_activation_link_fails_closed(app, client):
    with app.app_context():
        advisor = _create_user(
            name="Aura Advisor",
            email="advisor3@example.com",
            phone="+2348000000121",
            role="admin",
        )
        owner, issued = ClientOnboardingService.create_client(
            name="Expired Owner",
            phone_number="+2348000000122",
            email=None,
            created_by_user_id=advisor.id,
        )
        issued.invitation.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
        token = issued.token

    response = client.get(f"/auth/activate/{token}")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/login")


def test_owner_can_add_email_after_activation_without_weakening_verification_gate(
    app,
    client,
    monkeypatch,
):
    deliveries = []

    def fake_send(user):
        deliveries.append(user.email)
        return True

    monkeypatch.setattr("onboarding.routes.send_email_verification", fake_send)

    with app.app_context():
        advisor = _create_user(
            name="Aura Advisor",
            email="advisor4@example.com",
            phone="+2348000000131",
            role="admin",
        )
        owner, issued = ClientOnboardingService.create_client(
            name="Setup Owner",
            phone_number="+2348000000132",
            email=None,
            created_by_user_id=advisor.id,
        )
        owner_id = owner.id
        token = issued.token

    client.post(
        f"/auth/activate/{token}",
        data={
            "password": "OwnerPassword123",
            "confirm_password": "OwnerPassword123",
        },
    )

    response = client.post(
        "/account/setup",
        data={
            "name": "Setup Owner Updated",
            "phone_number": "+2348000000132",
            "email": "owner@example.com",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/account/setup")
    assert deliveries == ["owner@example.com"]

    with app.app_context():
        owner = db.session.get(User, owner_id)
        assert owner.name == "Setup Owner Updated"
        assert owner.email == "owner@example.com"
        assert owner.email_verified_at is None

    protected = client.get("/cars/999/consultations/book")
    assert protected.status_code == 302
    assert "/auth/verification-required" in protected.headers["Location"]


def test_non_advisor_cannot_create_client(app, client):
    with app.app_context():
        owner = _create_user(
            name="Ordinary Owner",
            email="ordinary@example.com",
            phone="+2348000000141",
            role="user",
        )
        _sign_in(client, owner)

    response = client.post(
        "/admin/clients/new",
        data={
            "name": "Should Not Exist",
            "phone_number": "+2348000000142",
            "email": "",
        },
    )

    assert response.status_code == 403
    with app.app_context():
        assert User.query.filter_by(phone_number="+2348000000142").first() is None


def test_advisor_can_add_vehicle_with_verified_mileage_provenance(app, client):
    with app.app_context():
        advisor = _create_user(
            name="Aura Advisor",
            email="advisor5@example.com",
            phone="+2348000000151",
            role="admin",
        )
        owner, _ = ClientOnboardingService.create_client(
            name="Vehicle Owner",
            phone_number="+2348000000152",
            email=None,
            created_by_user_id=advisor.id,
        )
        owner_id = owner.id
        _sign_in(client, advisor)

    response = client.post(
        f"/admin/clients/{owner_id}/vehicles/new",
        data={
            "brand": "Mercedes-Benz",
            "model": "GLE 450",
            "year": "2021",
            "vin": "4JGFB5KB5MA477535",
            "plate_number": "ABC123XY",
            "mileage": "64100",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/admin/clients/{owner_id}")

    with app.app_context():
        car = Car.query.filter_by(vin="4JGFB5KB5MA477535").one()
        ownership = CarOwnership.query.filter_by(
            car_id=car.id,
            user_id=owner_id,
            is_active=True,
        ).one()
        observation = MileageObservation.query.filter_by(car_id=car.id).one()

        assert car.current_mileage == 64100
        assert ownership.mileage_at_transfer == 64100
        assert observation.source == "advisor_observation"
        assert observation.verification_status == "advisor_verified"
        assert observation.ownership_id == ownership.id


def test_advisor_vehicle_onboarding_does_not_steal_existing_active_vehicle(app, client):
    with app.app_context():
        advisor = _create_user(
            name="Aura Advisor",
            email="advisor6@example.com",
            phone="+2348000000161",
            role="admin",
        )
        first_owner = _create_user(
            name="Existing Owner",
            email="existing@example.com",
            phone="+2348000000162",
            role="user",
        )
        second_owner, _ = ClientOnboardingService.create_client(
            name="Second Owner",
            phone_number="+2348000000163",
            email=None,
            created_by_user_id=advisor.id,
        )

        car = Car(
            brand="Mercedes-Benz",
            model="E 350",
            year=2020,
            vin="WDDZF8EB0LA123456",
        )
        db.session.add(car)
        db.session.flush()
        db.session.add(
            CarOwnership(
                user_id=first_owner.id,
                car_id=car.id,
                is_active=True,
            )
        )
        db.session.commit()
        second_owner_id = second_owner.id
        car_id = car.id
        _sign_in(client, advisor)

    response = client.post(
        f"/admin/clients/{second_owner_id}/vehicles/new",
        data={
            "brand": "Mercedes-Benz",
            "model": "E 350",
            "year": "2020",
            "vin": "WDDZF8EB0LA123456",
        },
    )

    assert response.status_code == 409
    with app.app_context():
        active = CarOwnership.query.filter_by(car_id=car_id, is_active=True).all()
        assert len(active) == 1
        assert active[0].user_id != second_owner_id


def test_client_registry_includes_pending_owner_without_vehicle(app, client):
    with app.app_context():
        advisor = _create_user(
            name="Aura Advisor",
            email="advisor7@example.com",
            phone="+2348000000171",
            role="admin",
        )
        pending, _ = ClientOnboardingService.create_client(
            name="Pending Pilot Owner",
            phone_number="+2348000000172",
            email=None,
            created_by_user_id=advisor.id,
        )
        _sign_in(client, advisor)

    response = client.get("/admin/clients")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Pending Pilot Owner" in html
    assert "Invited" in html
    assert "No vehicle has been added yet." in html
