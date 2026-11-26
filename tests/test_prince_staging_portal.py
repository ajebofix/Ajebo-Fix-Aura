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
                     "This document does not establish customer authorization"):
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


def _customer_decision(sequence=0, decision="accepted"):
    return {
        "expected_sequence": sequence, "decision": decision,
        "channel": "written_message",
        "evidence_reference": "Demo: fictional customer decision for the exact estimate.",
        "customer_decision_attested": True,
    }


@pytest.mark.parametrize("role", ["principal", "stephen"])
def test_principals_record_customer_decision_and_withdrawal(approved_quotation, client, role):
    assert _log_in(client, role=role).status_code == 303
    assert client.get(P + "/manage").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    headers = {"X-CSRF-Token": token}
    endpoint = approved_quotation + "/2/authorization"
    assert client.get(endpoint).json == {"state": "not_recorded", "events": []}
    payload = _customer_decision()
    assert client.post(endpoint, json={**payload, "customer_decision_attested": False},
                       headers=headers).status_code == 400
    assert client.post(endpoint, json={**payload, "evidence_reference": "Real customer approval"},
                       headers=headers).status_code == 400
    assert client.post(endpoint, json=payload).status_code == 400
    result = client.post(endpoint, json=payload, headers=headers)
    assert result.status_code == 201
    assert result.json["event"]["total_kobo"] == 2400100
    assert result.json["event"]["estimate_version"] == 2
    assert client.post(endpoint, json=payload, headers=headers).status_code == 409
    assert client.post(endpoint, json=_customer_decision(1, "accepted"),
                       headers=headers).status_code == 409
    assert client.post(endpoint, json=_customer_decision(1, "withdrawn"),
                       headers=headers).status_code == 201
    history = client.get(endpoint).json
    assert history["state"] == "withdrawn"
    assert [event["decision"] for event in history["events"]] == ["withdrawn", "accepted"]
    job_path = approved_quotation.rsplit("/", 1)[0]
    assert client.get(job_path).json["job_status"] == "draft"
    assert "Customer authorization" in client.get(job_path + "/estimate").get_data(as_text=True)
    assert "fictional customer decision" not in client.get(
        approved_quotation + "/2/print").get_data(as_text=True)


@pytest.mark.parametrize("role", ["operator-a", "operator-b"])
def test_operator_cannot_record_or_read_customer_authorization(approved_quotation, client, role):
    assert _log_in(client, role=role).status_code == 303
    assert client.get(P + "/operator").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    endpoint = approved_quotation + "/2/authorization"
    assert client.get(endpoint).status_code == 404
    assert client.post(endpoint, json=_customer_decision(),
                       headers={"X-CSRF-Token": token}).status_code == 404


def test_authorization_denies_old_version_and_forced_amount(approved_quotation, client):
    assert _log_in(client, role="principal").status_code == 303
    assert client.get(P + "/manage").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    headers = {"X-CSRF-Token": token}
    assert client.post(approved_quotation + "/1/authorization", json=_customer_decision(),
                       headers=headers).status_code == 404
    endpoint = approved_quotation + "/2/authorization"
    assert client.post(endpoint, json={**_customer_decision(), "total_kobo": 1},
                       headers=headers).status_code == 400
    assert client.post(endpoint, json=_customer_decision(0, "withdrawn"),
                       headers=headers).status_code == 409
    assert client.post(endpoint, json=_customer_decision(0, "declined"),
                       headers=headers).status_code == 201
    assert client.post(endpoint, json=_customer_decision(1, "accepted"),
                       headers=headers).status_code == 201


def test_start_gate_and_withdrawal_work_hold(approved_quotation, client):
    assert _log_in(client, role="stephen").status_code == 303
    assert client.get(P + "/manage").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    headers = {"X-CSRF-Token": token}
    root = approved_quotation.rsplit("/", 1)[0]
    authorization = approved_quotation + "/2/authorization"

    def advance(old, new, disposition=None):
        return client.post(root + "/lifecycle", json={
            "expected_status": old, "new_status": new,
            "reason": "Demo: record the fictional workshop lifecycle decision.",
            "closure_disposition": disposition,
            "follow_up_plan": "Demo: review withdrawal and agree a new scope." if disposition else None,
        }, headers=headers)

    assert advance("draft", "awaiting_approval").status_code == 200
    assert advance("awaiting_approval", "approved").status_code == 200
    assert advance("approved", "in_progress").status_code == 409
    assert client.post(authorization, json=_customer_decision(0, "declined"),
                       headers=headers).status_code == 201
    assert advance("approved", "in_progress").status_code == 409
    assert client.post(authorization, json=_customer_decision(1, "accepted"),
                       headers=headers).status_code == 201
    assert advance("approved", "in_progress").status_code == 200
    assert client.get(root).json["work_authorization"] == "accepted"
    assert client.post(authorization, json=_customer_decision(2, "withdrawn"),
                       headers=headers).status_code == 201
    assert client.get(root).json["work_authorization"] == "withdrawn"
    assert "Work hold:" in client.get(P + "/manage").get_data(as_text=True)
    entry = client.post(root + "/entries", json={
        "category": "reported_work", "body": "Demo: fictional work completed before withdrawal.",
    }, headers=headers)
    assert entry.status_code == 201
    entry_id = entry.json["entry"]["id"]
    assert client.post(root + f"/entries/{entry_id}/review", json={
        "decision": "work_confirmed", "basis": "direct_inspection",
        "review_note": "Demo: fictional inspection of work before withdrawal.",
    }, headers=headers).status_code == 201
    # No-follow-up closure must fail even when the work is confirmed.
    assert client.post(root + "/lifecycle", json={
        "expected_status": "in_progress", "new_status": "closed",
        "reason": "Demo: fictional closure without current authorization.",
        "closure_disposition": "no_follow_up_identified", "follow_up_plan": None,
    }, headers=headers).status_code == 409
    assert advance("in_progress", "closed", "follow_up_required").status_code == 200


def test_operator_sees_work_gate_without_private_evidence(approved_quotation, client, app, stage):
    from partnerships.prince_models import PrinceCustomerAuthorization
    with app.app_context():
        job = db.session.get(PrinceJob, stage["job_id"])
        db.session.add(PrinceCustomerAuthorization(
            tenant_id=job.tenant_id, job_id=job.id, estimate_version=2,
            sequence=1, decision="withdrawn", channel="written_message",
            evidence_reference="Demo: confidential withdrawal reference.",
            total_kobo=2400100, recorded_by_user_id=stage["principal"],
        ))
        job.status = "in_progress"
        db.session.commit()
    assert _log_in(client, role="operator-a").status_code == 303
    root = approved_quotation.rsplit("/", 1)[0]
    data = client.get(root).json
    assert data["work_authorization"] == "withdrawn"
    assert "confidential withdrawal reference" not in str(data)
    page = client.get(P + "/operator").get_data(as_text=True)
    assert "Do not start or continue chargeable work" in page
    assert "confidential withdrawal reference" not in page


def _invoice_payload(sequence=1):
    return {
        "expected_version": 2, "expected_authorization_sequence": sequence,
        "issue_reason": "Demo: fictional billing decision for full accepted scope.",
        "issue_attested": True,
    }


@pytest.mark.parametrize("role", ["principal", "stephen"])
def test_training_invoice_exact_snapshot_and_withdrawal_warning(approved_quotation, client, role):
    assert _log_in(client, role=role).status_code == 303
    assert client.get(P + "/manage").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    headers = {"X-CSRF-Token": token}
    root = approved_quotation.rsplit("/", 1)[0]
    invoice = root + "/invoice"
    auth = approved_quotation + "/2/authorization"
    payload = _invoice_payload()
    assert client.get(invoice + "/data").json == {"invoice": None}
    assert client.post(invoice, json=payload, headers=headers).status_code == 409
    assert client.post(auth, json=_customer_decision(), headers=headers).status_code == 201
    assert client.post(invoice, json=payload, headers=headers).status_code == 409
    for old, new in [("draft", "awaiting_approval"), ("awaiting_approval", "approved")]:
        assert client.post(root + "/lifecycle", json={
            "expected_status": old, "new_status": new,
            "reason": "Demo: fictional approval of workshop scope.",
            "closure_disposition": None, "follow_up_plan": None,
        }, headers=headers).status_code == 200
    assert client.post(invoice, json={**payload, "total_kobo": 1}, headers=headers).status_code == 400
    assert client.post(invoice, json={**payload, "issue_attested": False}, headers=headers).status_code == 400
    assert client.post(invoice, json=_invoice_payload(0), headers=headers).status_code == 409
    assert client.post(invoice, json=payload).status_code == 400
    result = client.post(invoice, json=payload, headers=headers)
    assert result.status_code == 201
    saved = result.json["invoice"]
    assert saved["total_kobo"] == 2400100
    assert saved["subtotal_kobo"] == 2500100
    assert saved["discount_kobo"] == 100000
    assert saved["estimate_version"] == 2
    assert saved["authorization_sequence"] == 1
    assert saved["authorization_current"] is True
    assert saved["reference"].startswith("DEMO-INV-PAC-")
    assert client.post(invoice, json=payload, headers=headers).status_code == 409
    assert client.get(root).json["job_status"] == "approved"
    page = client.get(invoice).get_data(as_text=True)
    assert "24,001.00" in page
    assert "Payment records are fictional" in page
    assert client.post(auth, json=_customer_decision(1, "withdrawn"), headers=headers).status_code == 201
    after = client.get(invoice + "/data").json["invoice"]
    assert after["reference"] == saved["reference"]
    assert after["total_kobo"] == saved["total_kobo"]
    assert after["authorization_current"] is False
    assert "Authorization has changed since issuance" in client.get(invoice).get_data(as_text=True)


@pytest.mark.parametrize("role", ["operator-a", "operator-b"])
def test_operator_cannot_read_or_issue_invoice(approved_quotation, client, role):
    assert _log_in(client, role=role).status_code == 303
    assert client.get(P + "/operator").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    invoice = approved_quotation.rsplit("/", 1)[0] + "/invoice"
    assert client.get(invoice).status_code == 404
    assert client.get(invoice + "/data").status_code == 404
    assert client.post(invoice, json=_invoice_payload(),
                       headers={"X-CSRF-Token": token}).status_code == 404


@pytest.mark.parametrize("role", ["principal", "stephen"])
def test_payment_pending_verified_rejected_and_balance(approved_quotation, client, role):
    assert _log_in(client, role=role).status_code == 303
    assert client.get(P + "/manage").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    headers = {"X-CSRF-Token": token}
    root = approved_quotation.rsplit("/", 1)[0]
    assert client.post(approved_quotation + "/2/authorization",
                       json=_customer_decision(), headers=headers).status_code == 201
    for old, new in [("draft", "awaiting_approval"), ("awaiting_approval", "approved")]:
        assert client.post(root + "/lifecycle", json={
            "expected_status": old, "new_status": new,
            "reason": "Demo: fictional approval of workshop scope.",
            "closure_disposition": None, "follow_up_plan": None,
        }, headers=headers).status_code == 200
    assert client.post(root + "/invoice", json=_invoice_payload(),
                       headers=headers).status_code == 201
    endpoint = root + "/invoice/payments"
    report = {"reference": "DEMO-TRANSFER-A", "method": "transfer", "amount_kobo": 1000050}
    for invalid in [{**report, "amount_kobo": 0}, {**report, "amount_kobo": 1.5},
                    {**report, "reference": "real-bank-reference"},
                    {**report, "decision": "verified"}]:
        assert client.post(endpoint, json=invalid, headers=headers).status_code == 400
    assert client.post(endpoint, json=report).status_code == 400
    pending = client.post(endpoint, json=report, headers=headers)
    assert pending.status_code == 201
    assert pending.json["verified_kobo"] == 0
    assert pending.json["pending_kobo"] == 1000050
    assert pending.json["outstanding_kobo"] == 2400100
    assert client.post(endpoint, json={**report, "reference": "demo-transfer-a"},
                       headers=headers).status_code == 409
    first = pending.json["records"][0]["id"]
    review = {"decision": "verified", "review_note": "Demo: fictional transfer matched to training invoice.",
              "review_attested": True}
    assert client.post(endpoint + f"/{first}/review", json={**review, "review_attested": False},
                       headers=headers).status_code == 400
    verified = client.post(endpoint + f"/{first}/review", json=review, headers=headers)
    assert verified.status_code == 200
    assert verified.json["verified_kobo"] == 1000050
    assert verified.json["outstanding_kobo"] == 1400050
    assert verified.json["status"] == "part_paid"
    assert client.post(endpoint + f"/{first}/review", json=review, headers=headers).status_code == 409
    second = client.post(endpoint, json={**report, "reference": "DEMO-TRANSFER-B",
                                        "amount_kobo": 1400051}, headers=headers)
    assert second.status_code == 201
    second_id = second.json["records"][-1]["id"]
    assert client.post(endpoint + f"/{second_id}/review", json=review, headers=headers).status_code == 409
    rejected = client.post(endpoint + f"/{second_id}/review", json={**review, "decision": "rejected"},
                           headers=headers)
    assert rejected.status_code == 200
    assert rejected.json["outstanding_kobo"] == 1400050
    third = client.post(endpoint, json={**report, "reference": "DEMO-CASH-C", "method": "cash",
                                       "amount_kobo": 1400050}, headers=headers)
    assert third.status_code == 201
    third_id = third.json["records"][-1]["id"]
    settled = client.post(endpoint + f"/{third_id}/review", json=review, headers=headers)
    assert settled.status_code == 200
    assert settled.json["verified_kobo"] == 2400100
    assert settled.json["outstanding_kobo"] == 0
    assert settled.json["pending_kobo"] == 0
    assert settled.json["status"] == "settled"
    assert len(settled.json["records"]) == 3
    assert client.get(endpoint).json == settled.json
    assert client.get(root).json["job_status"] == "approved"
    assert "Payment records and balance" in client.get(root + "/invoice").get_data(as_text=True)


@pytest.mark.parametrize("role", ["operator-a", "operator-b"])
def test_operator_cannot_access_payment_ledger(approved_quotation, client, role):
    assert _log_in(client, role=role).status_code == 303
    assert client.get(P + "/operator").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    endpoint = approved_quotation.rsplit("/", 1)[0] + "/invoice/payments"
    assert client.get(endpoint).status_code == 404
    assert client.post(endpoint, json={"reference": "DEMO-TRANSFER-A", "method": "transfer",
                                      "amount_kobo": 100}, headers={"X-CSRF-Token": token}).status_code == 404
    assert client.post(endpoint + "/1/review", json={"decision": "verified",
                       "review_note": "Demo: fictional unauthorized review attempt.",
                       "review_attested": True}, headers={"X-CSRF-Token": token}).status_code == 404


@pytest.mark.parametrize("role", ["principal", "stephen"])
def test_receipt_and_full_reversal_preserve_history(approved_quotation, client, role):
    assert _log_in(client, role=role).status_code == 303
    assert client.get(P + "/manage").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    headers = {"X-CSRF-Token": token}
    root = approved_quotation.rsplit("/", 1)[0]
    assert client.post(approved_quotation + "/2/authorization",
                       json=_customer_decision(), headers=headers).status_code == 201
    for old, new in [("draft", "awaiting_approval"), ("awaiting_approval", "approved")]:
        assert client.post(root + "/lifecycle", json={
            "expected_status": old, "new_status": new,
            "reason": "Demo: fictional approval of workshop scope.",
            "closure_disposition": None, "follow_up_plan": None,
        }, headers=headers).status_code == 200
    assert client.post(root + "/invoice", json=_invoice_payload(),
                       headers=headers).status_code == 201
    endpoint = root + "/invoice/payments"
    pending = client.post(endpoint, json={"reference": "DEMO-RECEIPT-A",
                          "method": "transfer", "amount_kobo": 1000050}, headers=headers)
    assert pending.status_code == 201
    payment_id = pending.json["records"][0]["id"]
    payment_url = endpoint + f"/{payment_id}"
    receipt_url = payment_url + "/receipt"
    reversal_url = payment_url + "/reverse"
    receipt_payload = {"receipt_attested": True}
    reversal_payload = {"reason": "Demo: fictional correction of an allocation error.",
                        "reversal_attested": True}
    assert client.post(receipt_url, json=receipt_payload, headers=headers).status_code == 409
    assert client.post(reversal_url, json=reversal_payload, headers=headers).status_code == 409
    assert client.get(receipt_url + "/print").status_code == 404
    review = {"decision": "verified", "review_note": "Demo: fictional payment evidence reviewed.",
              "review_attested": True}
    assert client.post(payment_url + "/review", json=review, headers=headers).status_code == 200
    assert client.post(receipt_url, json={"receipt_attested": False},
                       headers=headers).status_code == 400
    issued = client.post(receipt_url, json=receipt_payload, headers=headers)
    assert issued.status_code == 201
    reference = issued.json["reference"]
    assert client.post(receipt_url, json=receipt_payload, headers=headers).status_code == 409
    printed = client.get(receipt_url + "/print")
    assert printed.status_code == 200
    assert "10,000.50" in printed.get_data(as_text=True)
    assert "NOT FOR CUSTOMER ISSUE" in printed.get_data(as_text=True)
    assert "fictional payment evidence reviewed" not in printed.get_data(as_text=True)
    assert "no-store" in printed.headers["Cache-Control"]
    assert client.post(reversal_url, json=reversal_payload).status_code == 400
    reversed_result = client.post(reversal_url, json=reversal_payload, headers=headers)
    assert reversed_result.status_code == 200
    data = reversed_result.json
    assert data["verified_kobo"] == 0
    assert data["reversed_kobo"] == 1000050
    assert data["outstanding_kobo"] == 2400100
    assert data["records"][0]["decision"] == "verified"
    assert data["records"][0]["reversed"] is True
    assert data["records"][0]["receipt_reference"] == reference
    assert client.post(reversal_url, json=reversal_payload, headers=headers).status_code == 409
    assert client.post(receipt_url, json=receipt_payload, headers=headers).status_code == 409
    historical = client.get(receipt_url + "/print").get_data(as_text=True)
    assert "REVERSED" in historical
    assert reference in historical
    replacement = client.post(endpoint, json={"reference": "DEMO-RECEIPT-B",
                              "method": "cash", "amount_kobo": 2400100}, headers=headers)
    replacement_id = replacement.json["records"][-1]["id"]
    assert client.post(endpoint + f"/{replacement_id}/review", json=review,
                       headers=headers).json["outstanding_kobo"] == 0


@pytest.mark.parametrize("role", ["operator-a", "operator-b"])
def test_operator_cannot_access_receipt_or_reverse_payment(approved_quotation, client, role):
    assert _log_in(client, role=role).status_code == 303
    assert client.get(P + "/operator").status_code == 200
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    root = approved_quotation.rsplit("/", 1)[0] + "/invoice/payments/1"
    assert client.get(root + "/receipt/print").status_code == 404
    assert client.post(root + "/receipt", json={"receipt_attested": True},
                       headers={"X-CSRF-Token": token}).status_code == 404
    assert client.post(root + "/reverse", json={"reason": "Demo: unauthorized fictional reversal.",
                       "reversal_attested": True}, headers={"X-CSRF-Token": token}).status_code == 404
