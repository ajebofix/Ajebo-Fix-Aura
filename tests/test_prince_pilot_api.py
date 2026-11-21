"""HTTP-level Prince pilot authorization tests (synthetic identities only)."""
from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import User
from partnerships.prince_models import (
    PrinceCustomer, PrinceJob, PrinceMembership, PrinceTenant, PrinceVehicle,
)

URL = "/pilot/workshops/prince"
PRIVATE = {"phone_number", "email", "home_address", "phone_ciphertext",
           "email_ciphertext", "address_ciphertext"}


def _make_user(name, suffix):
    user = User(
        name=name, email=f"pilot-{suffix}@example.test",
        phone_number=f"+234800001{int(suffix):04d}",
        role="user", is_active=True, email_verified_at=datetime.utcnow(),
    )
    user.set_password("TestPassword123!")
    db.session.add(user)
    db.session.flush()
    return user


@pytest.fixture
def pilot(app):
    app.config["AURA_PRINCE_PILOT_API_ENABLED"] = True
    with app.app_context():
        seun = _make_user("Synthetic Operator", "1")
        principal = _make_user("Synthetic Principal", "2")
        other = _make_user("Synthetic Other Operator", "3")
        stranger = _make_user("Synthetic Stranger", "4")
        prince = PrinceTenant(
            slug="prince", display_name="Prince Automobile Clinic",
            active=True, intake_enabled=True,
            approved_privacy_notice_version="pilot-notice-v1",
        )
        other_tenant = PrinceTenant(
            slug="another-workshop", display_name="Another Workshop",
            active=True, intake_enabled=True,
            approved_privacy_notice_version="pilot-notice-v1",
        )
        db.session.add_all([prince, other_tenant])
        db.session.flush()
        db.session.add_all([
            PrinceMembership(tenant_id=prince.id, user_id=seun.id, role="operator", active=True),
            PrinceMembership(tenant_id=prince.id, user_id=principal.id, role="principal", active=True),
            PrinceMembership(tenant_id=prince.id, user_id=other.id, role="operator", active=True),
            PrinceMembership(tenant_id=other_tenant.id, user_id=stranger.id, role="operator", active=True),
        ])
        own_customer = PrinceCustomer(
            tenant_id=prince.id, full_name="Unassigned Synthetic",
            intake_by_user_id=principal.id,
            privacy_notice_version="pilot-notice-v1", participation_confirmed=True,
        )
        other_customer = PrinceCustomer(
            tenant_id=other_tenant.id, full_name="Cross Tenant Synthetic",
            intake_by_user_id=stranger.id,
            privacy_notice_version="pilot-notice-v1", participation_confirmed=True,
        )
        db.session.add_all([own_customer, other_customer])
        db.session.flush()
        own_vehicle = PrinceVehicle(
            tenant_id=prince.id, customer_id=own_customer.id,
            brand="Mercedes-Benz", model="W211", vin=None,
        )
        cross_vehicle = PrinceVehicle(
            tenant_id=other_tenant.id, customer_id=other_customer.id,
            brand="Toyota", model="Camry", vin=None,
        )
        db.session.add_all([own_vehicle, cross_vehicle])
        db.session.flush()
        own_job = PrinceJob(
            tenant_id=prince.id, customer_id=own_customer.id,
            vehicle_id=own_vehicle.id, assigned_user_id=other.id,
            reported_concern="Unverified", status="draft",
        )
        cross_job = PrinceJob(
            tenant_id=other_tenant.id, customer_id=other_customer.id,
            vehicle_id=cross_vehicle.id, assigned_user_id=stranger.id,
            reported_concern="Unverified", status="draft",
        )
        db.session.add_all([own_job, cross_job])
        db.session.commit()
        return {
            "seun_email": seun.email,
            "principal_email": principal.email,
            "unassigned_id": own_job.id,
            "cross_id": cross_job.id,
        }


def _login(client, email):
    client.get("/auth/login")
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    r = client.post("/auth/login", data={
        "identifier": email, "password": "TestPassword123!", "csrf_token": token,
    }, follow_redirects=False)
    assert r.status_code == 302
    client.get("/auth/login")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _intake_payload():
    return {
        "full_name": "Synthetic Owner",
        "phone_number": "+2348000000000",
        "email": "private@example.test",
        "home_address": "Private Test Address",
        "vehicle_brand": "Mercedes-Benz",
        "vehicle_model": "S-Class W221",
        "reported_concern": "Cranks, no start; fuel observation unverified",
        "privacy_notice_version": "pilot-notice-v1",
        "participation_confirmed": True,
    }


def test_feature_flag_blocks_routes_even_for_logged_out_user(client):
    assert client.get(f"{URL}/jobs").status_code == 404


def test_unauthenticated_access_denied_when_flag_enabled(app, client):
    app.config["AURA_PRINCE_PILOT_API_ENABLED"] = True
    assert client.get(f"{URL}/jobs").status_code == 401


def test_operator_intake_redaction_and_encryption(pilot, app, client):
    token = _login(client, pilot["seun_email"])
    r = client.post(f"{URL}/intake", json=_intake_payload(),
                    headers={"X-CSRF-Token": token})
    assert r.status_code == 201
    response = r.get_json()
    assert set(response) == {"customer_id", "full_name", "job_id"}
    assert not PRIVATE.intersection(response)
    assert "private@example.test" not in r.get_data(as_text=True)
    with app.app_context():
        customer = db.session.get(PrinceCustomer, response["customer_id"])
        assert customer.phone_ciphertext and "+2348000000000" not in customer.phone_ciphertext
        assert customer.email_ciphertext and "private@example.test" not in customer.email_ciphertext
        assert customer.address_ciphertext and "Private Test Address" not in customer.address_ciphertext
        vehicle = PrinceVehicle.query.filter_by(customer_id=customer.id).one()
        assert vehicle.vin is None
    detail = client.get(f"{URL}/jobs/{response['job_id']}")
    assert detail.status_code == 200
    assert detail.get_json()["full_name"] == "Synthetic Owner"
    assert "phone_number" not in detail.get_json()
    assert "private@example.test" not in detail.get_data(as_text=True)


def test_seun_sees_only_assigned_jobs_and_cannot_cross_tenant(pilot, client):
    _login(client, pilot["seun_email"])
    assert client.get(f"{URL}/jobs/{pilot['unassigned_id']}").status_code == 404
    assert client.get(f"{URL}/jobs/{pilot['cross_id']}").status_code == 404
    assert client.get("/pilot/workshops/another-workshop/jobs").status_code == 404
    listing = client.get(f"{URL}/jobs")
    assert listing.status_code == 200
    assert listing.get_json()["jobs"] == []


def test_operator_cannot_update_unassigned_job(pilot, client):
    token = _login(client, pilot["seun_email"])
    r = client.patch(f"{URL}/jobs/{pilot['unassigned_id']}/concern",
                     json={"reported_concern": "Overwrite"},
                     headers={"X-CSRF-Token": token})
    assert r.status_code == 404


def test_principal_can_view_within_tenant_but_not_cross_tenant(pilot, client):
    _login(client, pilot["principal_email"])
    assert client.get(f"{URL}/jobs/{pilot['unassigned_id']}").status_code == 200
    assert client.get(f"{URL}/jobs/{pilot['cross_id']}").status_code == 404


def test_intake_blocks_unapproved_fields_and_unapproved_notices(pilot, app, client):
    token = _login(client, pilot["seun_email"])
    payload = _intake_payload()
    payload["bank_pin"] = "DO-NOT-SAVE"
    r = client.post(f"{URL}/intake", json=payload, headers={"X-CSRF-Token": token})
    assert r.status_code == 400
    payload.pop("bank_pin")
    payload["privacy_notice_version"] = "not-approved"
    r = client.post(f"{URL}/intake", json=payload, headers={"X-CSRF-Token": token})
    assert r.status_code == 400
    with app.app_context():
        tenant = PrinceTenant.query.filter_by(slug="prince").one()
        tenant.intake_enabled = False
        db.session.commit()
    r = client.post(f"{URL}/intake", json=_intake_payload(),
                    headers={"X-CSRF-Token": token})
    assert r.status_code == 403


def test_intake_requires_csrf(pilot, client):
    _login(client, pilot["seun_email"])
    response = client.post(f"{URL}/intake", json=_intake_payload())
    assert response.status_code == 400


def test_intake_cannot_accept_unconfirmed_participation(pilot, client):
    token = _login(client, pilot["seun_email"])
    payload = _intake_payload()
    payload["participation_confirmed"] = False
    response = client.post(f"{URL}/intake", json=payload,
                           headers={"X-CSRF-Token": token})
    assert response.status_code == 400
