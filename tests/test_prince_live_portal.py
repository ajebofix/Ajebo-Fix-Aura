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


def test_live_environment_without_lockdown_denied(live, app, client):
    app.config["PRINCE_LIVE_PILOT_LOCKDOWN"] = False
    assert client.get("/dashboard").status_code == 503


def test_workshop_requires_approved_notice_and_staff(live, app, client, monkeypatch):
    app.config["PRINCE_LIVE_WORKSHOP_APPROVED"] = True
    monkeypatch.setenv("PRINCE_LIVE_PRIVACY_NOTICE_VERSION", "prince-notice-v1")
    tenant = PrinceTenant.query.filter_by(slug="prince").one()
    tenant.approved_privacy_notice_version = "different-version"
    tenant.intake_enabled = True
    db.session.commit()
    assert _sign_in(client).status_code == 303
    assert client.get("/pilot/workshops/prince/operator").status_code == 404
    tenant = PrinceTenant.query.filter_by(slug="prince").one()
    tenant.approved_privacy_notice_version = "prince-notice-v1"
    db.session.commit()
    assert client.get("/prince-staff/ready").status_code == 303
    assert client.get("/pilot/workshops/prince/operator").status_code == 200
    with client.session_transaction() as sess:
        csrf = sess["_csrf_token"]
    payload = {"full_name": "Synthetic consenting customer", "vehicle_brand": "Toyota",
               "vehicle_model": "Camry", "privacy_notice_version": "prince-notice-v1",
               "participation_confirmed": True}
    response = client.post("/pilot/workshops/prince/intake", json=payload,
                           headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201
    job_id = response.json["job_id"]
    customer_id = response.json["customer_id"]
    root = "/pilot/workshops/prince"
    for path in (f"/jobs/{job_id}/invoice", f"/jobs/{job_id}/clinical",
                 f"/customers/{customer_id}/private", "/finance"):
        assert client.get(root + path).status_code == 404
    assert client.get("/cars").status_code == 404
    app.config["PRINCE_LIVE_WORKSHOP_APPROVED"] = False
    assert client.get(root + "/jobs").status_code == 404


def test_written_customer_authorization_release(live, app, client, monkeypatch):
    app.config["PRINCE_LIVE_WORKSHOP_APPROVED"] = True
    monkeypatch.setenv("PRINCE_LIVE_PRIVACY_NOTICE_VERSION", "prince-notice-v1")
    tenant = PrinceTenant.query.filter_by(slug="prince").one()
    tenant.approved_privacy_notice_version = "prince-notice-v1"
    tenant.intake_enabled = True
    db.session.commit()
    assert _sign_in(client).status_code == 303
    client.get("/pilot/workshops/prince/operator")
    with client.session_transaction() as sess:
        headers = {"X-CSRF-Token": sess["_csrf_token"]}
    root = "/pilot/workshops/prince"
    intake = client.post(root + "/intake", json={
        "full_name": "Synthetic consenting customer", "vehicle_brand": "Toyota",
        "vehicle_model": "Camry", "privacy_notice_version": "prince-notice-v1",
        "participation_confirmed": True,
    }, headers=headers)
    assert intake.status_code == 201
    job = root + "/jobs/" + str(intake.json["job_id"])
    draft = client.post(job + "/estimates", json={
        "expected_version": 0, "discount_kobo": 0, "items": [{
            "category": "labour", "description": "Inspect suspension",
            "quantity": 1, "unit_kobo": 100000,
        }],
    }, headers=headers)
    assert draft.status_code == 201
    assert client.post(job + "/estimates/1/approve", json={
        "approval_note": "Reviewed exact proposed inspection scope and charge.",
    }, headers=headers).status_code == 200
    path = job + "/estimates/1/authorization"
    payload = {"expected_sequence": 0, "decision": "accepted", "channel": "written_message",
               "evidence_reference": "Test record ABC confirms exact estimate version and amount.",
               "customer_decision_attested": True}
    assert client.post(path, json=payload, headers=headers).status_code == 404
    app.config["PRINCE_LIVE_AUTHORIZATION_APPROVED"] = True
    assert client.post(path, json={**payload, "channel": "phone"}, headers=headers).status_code == 400
    assert client.post(path, json={**payload, "customer_decision_attested": False},
                       headers=headers).status_code == 400
    assert client.post(path, json={**payload, "evidence_reference": "Demo: sample authorization reference"},
                       headers=headers).status_code == 400
    assert client.post(path, json=payload, headers=headers).status_code == 201
    assert client.get(path).json["state"] == "accepted"
    assert client.post(path, json=payload, headers=headers).status_code == 409
    assert client.post(path, json={**payload, "expected_sequence": 1, "decision": "withdrawn"},
                       headers=headers).status_code == 201
    assert client.get(path).json["state"] == "withdrawn"
    assert client.get(job + "/invoice").status_code == 404
    assert client.get(job + "/clinical").status_code == 404


def test_live_finance_requires_release_and_verified_allocation(live, app, client, monkeypatch):
    app.config.update(PRINCE_LIVE_WORKSHOP_APPROVED=True,
                      PRINCE_LIVE_AUTHORIZATION_APPROVED=True)
    monkeypatch.setenv("PRINCE_LIVE_PRIVACY_NOTICE_VERSION", "prince-notice-v1")
    tenant = PrinceTenant.query.filter_by(slug="prince").one()
    tenant.approved_privacy_notice_version = "prince-notice-v1"
    tenant.intake_enabled = True
    db.session.commit()
    assert _sign_in(client).status_code == 303
    client.get("/pilot/workshops/prince/operator")
    with client.session_transaction() as sess:
        headers = {"X-CSRF-Token": sess["_csrf_token"]}
    root = "/pilot/workshops/prince"
    def post(path, payload):
        return client.post(path, json=payload, headers=headers)
    created = post(root + "/intake", {
        "full_name": "Synthetic finance customer", "vehicle_brand": "Toyota",
        "vehicle_model": "Camry", "privacy_notice_version": "prince-notice-v1",
        "participation_confirmed": True,
    })
    assert created.status_code == 201
    job = root + "/jobs/" + str(created.json["job_id"])
    assert post(job + "/estimates", {
        "expected_version": 0, "discount_kobo": 0, "items": [{
            "category": "labour", "description": "Inspection service",
            "quantity": 1, "unit_kobo": 100000,
        }],
    }).status_code == 201
    assert post(job + "/estimates/1/approve", {
        "approval_note": "Reviewed the exact inspection scope and amount.",
    }).status_code == 200
    assert post(job + "/estimates/1/authorization", {
        "expected_sequence": 0, "decision": "accepted", "channel": "written_message",
        "evidence_reference": "Test reference ABC records approval of exact estimate.",
        "customer_decision_attested": True,
    }).status_code == 201
    for old, new in (("draft", "awaiting_approval"), ("awaiting_approval", "approved")):
        assert post(job + "/lifecycle", {
            "expected_status": old, "new_status": new,
            "reason": "Reviewed the workshop records for progression.",
            "closure_disposition": None, "follow_up_plan": None,
        }).status_code == 200
    assert client.get(job + "/invoice").status_code == 404
    app.config["PRINCE_LIVE_FINANCE_APPROVED"] = True
    assert client.get(job + "/estimates/1/print").status_code == 200
    payload = {"expected_version": 1, "expected_authorization_sequence": 1,
               "issue_reason": "Recorded exact accepted inspection scope and amount.",
               "issue_attested": True}
    issued = post(job + "/invoice", payload)
    assert issued.status_code == 201
    assert issued.json["invoice"]["reference"].startswith("INV-PAC-")
    assert post(job + "/invoice", payload).status_code == 409
    path = job + "/invoice/payments"
    pay = {"reference": "PAC-PAY-TEST-A", "method": "cash", "amount_kobo": 40000}
    assert post(path, {**pay, "reference": "DEMO-TEST-A"}).status_code == 400
    reported = post(path, pay)
    assert reported.status_code == 201
    assert reported.json["outstanding_kobo"] == 100000
    assert post(path, pay).status_code == 409
    item = path + "/" + str(reported.json["records"][0]["id"])
    assert post(item + "/receipt", {"receipt_attested": True}).status_code == 409
    reviewed = post(item + "/review", {"decision": "verified",
        "review_note": "Checked the separately retained cash record for this allocation.",
        "review_attested": True})
    assert reviewed.status_code == 200
    assert reviewed.json["outstanding_kobo"] == 60000
    assert post(item + "/receipt", {"receipt_attested": True}).status_code == 201
    assert client.get(item + "/receipt/print").status_code == 200
    assert client.get(job + "/invoice").status_code == 200
    assert client.get(root + "/finance").status_code == 200
    reversed_payment = post(item + "/reverse", {
        "reason": "Recorded a test correction to the original allocation.",
        "reversal_attested": True})
    assert reversed_payment.status_code == 200
    assert reversed_payment.json["outstanding_kobo"] == 100000
    assert client.get(job + "/clinical").status_code == 404
    app.config["PRINCE_LIVE_FINANCE_APPROVED"] = False
    assert client.get(root + "/finance").status_code == 404
