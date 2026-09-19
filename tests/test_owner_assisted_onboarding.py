from __future__ import annotations

import hashlib
import re
from pathlib import Path
from datetime import datetime, timedelta

from extensions import db
from mileage.models import MileageObservation
from models import Car, CarOwnership, ClientInvitation, User
from services.client_onboarding import ClientOnboardingService


PASSWORD = "Password123"
ROOT = Path(__file__).resolve().parents[1]


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


def _csrf_token(client) -> str:
    with client.session_transaction() as flask_session:
        token = flask_session.get("_csrf_token")
        if not token:
            token = "owner-onboarding-test-csrf-token"
            flask_session["_csrf_token"] = token
        return str(token)


def _post(client, path: str, *, data=None, **kwargs):
    payload = dict(data or {})
    payload.setdefault("csrf_token", _csrf_token(client))
    return client.post(path, data=payload, **kwargs)


def _sign_in(client, user: User) -> None:
    response = _post(client, 
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

    response = _post(client, 
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

    response = _post(client, 
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

    _post(client, 
        f"/auth/activate/{token}",
        data={
            "password": "OwnerPassword123",
            "confirm_password": "OwnerPassword123",
        },
    )

    response = _post(client, 
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

    response = _post(client, 
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

    response = _post(client, 
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

    response = _post(client, 
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


def test_advisor_cannot_reissue_activation_after_owner_accepts(app, client):
    with app.app_context():
        advisor = _create_user(
            name="Aura Advisor",
            email="advisor8@example.com",
            phone="+2348000000181",
            role="admin",
        )
        owner, issued = ClientOnboardingService.create_client(
            name="Activated Owner",
            phone_number="+2348000000182",
            email=None,
            created_by_user_id=advisor.id,
        )
        owner_id = owner.id
        token = issued.token

    activation = _post(client, 
        f"/auth/activate/{token}",
        data={
            "password": "OwnerPassword123",
            "confirm_password": "OwnerPassword123",
        },
    )
    assert activation.status_code == 302

    _post(client, "/auth/logout")

    with app.app_context():
        advisor = User.query.filter_by(email="advisor8@example.com").one()
        _sign_in(client, advisor)

    response = _post(client, f"/admin/clients/{owner_id}/activation-link")

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/admin/clients/{owner_id}")

    with app.app_context():
        invitations = ClientInvitation.query.filter_by(user_id=owner_id).all()
        assert len(invitations) == 1
        assert invitations[0].accepted_at is not None


def test_unchanged_unverified_email_does_not_send_again(app, client, monkeypatch):
    deliveries = []

    def fake_send(user):
        deliveries.append(user.email)
        return True

    monkeypatch.setattr("onboarding.routes.send_email_verification", fake_send)

    with app.app_context():
        owner = _create_user(
            name="Unverified Owner",
            email="unverified@example.com",
            phone="+2348000000191",
            role="user",
            verified=False,
        )
        _sign_in(client, owner)

    response = _post(client, 
        "/account/setup",
        data={
            "name": "Unverified Owner",
            "phone_number": "+2348000000191",
            "email": "unverified@example.com",
        },
    )

    assert response.status_code == 302
    assert deliveries == []


def test_activated_owner_without_email_can_sign_back_in_by_phone(app, client):
    with app.app_context():
        advisor = _create_user(
            name="Aura Advisor",
            email="advisor9@example.com",
            phone="+2348000000201",
            role="admin",
        )
        owner, issued = ClientOnboardingService.create_client(
            name="Phone Login Owner",
            phone_number="+2348000000202",
            email=None,
            created_by_user_id=advisor.id,
        )
        token = issued.token

    activation = _post(client, 
        f"/auth/activate/{token}",
        data={
            "password": "OwnerPassword123",
            "confirm_password": "OwnerPassword123",
        },
    )
    assert activation.status_code == 302

    _post(client, "/auth/logout")

    response = _post(client, 
        "/auth/login",
        data={
            "identifier": "+2348000000202",
            "password": "OwnerPassword123",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard/")

    setup = client.get("/account/setup")
    assert setup.status_code == 200


def test_self_service_vehicle_mileage_is_pending_evidence_not_verified_current(
    app,
    client,
):
    with app.app_context():
        owner = _create_user(
            name="Self Service Owner",
            email="selfservice@example.com",
            phone="+2348000000211",
            role="user",
        )
        owner_id = owner.id
        _sign_in(client, owner)

    response = _post(client, 
        "/cars/add",
        data={
            "brand": "Mercedes-Benz",
            "model": "C 300",
            "year": "2022",
            "vin": "W1KWF8EB0NR123456",
            "plate_number": "",
            "mileage_at_transfer": "42000",
            "color": "",
            "engine_number": "",
            "engine_type": "",
            "transmission": "",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/cars/my-vehicles")

    with app.app_context():
        car = Car.query.filter_by(vin="W1KWF8EB0NR123456").one()
        ownership = CarOwnership.query.filter_by(
            user_id=owner_id,
            car_id=car.id,
            is_active=True,
        ).one()
        observation = MileageObservation.query.filter_by(car_id=car.id).one()

        assert car.current_mileage is None
        assert ownership.mileage_at_transfer == 42000
        assert observation.odometer_km == 42000
        assert observation.source == "client_report"
        assert observation.verification_status == "client_reported"
        assert observation.review_status == "pending"

    vehicles = client.get("/cars/my-vehicles")
    assert vehicles.status_code == 200
    html = vehicles.get_data(as_text=True)
    assert "C 300" in html
    assert "Not verified yet" in html


def test_owner_navigation_and_vehicle_templates_do_not_regress_to_internal_surfaces():
    base = (ROOT / "templates/base.html").read_text(encoding="utf-8")
    vehicle = (ROOT / "templates/car_detail.html").read_text(encoding="utf-8")
    concerns = (ROOT / "templates/cars/faults_list.html").read_text(encoding="utf-8")

    assert "cars.my_vehicles" in base
    assert '<a href="/cars" class="nav-item">Vehicles</a>' not in base
    assert "Driver Trust Score" not in vehicle
    assert "fault.severity" not in concerns

    dtc_form = "url_for('admin.add_vehicle_dtc', car_id=car.id)"
    assert dtc_form in vehicle
    before_form = vehicle[: vehicle.index(dtc_form)]
    assert "{% if is_admin_view %}" in before_form[-500:]
