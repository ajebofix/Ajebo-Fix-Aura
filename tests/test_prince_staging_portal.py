"""Synthetic-only route lock and two-factor demo login HTTP contract."""
from datetime import datetime

import pytest

from extensions import db
from models import User
from partnerships.prince_models import (
    PrinceCustomer, PrinceJob, PrinceMembership, PrinceTenant, PrinceVehicle
)

P = "/pilot/workshops/prince"
GATE = "p" * 40
USER_PASSWORD = "SyntheticStrongPassForTests-2026!"


@pytest.fixture
def stage(app, monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "3ed38d6c-6411-4890-861b-70cea583add2")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "aef601d6-38f3-4d16-99f0-aa908c87b3f2")
    monkeypatch.setenv("PRINCE_STAGING_GATE_TOKEN", GATE)
    app.config.update(
        APP_ENV="staging", PRINCE_STAGING_LOCKDOWN=True,
        AURA_PRINCE_PILOT_API_ENABLED=True
    )
    with app.app_context():
        tenant = PrinceTenant(
            slug="prince", display_name="Prince Automobile Clinic",
            active=True, intake_enabled=True,
            approved_privacy_notice_version="synthetic-staging-v1"
        )
        db.session.add(tenant)
        db.session.flush()
        accounts = {}
        actors = (
            ("principal", "principal"),
            ("operator-a", "operator"),
            ("operator-b", "operator"),
        )
        for i, (name, role) in enumerate(actors):
            user = User(
                name=f"Demo {name}",
                email=f"prince-demo-{name}@example.invalid",
                phone_number=f"demo-prince-{i+1}",
                role="user", is_active=True,
                email_verified_at=datetime.utcnow(),
            )
            user.set_password(USER_PASSWORD)
            db.session.add(user)
            db.session.flush()
            db.session.add(PrinceMembership(
                tenant_id=tenant.id, user_id=user.id, role=role, active=True
            ))
            accounts[name] = user
        owner = PrinceCustomer(
            tenant_id=tenant.id, full_name="Synthetic Principal Owner",
            intake_by_user_id=accounts["principal"].id,
            privacy_notice_version="synthetic-staging-v1", participation_confirmed=True,
        )
        db.session.add(owner)
        db.session.flush()
        vehicle = PrinceVehicle(
            tenant_id=tenant.id, customer_id=owner.id,
            brand="Mercedes-Benz", model="S-Class W221"
        )
        db.session.add(vehicle)
        db.session.flush()
        job = PrinceJob(
            tenant_id=tenant.id, customer_id=owner.id,
            vehicle_id=vehicle.id, assigned_user_id=accounts["operator-a"].id,
            reported_concern="Engine cranks; diagnostic evidence pending", status="draft",
        )
        db.session.add(job)
        db.session.commit()
        return {"job_id": job.id, "principal": accounts["principal"].id}


def _log_in(client, role="operator-a", gate=GATE, password=USER_PASSWORD):
    response = client.get("/prince-demo")
    assert response.status_code == 200
    with client.session_transaction() as sess:
        csrf = sess["_csrf_token"]
    return client.post(
        "/prince-demo",
        data={"role": role, "portal_code": gate, "password": password,
              "csrf_token": csrf},
    )


def test_staging_gate_closed_without_correct_project(app, client, stage, monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "wrong-project")
    assert client.get("/prince-demo").status_code == 404
    assert client.get(P + "/operator").status_code == 503


def test_public_cannot_read_prince_or_legacy_routes(stage, client):
    assert client.get("/prince-demo").status_code == 200
    assert client.get(P + "/operator").status_code == 404
    assert client.get(P + "/jobs").status_code == 404
    for route in ("/cars", "/version", "/auth/login", "/dashboard", "/"):
        assert client.get(route).status_code == 404


def test_bad_gate_and_password_rejected(stage, client):
    assert _log_in(client, gate="q" * 40).status_code == 401
    assert _log_in(client, password="incorrect-password").status_code == 401
    assert client.get(P + "/operator").status_code == 404


def test_operator_receives_only_assigned_private_page(stage, client):
    response = _log_in(client)
    assert response.status_code == 303
    assert response.headers["Location"].endswith(P + "/operator")
    page = client.get(P + "/operator")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Synthetic Principal Owner" in text
    assert "STAGING TEST ONLY." in text
    assert "Sign out of demo" in text
    assert page.headers["Cache-Control"] == "no-store"
    assert page.headers["X-Robots-Tag"].startswith("noindex")
    for route in ("/cars", "/dashboard", "/version", "/auth/login", "/",
                  "/admin", "/billing"):
        assert client.get(route).status_code == 404
    assert client.get("/pilot/workshops/other-workshop/jobs").status_code == 404
    assert client.get(P + "/jobs/" + str(stage["job_id"])).status_code == 200


def test_operator_b_cannot_see_operator_a_jobs(stage, client):
    assert _log_in(client, role="operator-b").status_code == 303
    page = client.get(P + "/operator").get_data(as_text=True)
    assert "Synthetic Principal Owner" not in page
    assert client.get(P + "/jobs/" + str(stage["job_id"])).status_code == 404


def test_principal_can_view_own_tenant_only(stage, client):
    assert _log_in(client, role="principal").status_code == 303
    page = client.get(P + "/operator").get_data(as_text=True)
    assert "Synthetic Principal Owner" in page
    assert "phone_ciphertext" not in page


def test_signout_revokes_staging_access(stage, client):
    assert _log_in(client).status_code == 303
    assert client.get(P + "/operator").status_code == 200
    with client.session_transaction() as sess:
        csrf = sess["_csrf_token"]
    response = client.post(
        "/prince-demo/sign-out", data={"csrf_token": csrf}
    )
    assert response.status_code == 303
    assert client.get(P + "/operator").status_code == 404


def test_staging_requires_csrf_for_signin(stage, client):
    client.get("/prince-demo")
    response = client.post(
        "/prince-demo", data={
            "role": "operator-a", "portal_code": GATE, "password": USER_PASSWORD,
        }
    )
    assert response.status_code == 400
