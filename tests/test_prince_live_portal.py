"""Test the closed-by-default live staff surface without real data."""
from datetime import datetime

import pytest

from extensions import db
from models import User
from partnerships.prince_models import PrinceMembership, PrinceTenant


PROJECT = "11111111-1111-4111-8111-111111111111"
ENVIRONMENT = "22222222-2222-4222-8222-222222222222"
PASSWORD = "SyntheticStaffTestPassword!"


@pytest.fixture
def live(app, monkeypatch):
    for name, value in {
        "RAILWAY_PROJECT_ID": PROJECT, "PRINCE_LIVE_EXPECTED_PROJECT_ID": PROJECT,
        "RAILWAY_ENVIRONMENT_ID": ENVIRONMENT,
        "PRINCE_LIVE_EXPECTED_ENVIRONMENT_ID": ENVIRONMENT,
        "PRINCE_STAGING_SYNTHETIC_SEED": "false",
    }.items():
        monkeypatch.setenv(name, value)
    app.config.update(APP_ENV="prince-live-pilot", PRINCE_LIVE_PILOT_LOCKDOWN=True,
                      PRINCE_STAGING_LOCKDOWN=False, AURA_PRINCE_PILOT_API_ENABLED=False)
    tenant = PrinceTenant(slug="prince", display_name="Synthetic Prince",
                         active=True, intake_enabled=False)
    user = User(name="Synthetic staff", email="staff@example.test",
                phone_number="synthetic-staff", role="user", is_active=True,
                email_verified_at=datetime.utcnow())
    user.set_password(PASSWORD)
    db.session.add_all([tenant, user])
    db.session.flush()
    member = PrinceMembership(tenant_id=tenant.id, user_id=user.id,
                              role="principal", active=True)
    db.session.add(member)
    db.session.commit()
    return member.id


def _sign_in(client, password=PASSWORD):
    assert client.get("/prince-staff").status_code == 200
    with client.session_transaction() as sess:
        csrf = sess["_csrf_token"]
    return client.post("/prince-staff", data={
        "email": "staff@example.test", "password": password, "csrf_token": csrf,
    })


def test_disabled_by_default(client):
    assert client.get("/prince-staff").status_code == 404


def test_live_staff_only_and_no_workshop_data(live, client):
    assert client.get("/prince-staff/ready").status_code == 404
    assert _sign_in(client).status_code == 303
    assert client.get("/prince-staff/ready").status_code == 200
    for path in ("/dashboard", "/cars", "/admin", "/prince-demo",
                 "/pilot/workshops/prince/jobs", "/pilot/workshops/prince/finance"):
        assert client.get(path).status_code == 404
    with client.session_transaction() as sess:
        csrf = sess["_csrf_token"]
    assert client.post("/pilot/workshops/prince/intake", json={},
                       headers={"X-CSRF-Token": csrf}).status_code == 404
    assert client.post("/prince-staff/sign-out", data={"csrf_token": csrf}).status_code == 303
    assert client.get("/prince-staff/ready").status_code == 404


def test_bad_password_and_csrf(live, client):
    assert _sign_in(client, "incorrect").status_code == 401
    assert client.post("/prince-staff", data={"email": "staff@example.test",
                                            "password": PASSWORD}).status_code == 400


def test_membership_revoked(live, client):
    assert _sign_in(client).status_code == 303
    member = db.session.get(PrinceMembership, live)
    member.active = False
    db.session.commit()
    assert client.get("/prince-staff/ready").status_code == 404


def test_misconfiguration_fails_closed(live, client, monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "wrong")
    assert client.get("/prince-staff").status_code == 503
    assert client.get("/dashboard").status_code == 503


def test_api_cannot_be_enabled_in_staff_rehearsal(live, app, client):
    app.config["AURA_PRINCE_PILOT_API_ENABLED"] = True
    assert client.get("/prince-staff").status_code == 503


def test_demo_seed_cannot_be_enabled(live, client, monkeypatch):
    monkeypatch.setenv("PRINCE_STAGING_SYNTHETIC_SEED", "true")
    assert client.get("/prince-staff").status_code == 503
