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


def _staff_post(client, path, payload):
    with client.session_transaction() as sess:
        csrf = sess["_csrf_token"]
    return client.post(path, data={**payload, "csrf_token": csrf})


def test_browser_staff_admin_is_separately_gated(live, app, client):
    assert _sign_in(client).status_code == 303
    assert client.get("/prince-staff/accounts").status_code == 404
    app.config["PRINCE_LIVE_STAFF_ADMIN_APPROVED"] = True
    assert client.get("/prince-staff/accounts").status_code == 200
    assert client.get("/pilot/workshops/prince/operator").status_code == 404


def test_principal_creates_staff_and_corrects_own_email(live, app, client):
    from partnerships.prince_models import PrinceAudit
    from security.session_registry import UserSession

    app.config["PRINCE_LIVE_STAFF_ADMIN_APPROVED"] = True
    assert client.get("/prince-staff/accounts").status_code == 404
    assert _sign_in(client).status_code == 303
    assert client.get("/prince-staff/accounts").status_code == 200
    payload = {"name": "Fictional new principal", "email": "new-staff@example.org",
               "role": "principal", "new_password": PASSWORD,
               "confirm_password": PASSWORD, "current_password": PASSWORD,
               "identity_attested": "yes"}
    path = "/prince-staff/accounts/create"
    assert _staff_post(client, path, {**payload, "current_password": "wrong"}).status_code == 403
    assert _staff_post(client, path, {**payload, "identity_attested": ""}).status_code == 400
    assert _staff_post(client, path, payload).status_code == 303
    assert _staff_post(client, path, payload).status_code == 409
    user = User.query.filter_by(email=payload["email"]).one()
    assert user.check_password(PASSWORD)
    assert PrinceMembership.query.filter_by(user_id=user.id).one().role == "principal"
    original = User.query.filter_by(email="staff@example.test").one()
    original_id = original.id
    assert _staff_post(client, "/prince-staff/accounts/email", {
        "email": "corrected-staff@example.org", "identity_attested": "yes",
        "current_password": PASSWORD}).status_code == 303
    assert db.session.get(User, original_id).email == "corrected-staff@example.org"
    assert UserSession.query.filter_by(user_id=original_id, revoked_at=None).count() == 0
    assert PrinceAudit.query.filter_by(target_kind="staff_account").count() == 2
    assert client.get("/prince-staff/accounts").status_code == 404


def test_operator_cannot_create_or_suspend_staff(live, app, client):
    app.config["PRINCE_LIVE_STAFF_ADMIN_APPROVED"] = True
    member = db.session.get(PrinceMembership, live)
    member.role = "operator"
    db.session.commit()
    assert _sign_in(client).status_code == 303
    page = client.get("/prince-staff/accounts")
    assert page.status_code == 200 and b"Staff roster" not in page.data
    assert _staff_post(client, "/prince-staff/accounts/create", {
        "current_password": PASSWORD}).status_code == 404
    assert _staff_post(client, f"/prince-staff/accounts/{live}/suspend", {
        "current_password": PASSWORD}).status_code == 404


def test_suspension_scoped_to_operator_and_revokes_access(live, app, client):
    app.config["PRINCE_LIVE_STAFF_ADMIN_APPROVED"] = True
    assert _sign_in(client).status_code == 303
    assert client.get("/prince-staff/accounts").status_code == 200
    payload = {"name": "Fictional operator", "email": "operator@example.org",
               "role": "operator", "new_password": PASSWORD,
               "confirm_password": PASSWORD, "current_password": PASSWORD,
               "identity_attested": "yes"}
    assert _staff_post(client, "/prince-staff/accounts/create", payload).status_code == 303
    user = User.query.filter_by(email=payload["email"]).one()
    operator = PrinceMembership.query.filter_by(user_id=user.id).one()
    assert _staff_post(client, f"/prince-staff/accounts/{live}/suspend", {
        "current_password": PASSWORD}).status_code == 404
    assert _staff_post(client, f"/prince-staff/accounts/{operator.id}/suspend", {
        "current_password": PASSWORD}).status_code == 303
    assert not db.session.get(PrinceMembership, operator.id).active
    assert PrinceTenant.query.filter_by(slug="prince").one().intake_enabled is False


def test_principal_corrects_another_principals_role(live, app, client):
    from partnerships.prince_models import PrinceAudit
    from security.session_registry import UserSession

    app.config["PRINCE_LIVE_STAFF_ADMIN_APPROVED"] = True
    assert _sign_in(client).status_code == 303
    assert client.get("/prince-staff/accounts").status_code == 200
    payload = {"name": "Fictional second principal", "email": "second@example.org",
               "role": "principal", "new_password": PASSWORD,
               "confirm_password": PASSWORD, "current_password": PASSWORD,
               "identity_attested": "yes"}
    assert _staff_post(client, "/prince-staff/accounts/create", payload).status_code == 303
    user = User.query.filter_by(email=payload["email"]).one()
    user_id = user.id
    target_id = PrinceMembership.query.filter_by(user_id=user_id).one().id
    other = app.test_client()
    # The shared fixture keeps an application context open. Give each
    # simulated browser request its own context, as in real HTTP traffic.
    with app.app_context():
        assert other.get("/prince-staff").status_code == 200
    with app.app_context():
        assert _staff_post(other, "/prince-staff", {
            "email": payload["email"], "password": PASSWORD}).status_code == 303
    with app.app_context():
        assert b"Staff roster" in other.get("/prince-staff/accounts").data
    path = f"/prince-staff/accounts/{target_id}/role"
    change = {"expected_role": "principal", "role": "operator",
              "role_confirmed": "yes", "current_password": PASSWORD}
    assert b"Change staff role" in client.get("/prince-staff/accounts").data
    assert client.post(path, data=change).status_code == 400
    assert _staff_post(client, path, {**change, "current_password": "wrong"}).status_code == 403
    assert _staff_post(client, path, change).status_code == 303
    assert db.session.get(PrinceMembership, target_id).role == "operator"
    assert db.session.get(User, user_id).check_password(PASSWORD)
    assert db.session.get(PrinceMembership, live).role == "principal"
    assert UserSession.query.filter_by(user_id=user_id, revoked_at=None).count() == 0
    assert PrinceAudit.query.filter_by(target_id=user_id,
                                      action="live_staff_demoted_to_operator").count() == 1
    with app.app_context():
        assert other.get("/prince-staff/accounts").status_code != 200
    with app.app_context():
        assert other.get("/prince-staff").status_code == 200
    with app.app_context():
        assert _staff_post(other, "/prince-staff", {
            "email": payload["email"], "password": PASSWORD}).status_code == 303
    with app.app_context():
        page = other.get("/prince-staff/accounts")
        assert page.status_code == 200 and b"Staff roster" not in page.data
    assert _staff_post(client, path, change).status_code == 409
    assert PrinceTenant.query.filter_by(slug="prince").one().intake_enabled is False


def test_staff_role_correction_rejects_self_and_cross_tenant(live, app, client):
    app.config["PRINCE_LIVE_STAFF_ADMIN_APPROVED"] = True
    tenant = PrinceTenant(slug="other", display_name="Other synthetic workshop", active=True)
    outsider = User(name="Fictional outsider", email="outsider@example.org",
                    phone_number="other-synthetic", role="user", is_active=True,
                    email_verified_at=datetime.utcnow())
    outsider.set_password(PASSWORD)
    db.session.add_all([tenant, outsider])
    db.session.flush()
    foreign = PrinceMembership(tenant_id=tenant.id, user_id=outsider.id,
                               role="principal", active=True)
    db.session.add(foreign)
    db.session.commit()
    foreign_id = foreign.id
    assert _sign_in(client).status_code == 303
    assert client.get("/prince-staff/accounts").status_code == 200
    change = {"expected_role": "principal", "role": "operator",
              "role_confirmed": "yes", "current_password": PASSWORD}
    assert _staff_post(client, f"/prince-staff/accounts/{live}/role", change).status_code == 404
    assert _staff_post(client, f"/prince-staff/accounts/{foreign_id}/role", change).status_code == 404
    assert db.session.get(PrinceMembership, foreign_id).role == "principal"
    assert db.session.get(PrinceMembership, live).role == "principal"


def test_operator_cannot_promote_staff(live, app, client):
    app.config["PRINCE_LIVE_STAFF_ADMIN_APPROVED"] = True
    member = db.session.get(PrinceMembership, live)
    member.role = "operator"
    db.session.commit()
    assert _sign_in(client).status_code == 303
    assert client.get("/prince-staff/accounts").status_code == 200
    assert _staff_post(client, f"/prince-staff/accounts/{live}/role", {
        "expected_role": "operator", "role": "principal",
        "role_confirmed": "yes", "current_password": PASSWORD}).status_code == 404
    assert db.session.get(PrinceMembership, live).role == "operator"


def test_live_staff_ux_assets_are_narrowly_public(live, client):
    page = client.get("/prince-staff")
    assert page.status_code == 200
    assert b"Welcome back" in page.data
    assert b"prince_live_staff.css" in page.data
    assert client.get("/static/css/prince_live_staff.css").status_code == 200
    assert client.get("/static/img/prince-workshop-logo-pilot.jpg").status_code == 200
    assert client.get("/static/js/prince_management.js").status_code == 404
    assert client.get("/static/css/prince_management.css").status_code == 404
    assert client.get("/prince-staff/accounts").status_code == 404


def test_staff_ux_defaults_to_operator_and_shows_role_context(live, app, client):
    app.config["PRINCE_LIVE_STAFF_ADMIN_APPROVED"] = True
    assert _sign_in(client).status_code == 303
    landing = client.get("/prince-staff/ready")
    assert b"Customer intake is not open" in landing.data
    assert b"Manage staff and account details" in landing.data
    page = client.get("/prince-staff/accounts")
    assert page.status_code == 200
    assert b'id="roster"' in page.data
    assert b'name="role" value="operator" checked' in page.data
    assert b"Recommended for staff" in page.data
    assert b'id="my-account"' in page.data


def test_workshop_ux_keeps_release_locks_visible(live, app, client, monkeypatch):
    app.config.update(PRINCE_LIVE_WORKSHOP_APPROVED=True,
                      PRINCE_LIVE_STAFF_ADMIN_APPROVED=True)
    monkeypatch.setenv("PRINCE_LIVE_PRIVACY_NOTICE_VERSION", "prince-notice-v1")
    tenant = PrinceTenant.query.filter_by(slug="prince").one()
    tenant.approved_privacy_notice_version = "prince-notice-v1"
    db.session.commit()
    assert _sign_in(client).status_code == 303
    operator = client.get("/pilot/workshops/prince/operator")
    assert operator.status_code == 200
    assert b"live-release-note" in operator.data
    assert b"My account" in operator.data
    assert b"Customer intake is locked" in operator.data
    assert b'id="prince-intake-form"' not in operator.data
    assert client.get("/pilot/workshops/prince/finance").status_code == 404
