from __future__ import annotations

import json
import re
from datetime import datetime

from extensions import db
from models import AdvisorNote, Car, CarDriver, CarOwnership, User
from profiles.models import ClientProfile, ProfileAuditEvent


PASSWORD = "Password123"


def _create_user(
    *,
    name: str = "Femi Adebayo",
    email: str = "femi@example.com",
    phone: str = "+2348000000001",
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
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def _csrf_token_for(client, path: str) -> str:
    client.get(path)
    with client.session_transaction() as browser_session:
        return browser_session["_csrf_token"]


def _sign_in(client, user: User) -> None:
    token = _csrf_token_for(client, "/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "csrf_token": token,
            "email": user.email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 302


def _valid_profile_payload(token: str, **overrides) -> dict[str, str]:
    payload = {
        "csrf_token": token,
        "name": "Femi Adebayo",
        "occupation": "Managing Director",
        "organisation": "Adebayo Holdings",
        "gender": "male",
        "city": "Lagos",
        "state_region": "Lagos",
        "country": "Nigeria",
        "home_address": "12 Private Street, Lagos",
        "office_address": "4 Executive Avenue, Lagos",
        "preferred_communication": "whatsapp",
        "preferred_communication_time": "Weekdays, 9:00 AM-5:00 PM",
        "care_preference": "Preventive management and calm progress updates",
        "preferred_language": "English",
        "timezone": "Africa/Lagos",
        "emergency_contact_name": "Trusted Contact",
        "emergency_contact_phone": "+2348000000002",
        "marketing_consent": "on",
    }
    payload.update(overrides)
    return payload


def test_profile_pages_require_authentication(client):
    for path in ("/profile/", "/profile/edit", "/profile/privacy"):
        response = client.get(path)
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]


def test_profile_page_works_before_profile_row_exists(app, client):
    with app.app_context():
        user = _create_user()
        user_id = user.id
        _sign_in(client, user)

    response = client.get("/profile/")

    assert response.status_code == 200
    assert "My Profile" in response.get_data(as_text=True)
    with app.app_context():
        assert ClientProfile.query.filter_by(user_id=user_id).first() is None


def test_verified_client_can_create_encrypted_profile(app, client):
    with app.app_context():
        user = _create_user()
        user_id = user.id
        original_email = user.email
        original_phone = user.phone_number
        _sign_in(client, user)

    token = _csrf_token_for(client, "/profile/edit")
    response = client.post(
        "/profile/edit",
        data=_valid_profile_payload(
            token,
            email="attacker-controlled@example.com",
            phone_number="+19999999999",
            role="admin",
            care_plan="priority_access",
            vehicle_count="9000",
            driver_count="9000",
        ),
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/profile/")

    with app.app_context():
        refreshed = db.session.get(User, user_id)
        profile = ClientProfile.query.filter_by(user_id=user_id).one()
        audit = ProfileAuditEvent.query.filter_by(user_id=user_id).one()

        assert refreshed.email == original_email
        assert refreshed.phone_number == original_phone
        assert refreshed.role == "user"
        assert profile.occupation == "Managing Director"
        assert profile.preferred_communication == "whatsapp"
        assert profile.home_address == "12 Private Street, Lagos"
        assert profile.office_address == "4 Executive Avenue, Lagos"
        assert "12 Private Street" not in profile.home_address_ciphertext
        assert "4 Executive Avenue" not in profile.office_address_ciphertext
        assert audit.action == "profile_created"
        assert audit.success is True
        assert "email" not in audit.changed_fields
        assert "phone_number" not in audit.changed_fields
        assert "role" not in audit.changed_fields
        assert "care_plan" not in audit.changed_fields

        audit_blob = json.dumps(audit.changed_fields)
        assert "Private Street" not in audit_blob
        assert "Trusted Contact" not in audit_blob


def test_unverified_client_cannot_mutate_profile(app, client):
    with app.app_context():
        user = _create_user(verified=False)
        user_id = user.id
        _sign_in(client, user)

    token = _csrf_token_for(client, "/profile/edit")
    response = client.post(
        "/profile/edit",
        data=_valid_profile_payload(token),
    )

    assert response.status_code == 302
    assert "/auth/verification-required" in response.headers["Location"]
    with app.app_context():
        assert ClientProfile.query.filter_by(user_id=user_id).first() is None


def test_profile_update_rejects_invalid_values_and_audits_field_names(app, client):
    with app.app_context():
        user = _create_user()
        user_id = user.id
        _sign_in(client, user)

    token = _csrf_token_for(client, "/profile/edit")
    response = client.post(
        "/profile/edit",
        data=_valid_profile_payload(
            token,
            gender="invented-value",
            home_address="Sensitive rejected address",
        ),
    )

    assert response.status_code == 400
    with app.app_context():
        assert ClientProfile.query.filter_by(user_id=user_id).first() is None
        audit = ProfileAuditEvent.query.filter_by(user_id=user_id).one()
        assert audit.action == "profile_update_rejected"
        assert audit.success is False
        assert audit.reason_code == "validation_failed"
        assert "home_address" in audit.changed_fields
        assert "Sensitive rejected address" not in json.dumps(audit.changed_fields)


def test_profile_update_rejects_overlong_care_preference(app, client):
    with app.app_context():
        user = _create_user()
        _sign_in(client, user)

    token = _csrf_token_for(client, "/profile/edit")
    response = client.post(
        "/profile/edit",
        data=_valid_profile_payload(token, care_preference="x" * 1001),
    )

    assert response.status_code == 400
    assert "1000 characters or fewer" in response.get_data(as_text=True)


def test_profile_mutation_requires_csrf(app, client):
    with app.app_context():
        user = _create_user()
        _sign_in(client, user)

    response = client.post(
        "/profile/edit",
        data={"name": "No CSRF"},
    )

    assert response.status_code == 400


def test_profile_counts_only_active_owned_vehicles_and_distinct_drivers(app, client):
    with app.app_context():
        owner = _create_user()
        driver_one = _create_user(
            name="Driver One",
            email="driver1@example.com",
            phone="+2348000000011",
            role="driver",
        )
        driver_two = _create_user(
            name="Driver Two",
            email="driver2@example.com",
            phone="+2348000000012",
            role="driver",
        )

        cars = [
            Car(brand="Mercedes-Benz", model="GLE450", year=2021, vin="A" * 17),
            Car(brand="Mercedes-Benz", model="E350", year=2019, vin="B" * 17),
            Car(brand="BMW", model="X5", year=2020, vin="C" * 17),
        ]
        db.session.add_all(cars)
        db.session.flush()

        db.session.add_all(
            [
                CarOwnership(user_id=owner.id, car_id=cars[0].id, is_active=True),
                CarOwnership(user_id=owner.id, car_id=cars[1].id, is_active=True),
                CarOwnership(user_id=owner.id, car_id=cars[2].id, is_active=False),
                CarDriver(car_id=cars[0].id, user_id=driver_one.id, is_active=True),
                CarDriver(car_id=cars[1].id, user_id=driver_one.id, is_active=True),
                CarDriver(car_id=cars[2].id, user_id=driver_two.id, is_active=True),
            ]
        )
        db.session.commit()
        _sign_in(client, owner)

    response = client.get("/profile/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert re.search(
        r"<span>\s*2\s*</span>\s*<small>Active vehicles</small>",
        html,
    )
    assert re.search(
        r"<span>\s*1\s*</span>\s*<small>Assigned drivers</small>",
        html,
    )


def test_advisor_notes_are_not_exposed_on_client_profile(app, client):
    secret_note = "INTERNAL-ONLY-RELATIONSHIP-NOTE-7843"
    with app.app_context():
        client_user = _create_user()
        advisor = _create_user(
            name="Aura Advisor",
            email="advisor@example.com",
            phone="+2348000000030",
            role="admin",
        )
        db.session.add(
            AdvisorNote(
                user_id=client_user.id,
                advisor_id=advisor.id,
                note=secret_note,
            )
        )
        db.session.commit()
        _sign_in(client, client_user)

    response = client.get("/profile/")

    assert response.status_code == 200
    assert secret_note not in response.get_data(as_text=True)


def test_client_profile_has_no_arbitrary_user_id_route(app, client):
    with app.app_context():
        user = _create_user()
        _sign_in(client, user)

    response = client.get("/profile/999")
    assert response.status_code == 404


def test_privacy_centre_is_available_to_authenticated_client(app, client):
    with app.app_context():
        user = _create_user()
        _sign_in(client, user)

    response = client.get("/profile/privacy")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Privacy Centre" in html
    assert "No online service can eliminate every risk" in html
    assert "one-way hashed" in html
