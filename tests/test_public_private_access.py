"""Public homepage, controlled registration, and private enquiry protections."""

import re

from extensions import db
from models import User
from public_site.models import PrivateAccessRequest


def token(client):
    page = client.get("/")
    assert page.status_code == 200
    return re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()


def test_public_homepage_and_private_routes(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Your vehicle has a history" in response.data
    assert b"ILLUSTRATION" in response.data
    assert b"/auth/login" in response.data
    assert client.get("/admin/access-requests").status_code in (302, 401)


def test_private_access_form_csrf_and_persistence(app, client, monkeypatch):
    monkeypatch.setattr("public_site.routes.send_transactional_email", lambda **kwargs: type("Delivery", (), {"success": True})())
    assert client.post("/request-access", data={"name":"Test"}).status_code == 400
    csrf = token(client)
    payload = {
        "csrf_token": csrf, "name": "Sample Owner", "email": "Owner@example.com",
        "vehicle": "Mercedes-Benz GLE", "request_type": "assessment",
        "contact_consent": "yes",
    }
    response = client.post("/request-access", data=payload)
    assert response.status_code == 303
    assert "/?submitted=1#request-access" in response.headers["Location"]
    with app.app_context():
        saved = PrivateAccessRequest.query.one()
        assert saved.email == "owner@example.com"
        assert saved.status == "pending"
        assert saved.request_type == "assessment"
        assert User.query.filter_by(email=saved.email).first() is None


def test_honeypot_does_not_persist(app, client):
    csrf = token(client)
    response = client.post("/request-access", data={
        "csrf_token":csrf, "website":"http://spam.invalid"
    })
    assert response.status_code == 303
    with app.app_context():
        assert PrivateAccessRequest.query.count() == 0


def test_registration_gate_blocks_new_owners_without_invitation(app, client):
    app.config["AURA_PRIVATE_ACCESS_ONLY"] = True
    response = client.get("/auth/signup")
    assert response.status_code == 302
    assert "#request-access" in response.headers["Location"]
    csrf = token(client)
    response = client.post("/auth/signup", data={
        "csrf_token":csrf, "name":"No Invite",
        "email":"new@example.com", "phone_number":"+2348000000000",
        "password":"SecurePassword2026",
    })
    assert response.status_code == 303
    with app.app_context():
        assert User.query.filter_by(email="new@example.com").first() is None


def test_unauthenticated_cannot_review_enquiries(client):
    assert client.post("/admin/access-requests/1/status",
        data={"status":"closed"}).status_code in (302, 400, 401)


def test_private_access_form_rejects_unconsented_and_bad_input(app, client):
    csrf = token(client)
    for payload in (
        {"csrf_token":csrf, "name":"Owner", "email":"owner@example.com", "request_type":"private_access"},
        {"csrf_token":csrf, "name":"Owner", "email":"invalid", "request_type":"private_access", "contact_consent":"yes"},
        {"csrf_token":csrf, "name":"Owner", "email":"owner@example.com", "request_type":"admin", "contact_consent":"yes"},
    ):
        assert client.post("/request-access", data=payload).status_code == 400
    with app.app_context():
        assert db.session.query(PrivateAccessRequest).count() == 0


def test_public_enquiry_rate_limit_is_per_ip(client):
    csrf = token(client)
    for _ in range(3):
        response = client.post("/request-access", data={
            "csrf_token": csrf, "name": "X", "email": "invalid"
        })
        assert response.status_code == 400
    blocked = client.post("/request-access", data={
        "csrf_token": csrf, "name": "Different", "email": "other@example.com"
    })
    assert blocked.status_code == 429


def test_public_enquiry_links_target_the_form_and_preserve_request_type(client):
    page = client.get("/")
    body = page.get_data(as_text=True)
    for intent in ("private_access", "assessment", "fleet"):
        assert f'data-request-type="{intent}"' in body
        assert f'request_type={intent}#access-form' in body

    for intent, label in (
        ("private_access", "Private Aura access"),
        ("assessment", "Professional vehicle assessment"),
        ("fleet", "Fleet / partnership enquiry"),
    ):
        response = client.get(f"/?request_type={intent}")
        assert response.status_code == 200
        assert f'value="{intent}" selected'.encode() in response.data
        assert label in response.get_data(as_text=True)
