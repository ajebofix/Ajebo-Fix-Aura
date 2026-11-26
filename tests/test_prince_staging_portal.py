"""Synthetic-only route lock and two-factor demo login HTTP contract."""
from datetime import datetime

import pytest

from extensions import db
from models import User
from partnerships.prince_models import (
    PrinceCustomer, PrinceEstimate, PrinceJob, PrinceJobEntry, PrinceMembership, PrinceTenant, PrinceVehicle
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
            ("stephen", "principal"),
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
    assert client.get("/prince-demo").status_code == 503
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


def test_synthetic_staging_rejects_real_contact_entry(stage, client):
    assert _log_in(client).status_code == 303
    assert client.get(P + "/operator").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    base = {
        "full_name": "Demo New Owner",
        "vehicle_brand": "Mercedes-Benz",
        "vehicle_model": "S-Class W221",
        "privacy_notice_version": "synthetic-staging-v1",
        "participation_confirmed": True,
    }
    headers = {"X-CSRF-Token": token}
    ok = client.post(P + "/intake", json=base, headers=headers)
    assert ok.status_code == 201
    assert set(ok.json) == {"customer_id", "full_name", "job_id"}
    real_name = {**base, "full_name": "Unlabeled Owner"}
    assert client.post(P + "/intake", json=real_name, headers=headers).status_code == 400
    real_phone = {**base, "phone_number": "+2348011111111"}
    assert client.post(P + "/intake", json=real_phone, headers=headers).status_code == 400
    real_email = {**base, "email": "realowner@example.com"}
    assert client.post(P + "/intake", json=real_email, headers=headers).status_code == 400
    fake_phone = {**base, "phone_number": "DEMO CONTACT ONLY"}
    assert client.post(P + "/intake", json=fake_phone, headers=headers).status_code == 400


def test_stage_entries_require_fictional_prefix_and_do_not_leak_between_roles(stage, client):
    assert _log_in(client, role="operator-a").status_code == 303
    assert client.get(P + "/operator").status_code == 200
    with client.session_transaction() as sess:
        csrf = sess["_csrf_token"]
    endpoint = f"{P}/jobs/{stage['job_id']}/entries"
    note = {
        "category": "temporary_intervention",
        "body": "Demo: simulated test jumper reportedly installed; unverified",
    }
    saved = client.post(endpoint, json=note,
                        headers={"X-CSRF-Token": csrf})
    assert saved.status_code == 201
    assert saved.json["entry"]["review_state"] == "unverified"
    assert client.post(endpoint, json={
        "category": "reported_work", "body": "Actual repair history from Anambra"
    }, headers={"X-CSRF-Token": csrf}).status_code == 400
    assert client.post(endpoint, json={
        "category": "reported_work",
        "body": "Demo: call owner on +2348012345678"
    }, headers={"X-CSRF-Token": csrf}).status_code == 400
    assert len(client.get(endpoint).json["entries"]) == 1

    assert client.post("/prince-demo/sign-out",
                       data={"csrf_token": csrf}).status_code == 303
    assert _log_in(client, role="operator-b").status_code == 303
    assert client.get(endpoint).status_code == 404
    assert "simulated test jumper" not in client.get(P + "/operator").get_data(as_text=True)


def test_stephen_principal_page_has_private_controls(stage, client):
    assert _log_in(client, role="stephen").status_code == 303
    page = client.get(P + "/operator").get_data(as_text=True)
    assert "Synthetic Principal Owner" in page
    assert "Private customer details" in page
    assert "Load protected contacts" in page
    assert 'name="phone_number"' in page
    assert 'name="email"' in page
    assert 'name="home_address"' in page


def test_operator_page_has_no_private_contact_controls(stage, client):
    assert _log_in(client, role="operator-a").status_code == 303
    page = client.get(P + "/operator").get_data(as_text=True)
    assert "Load protected contacts" not in page
    assert 'name="phone_number"' not in page
    assert 'name="email"' not in page
    assert 'name="home_address"' not in page


@pytest.mark.parametrize("role", ["principal", "stephen"])
def test_each_staging_principal_can_edit_fictional_private_contact(stage, client, role):
    assert _log_in(client, role=role).status_code == 303
    assert client.get(P + "/operator").status_code == 200
    job = client.get(P + f"/jobs/{stage['job_id']}").json
    endpoint = P + f"/customers/{job['customer_id']}/private"
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    edited = client.patch(endpoint, json={
        "phone_number": "DEMO CONTACT ONLY",
        "email": "demo@example.invalid",
        "home_address": "DEMO ADDRESS ONLY",
    }, headers={"X-CSRF-Token": token})
    assert edited.status_code == 200
    assert client.get(endpoint).json["home_address"] == "DEMO ADDRESS ONLY"
    assert client.patch(endpoint, json={"phone_number": "+2348012345678"},
                        headers={"X-CSRF-Token": token}).status_code == 400


def test_operator_staging_private_api_access_is_denied(stage, client):
    assert _log_in(client, role="operator-a").status_code == 303
    assert client.get(P + "/operator").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    from partnerships.prince_models import PrinceJob
    with client.application.app_context():
        customer_id = db.session.get(PrinceJob, stage["job_id"]).customer_id
    endpoint = P + f"/customers/{customer_id}/private"
    assert client.get(endpoint).status_code == 404
    assert client.patch(endpoint, json={"email": "demo@example.invalid"},
                        headers={"X-CSRF-Token": token}).status_code == 404


def test_stage_operator_cannot_review_reported_work(stage, client):
    assert _log_in(client, role="operator-a").status_code == 303
    assert client.get(P + "/operator").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    entry = client.post(
        P + f"/jobs/{stage['job_id']}/entries",
        json={"category": "reported_work",
              "body": "Demo: technician reports a simulated repair"},
        headers={"X-CSRF-Token": token},
    )
    assert entry.status_code == 201
    review_url = P + f"/jobs/{stage['job_id']}/entries/{entry.json['entry']['id']}/review"
    assert client.post(review_url, json={
        "decision": "work_confirmed", "basis": "direct_inspection",
        "review_note": "Demo: I saw the work in this fictional example",
    }, headers={"X-CSRF-Token": token}).status_code == 404


def test_stage_principal_requires_fictional_review_note(stage, client, app):
    with app.app_context():
        job = db.session.get(PrinceJob, stage["job_id"])
        entry = PrinceJobEntry(
            tenant_id=job.tenant_id, job_id=job.id,
            category="reported_work",
            body="Demo: simulated service record",
            review_state="unverified",
            recorded_by_user_id=job.assigned_user_id,
        )
        db.session.add(entry)
        db.session.commit()
        entry_id = entry.id
    assert _log_in(client, role="principal").status_code == 303
    assert client.get(P + "/operator").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    review_url = P + f"/jobs/{stage['job_id']}/entries/{entry_id}/review"
    assert client.post(review_url, json={
        "decision": "work_confirmed", "basis": "direct_inspection",
        "review_note": "Actual inspection of real vehicle",
    }, headers={"X-CSRF-Token": token}).status_code == 400
    approved = client.post(review_url, json={
        "decision": "work_confirmed", "basis": "direct_inspection",
        "review_note": "Demo: simulated inspection confirmed work for training only",
    }, headers={"X-CSRF-Token": token})
    assert approved.status_code == 201
    assert approved.json["principal_review"]["decision"] == "work_confirmed"


@pytest.mark.parametrize("role", ["principal", "stephen"])
def test_staging_principals_land_on_private_management_dashboard(stage, client, role):
    response = _log_in(client, role=role)
    assert response.status_code == 303
    assert response.headers["Location"].endswith(P + "/manage")
    page = client.get(P + "/manage")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Principal management console" in text
    assert "Synthetic Principal Owner" in text
    assert "Job assignments" in text
    assert "phone_ciphertext" not in text
    assert 'name="phone_number"' not in text
    assert "Private Test Address" not in text


@pytest.mark.parametrize("role", ["operator-a", "operator-b"])
def test_staging_operators_cannot_visit_management_dashboard(stage, client, role):
    assert _log_in(client, role=role).status_code == 303
    assert client.get(P + "/manage").status_code == 404
    assert client.get(P + "/operator").status_code == 200


def test_staging_principal_assignment_requires_demo_reason(stage, client, app):
    assert _log_in(client, role="stephen").status_code == 303
    assert client.get(P + "/manage").status_code == 200
    with client.session_transaction() as sess:
        csrf = sess["_csrf_token"]
    with app.app_context():
        old = db.session.get(PrinceJob, stage["job_id"]).assigned_user_id
        target = (
            PrinceMembership.query.filter_by(
                role="operator", active=True,
            ).filter(PrinceMembership.user_id != old).first()
        )
        assert target is not None
        new = target.user_id
    endpoint = P + f"/jobs/{stage['job_id']}/assignment"
    payload = {
        "assignee_user_id": new,
        "expected_assignee_user_id": old,
        "reason": "Assignment from genuine workshop information",
    }
    assert client.post(endpoint, json=payload,
                       headers={"X-CSRF-Token": csrf}).status_code == 400
    payload["reason"] = "Demo: reallocate fictional W221 to an available operator"
    changed = client.post(endpoint, json=payload,
                          headers={"X-CSRF-Token": csrf})
    assert changed.status_code == 200
    assert changed.json["assigned_user_id"] == new
    assert client.post(endpoint, json=payload,
                       headers={"X-CSRF-Token": csrf}).status_code == 409
    assert client.get(P + "/manage").status_code == 200


@pytest.mark.parametrize("role", ["principal", "stephen"])
def test_staging_lifecycle_accepts_only_synthetic_decision(stage, client, role):
    assert _log_in(client, role=role).status_code == 303
    assert client.get(P + "/manage").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    endpoint = P + f"/jobs/{stage['job_id']}/lifecycle"
    payload = {
        "expected_status": "draft", "new_status": "awaiting_approval",
        "reason": "Actual workshop decision without synthetic marker",
        "closure_disposition": None, "follow_up_plan": None,
    }
    headers = {"X-CSRF-Token": token}
    assert client.post(endpoint, json=payload, headers=headers).status_code == 400
    payload["reason"] = "Demo: request a principal review of fictional job scope."
    result = client.post(endpoint, json=payload, headers=headers)
    assert result.status_code == 200
    assert result.json["job_status"] == "awaiting_approval"
    assert client.get(endpoint).json["history"][0]["new_status"] == "awaiting_approval"


def test_staging_operator_cannot_progress_assigned_job(stage, client):
    assert _log_in(client, role="operator-a").status_code == 303
    assert client.get(P + "/operator").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    endpoint = P + f"/jobs/{stage['job_id']}/lifecycle"
    assert client.get(endpoint).status_code == 404
    assert client.post(endpoint, json={
        "expected_status": "draft", "new_status": "awaiting_approval",
        "reason": "Demo: operator attempts unauthorized status change.",
        "closure_disposition": None, "follow_up_plan": None,
    }, headers={"X-CSRF-Token": token}).status_code == 404


def test_stage_estimate_synthetic_inputs_and_principal_approval(stage, client):
    assert _log_in(client, role="stephen").status_code == 303
    assert client.get(P + "/manage").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    endpoint = P + f"/jobs/{stage['job_id']}/estimates"
    headers = {"X-CSRF-Token": token}
    payload = {"expected_version": 0, "discount_kobo": 0, "items": [
        {"category": "labour", "description": "Real workshop labour",
         "quantity": 1, "unit_kobo": 100050},
    ]}
    assert client.post(endpoint, json=payload, headers=headers).status_code == 400
    payload["items"][0]["description"] = "Demo: fictional workshop labour"
    assert client.post(endpoint, json=payload, headers=headers).status_code == 201
    assert client.get(endpoint[:-1]).status_code == 200
    assert client.post(endpoint + "/1/approve", json={
        "approval_note": "Real workshop approval is not allowed here.",
    }, headers=headers).status_code == 400
    assert client.post(endpoint + "/1/approve", json={
        "approval_note": "Demo: reviewed fictional estimate for internal approval.",
    }, headers=headers).status_code == 200
    assert client.get(endpoint).json["versions"][0]["total_kobo"] == 100050


@pytest.fixture
def approved_quotation(stage, app):
    with app.app_context():
        job = db.session.get(PrinceJob, stage["job_id"])
        db.session.add(PrinceEstimate(
            tenant_id=job.tenant_id, job_id=job.id, version=1,
            items=[{"category": "parts", "description": "Demo: obsolete draft",
                    "quantity": 1, "unit_kobo": 100, "line_kobo": 100}],
            subtotal_kobo=100, discount_kobo=0, total_kobo=100,
            created_by_user_id=stage["principal"],
        ))
        db.session.add(PrinceEstimate(
            tenant_id=job.tenant_id, job_id=job.id, version=2,
            items=[{"category": "labour", "description": "Demo: fictional workshop labour",
                    "quantity": 2, "unit_kobo": 1250050, "line_kobo": 2500100}],
            subtotal_kobo=2500100, discount_kobo=100000, total_kobo=2400100,
            created_by_user_id=stage["principal"], approved_by_user_id=stage["principal"],
            approved_at=datetime.utcnow(), approval_note="Demo: internal review note not for quotation.",
        ))
        db.session.commit()
    return P + f"/jobs/{stage['job_id']}/estimates"


@pytest.mark.parametrize("role", ["principal", "stephen"])
def test_approved_print_quotation_principal_only_and_safe(approved_quotation, client, role):
    assert _log_in(client, role=role).status_code == 303
    response = client.get(approved_quotation + "/2/print")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    for required in ("TRAINING SAMPLE", "NOT FOR CUSTOMER ISSUE", "Synthetic Principal Owner",
                     "24,001.00", "25,001.00", "1,000.00", "Prince Automobile Clinic",
                     "Customer authorization has not been recorded"):
        assert required in page
    for forbidden in ("internal review note not for quotation", "obsolete draft",
                      "phone_ciphertext", "home_address", "approved_by_user_id"):
        assert forbidden not in page
    assert "no-store" in response.headers["Cache-Control"]
    assert client.get(approved_quotation + "/1/print").status_code == 404
    assert client.get(approved_quotation + "/999/print").status_code == 404
    assert client.get("/static/css/prince_quotation_print.css").status_code == 200
    assert client.get("/static/js/prince_quotation_print.js").status_code == 200


@pytest.mark.parametrize("role", ["operator-a", "operator-b"])
def test_operator_cannot_print_quotation(approved_quotation, client, role):
    assert _log_in(client, role=role).status_code == 303
    assert client.get(approved_quotation + "/2/print").status_code == 404


def test_public_cannot_print_quotation(approved_quotation, client):
    assert client.get(approved_quotation + "/2/print").status_code == 404


def test_print_requires_approved_estimate(stage, client):
    assert _log_in(client, role="principal").status_code == 303
    assert client.get(P + "/manage").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    endpoint = P + f"/jobs/{stage['job_id']}/estimates"
    payload = {"expected_version": 0, "discount_kobo": 0, "items": [
        {"category": "labour", "description": "Demo: fictional work",
         "quantity": 1, "unit_kobo": 10000},
    ]}
    assert client.post(endpoint, json=payload, headers={"X-CSRF-Token": token}).status_code == 201
    assert client.get(endpoint + "/1/print").status_code == 404
