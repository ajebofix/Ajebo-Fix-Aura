"""HTTP-level Prince pilot authorization tests (synthetic identities only)."""
from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import User
from partnerships.prince_models import (
    PrinceAudit, PrinceCustomer, PrinceJob, PrinceJobAssignment, PrinceMembership,
    PrinceTenant, PrinceVehicle, PrinceJobTransition,
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
        stephen = _make_user("Synthetic Co Principal", "5")
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
            PrinceMembership(tenant_id=prince.id, user_id=stephen.id, role="principal", active=True),
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
            "other_email": other.email,
            "principal_email": principal.email,
            "stephen_email": stephen.email,
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


def test_operator_intake_stores_no_private_contacts(pilot, app, client):
    token = _login(client, pilot["seun_email"])
    response = client.post(f"{URL}/intake", json=_intake_payload(),
                           headers={"X-CSRF-Token": token})
    assert response.status_code == 201
    result = response.json
    assert set(result) == {"customer_id", "full_name", "job_id"}
    assert not PRIVATE.intersection(result)
    with app.app_context():
        customer = db.session.get(PrinceCustomer, result["customer_id"])
        assert customer.phone_ciphertext is None
        assert customer.email_ciphertext is None
        assert customer.address_ciphertext is None
        vehicle = PrinceVehicle.query.filter_by(customer_id=customer.id).one()
        assert vehicle.vin is None
    detail = client.get(f"{URL}/jobs/{result['job_id']}")
    assert detail.status_code == 200
    assert detail.json["full_name"] == "Synthetic Owner"
    assert "phone_number" not in detail.json


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
    for prohibited in ("phone_number", "email", "home_address"):
        payload[prohibited] = "Private information"
        assert client.post(f"{URL}/intake", json=payload,
                           headers={"X-CSRF-Token": token}).status_code == 400
        del payload[prohibited]
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


def test_revoked_operator_membership_denies_immediately(pilot, app, client):
    _login(client, pilot["seun_email"])
    assert client.get(f"{URL}/jobs").status_code == 200
    with app.app_context():
        actor = User.query.filter_by(email=pilot["seun_email"]).one()
        membership = PrinceMembership.query.filter_by(user_id=actor.id).one()
        membership.active = False
        db.session.commit()
    assert client.get(f"{URL}/jobs").status_code == 404


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


def test_old_concern_editor_retired_without_destroying_text(pilot, client, app):
    token = _login(client, pilot["seun_email"])
    created = client.post(f"{URL}/intake", json=_intake_payload(),
                          headers={"X-CSRF-Token": token})
    job_id = created.get_json()["job_id"]
    attempted = client.patch(
        f"{URL}/jobs/{job_id}/concern",
        json={"reported_concern": "New text would overwrite"},
        headers={"X-CSRF-Token": token},
    )
    assert attempted.status_code == 410
    with app.app_context():
        job = db.session.get(PrinceJob, job_id)
        assert job.reported_concern == "Cranks, no start; fuel observation unverified"


def test_operator_can_add_multiple_unverified_categories_without_overwrite(pilot, client, app):
    token = _login(client, pilot["seun_email"])
    created = client.post(f"{URL}/intake", json=_intake_payload(),
                          headers={"X-CSRF-Token": token})
    assert created.status_code == 201
    job_id = created.get_json()["job_id"]
    endpoint = f"{URL}/jobs/{job_id}/entries"
    examples = [
        ("customer_complaint", "Vehicle cranks without starting"),
        ("temporary_intervention", "Temporary electrical workaround reportedly applied"),
        ("diagnostic_finding", "Fuel pressure still needs testing"),
        ("reported_work", "Technician reports preliminary inspection only"),
        ("outstanding_concern", "Unresolved cause of no-start"),
        ("next_action", "Check supply voltage and pressure with proper procedure"),
    ]
    for category, body in examples:
        result = client.post(endpoint, json={"category": category, "body": body},
                             headers={"X-CSRF-Token": token})
        assert result.status_code == 201, result.get_data(as_text=True)
        assert result.json["entry"]["review_state"] == "unverified"
        assert result.json["entry"]["category"] == category
        assert result.json["entry"]["body"] == body
    listing = client.get(endpoint)
    assert listing.status_code == 200
    assert [(x["category"], x["body"]) for x in listing.json["entries"]] == examples
    assert "phone_ciphertext" not in listing.get_data(as_text=True)
    with app.app_context():
        job = db.session.get(PrinceJob, job_id)
        assert job.reported_concern == "Cranks, no start; fuel observation unverified"


def test_operator_cannot_read_or_write_unassigned_or_cross_tenant_entries(pilot, client):
    token = _login(client, pilot["seun_email"])
    for job_id in (pilot["unassigned_id"], pilot["cross_id"]):
        endpoint = f"{URL}/jobs/{job_id}/entries"
        assert client.get(endpoint).status_code == 404
        attempted = client.post(endpoint,
                                json={"category": "technician_observation",
                                      "body": "Demo-only observation"},
                                headers={"X-CSRF-Token": token})
        assert attempted.status_code == 404
    assert client.get(f"/pilot/workshops/another-workshop/jobs/{pilot['cross_id']}/entries").status_code == 404


def test_principal_can_append_within_tenant_and_cannot_certify_work(pilot, client, app):
    token = _login(client, pilot["principal_email"])
    endpoint = f"{URL}/jobs/{pilot['unassigned_id']}/entries"
    result = client.post(endpoint,
                         json={"category": "reported_work",
                               "body": "Staff report repairs attempted, not yet verified"},
                         headers={"X-CSRF-Token": token})
    assert result.status_code == 201
    assert result.json["entry"]["review_state"] == "unverified"
    assert client.get(f"{URL}/jobs/{pilot['cross_id']}/entries").status_code == 404
    assert client.post(endpoint, json={
        "category": "reported_work", "body": "This is a test statement",
        "review_state": "verified",
    }, headers={"X-CSRF-Token": token}).status_code == 400


def test_job_entries_validate_category_content_and_csrf(pilot, client):
    token = _login(client, pilot["seun_email"])
    created = client.post(f"{URL}/intake", json=_intake_payload(),
                          headers={"X-CSRF-Token": token})
    endpoint = f"{URL}/jobs/{created.json['job_id']}/entries"
    for payload in (
        {"category": "repair_completed", "body": "Technician completed everything"},
        {"category": "technician_observation", "body": "Please call +2348012345678"},
        {"category": "technician_observation", "body": "Email private@example.test"},
        {"category": "technician_observation", "body": "No"},
        {"category": "technician_observation", "body": "Clean observation", "secret": "leak"},
    ):
        assert client.post(endpoint, json=payload,
                           headers={"X-CSRF-Token": token}).status_code == 400
    assert client.post(endpoint, json={
        "category": "next_action", "body": "Next inspection pending"
    }).status_code == 400
    assert client.get(endpoint).json["entries"] == []


def test_operator_cannot_append_when_job_is_closed(pilot, app, client):
    with app.app_context():
        job = db.session.get(PrinceJob, pilot["unassigned_id"])
        job.status = "closed"
        db.session.commit()
    token = _login(client, pilot["principal_email"])
    response = client.post(
        f"{URL}/jobs/{pilot['unassigned_id']}/entries",
        json={"category": "next_action", "body": "Test follow-up"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("principal_key", ["principal_email", "stephen_email"])
def test_both_principals_independently_manage_encrypted_contacts(pilot, app, client, principal_key):
    token = _login(client, pilot[principal_key])
    detail = client.get(f"{URL}/jobs/{pilot['unassigned_id']}").json
    customer_id = detail["customer_id"]
    endpoint = f"{URL}/customers/{customer_id}/private"
    assert client.get(endpoint).json["phone_number"] is None
    update = client.patch(endpoint, json={
        "phone_number": "+2348000000000",
        "email": "private@example.test",
        "home_address": "Private Test Address",
    }, headers={"X-CSRF-Token": token})
    assert update.status_code == 200
    assert set(update.json) == {"customer_id", "updated"}
    with app.app_context():
        customer = db.session.get(PrinceCustomer, customer_id)
        assert customer.phone_ciphertext and "+2348000000000" not in customer.phone_ciphertext
        assert customer.email_ciphertext and "private@example.test" not in customer.email_ciphertext
        assert customer.address_ciphertext and "Private Test Address" not in customer.address_ciphertext
    private = client.get(endpoint)
    assert private.status_code == 200
    assert private.headers["Cache-Control"].startswith("no-store")
    assert private.json["phone_number"] == "+2348000000000"
    assert private.json["email"] == "private@example.test"
    assert private.json["home_address"] == "Private Test Address"
    change = client.patch(endpoint, json={"email": "updated-private@example.test"},
                          headers={"X-CSRF-Token": token})
    assert change.status_code == 200
    assert client.get(endpoint).json["email"] == "updated-private@example.test"


def test_operator_cannot_read_or_write_customer_contacts(pilot, client):
    token = _login(client, pilot["seun_email"])
    with client.application.app_context():
        customer_id = PrinceCustomer.query.filter_by(full_name="Unassigned Synthetic").one().id
    endpoint = f"{URL}/customers/{customer_id}/private"
    assert client.get(endpoint).status_code == 404
    assert client.patch(endpoint, json={"phone_number": "+2348012345678"},
                        headers={"X-CSRF-Token": token}).status_code == 404


def test_principals_cannot_access_other_tenant_contacts(pilot, app, client):
    _login(client, pilot["principal_email"])
    with app.app_context():
        cross = PrinceCustomer.query.filter_by(full_name="Cross Tenant Synthetic").one()
        cross_id = cross.id
    assert client.get(f"{URL}/customers/{cross_id}/private").status_code == 404


def test_private_endpoint_blocks_unapproved_fields_and_missing_csrf(pilot, client):
    token = _login(client, pilot["principal_email"])
    customer_id = client.get(f"{URL}/jobs/{pilot['unassigned_id']}").json["customer_id"]
    endpoint = f"{URL}/customers/{customer_id}/private"
    assert client.patch(endpoint, json={"email": "bad@example.test"}).status_code == 400
    assert client.patch(endpoint, json={"bank_pin": "0000"},
                        headers={"X-CSRF-Token": token}).status_code == 400
    assert client.patch(endpoint, json={"email": False},
                        headers={"X-CSRF-Token": token}).status_code == 400


@pytest.mark.parametrize("principal_key", ["principal_email", "stephen_email"])
def test_principal_work_attestation_separate_from_staff_note(pilot, client, principal_key):
    token = _login(client, pilot[principal_key])
    job_id = pilot["unassigned_id"]
    entry_response = client.post(
        f"{URL}/jobs/{job_id}/entries",
        json={"category": "reported_work",
              "body": "Technician reports repair and test drive pending review"},
        headers={"X-CSRF-Token": token},
    )
    assert entry_response.status_code == 201
    entry_id = entry_response.json["entry"]["id"]
    review_url = f"{URL}/jobs/{job_id}/entries/{entry_id}/review"
    invalid = client.post(review_url, json={
        "decision": "work_confirmed", "basis": "not_applicable",
        "review_note": "Reviewed, no actual inspection",
    }, headers={"X-CSRF-Token": token})
    assert invalid.status_code == 400

    reviewed = client.post(review_url, json={
        "decision": "work_confirmed", "basis": "direct_inspection",
        "review_note": "Personally inspected and confirmed the reported work",
    }, headers={"X-CSRF-Token": token})
    assert reviewed.status_code == 201
    assert reviewed.json["principal_review"]["decision"] == "work_confirmed"
    repeat = client.post(review_url, json={
        "decision": "not_confirmed", "basis": "not_applicable",
        "review_note": "Trying to overwrite prior review",
    }, headers={"X-CSRF-Token": token})
    assert repeat.status_code == 409
    entries = client.get(f"{URL}/jobs/{job_id}/entries").json["entries"]
    assert len(entries) == 1
    assert entries[0]["review_state"] == "unverified"
    assert entries[0]["principal_review"]["decision"] == "work_confirmed"
    assert entries[0]["principal_review"]["review_note"].startswith("Personally inspected")

    assert entries[0]["body"] == "Technician reports repair and test drive pending review"


def test_operator_cannot_review_own_entry(pilot, client):
    token = _login(client, pilot["seun_email"])
    onboard = client.post(f"{URL}/intake", json=_intake_payload(),
                          headers={"X-CSRF-Token": token})
    job_id = onboard.json["job_id"]
    entry = client.post(
        f"{URL}/jobs/{job_id}/entries",
        json={"category": "temporary_intervention",
              "body": "Reported temporary electrical workaround, not repair"},
        headers={"X-CSRF-Token": token},
    )
    entry_id = entry.json["entry"]["id"]
    review_url = f"{URL}/jobs/{job_id}/entries/{entry_id}/review"
    assert client.post(review_url, json={
        "decision": "work_confirmed", "basis": "direct_inspection",
        "review_note": "Operator claims to confirm work",
    }, headers={"X-CSRF-Token": token}).status_code == 404


def test_principal_cannot_confirm_nonwork_category(pilot, client):
    token = _login(client, pilot["principal_email"])
    job_id = pilot["unassigned_id"]
    entry = client.post(
        f"{URL}/jobs/{job_id}/entries",
        json={"category": "temporary_intervention",
              "body": "Reported temporary electrical workaround, not repair"},
        headers={"X-CSRF-Token": token},
    )
    assert entry.status_code == 201
    entry_id = entry.json["entry"]["id"]
    review_url = f"{URL}/jobs/{job_id}/entries/{entry_id}/review"
    assert client.post(review_url, json={
        "decision": "work_confirmed", "basis": "direct_inspection",
        "review_note": "An observation is not verified work",
    }, headers={"X-CSRF-Token": token}).status_code == 400
    acknowledged = client.post(review_url, json={
        "decision": "needs_evidence", "basis": "not_applicable",
        "review_note": "Need further evidence for the temporary intervention",
    }, headers={"X-CSRF-Token": token})
    assert acknowledged.status_code == 201
    assert client.get(f"{URL}/jobs/{job_id}/entries").json["entries"][0][
        "principal_review"]["review_note"].startswith("Need further evidence")


@pytest.mark.parametrize("principal_key", ["principal_email", "stephen_email"])
def test_principal_management_console_is_tenant_scoped(pilot, client, principal_key):
    _login(client, pilot[principal_key])
    response = client.get(f"{URL}/manage")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Principal management console" in page
    assert "Unassigned Synthetic" in page
    assert "Cross Tenant Synthetic" not in page
    assert "Job assignments" in page
    assert "Statements awaiting review" in page
    assert "Synthetic Operator" in page
    assert "Synthetic Other Operator" in page
    assert "phone_ciphertext" not in page
    assert "home_address" not in page


def test_operator_cannot_open_management_console(pilot, client):
    _login(client, pilot["seun_email"])
    assert client.get(f"{URL}/manage").status_code == 404


def test_principal_reassignment_revokes_old_operator_and_grants_new(pilot, app, client):
    token = _login(client, pilot["principal_email"])
    with app.app_context():
        seun = User.query.filter_by(email=pilot["seun_email"]).one()
        other = User.query.filter_by(name="Synthetic Other Operator").one()
        seun_id, other_id = seun.id, other.id
    job_id = pilot["unassigned_id"]
    endpoint = f"{URL}/jobs/{job_id}/assignment"
    response = client.post(endpoint, json={
        "assignee_user_id": seun_id,
        "expected_assignee_user_id": other_id,
        "reason": "Operator available to continue inspection",
    }, headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    assert response.json["previous_assignee_user_id"] == other_id
    assert response.json["assigned_user_id"] == seun_id
    assert response.json["assigned_operator_name"] == "Synthetic Operator"
    assert "phone_number" not in str(response.json)
    with app.app_context():
        record = db.session.get(PrinceJob, job_id)
        assert record.assigned_user_id == seun_id
        events = PrinceJobAssignment.query.filter_by(job_id=job_id).all()
        assert len(events) == 1
        assert events[0].previous_user_id == other_id
        assert events[0].new_user_id == seun_id
        assert events[0].reason == "Operator available to continue inspection"
        assert PrinceAudit.query.filter_by(
            action="job_assignment_changed", target_id=job_id,
        ).count() == 1
    assert client.get(f"{URL}/jobs/{job_id}").status_code == 200

    # Operator visibility is exercised in a separate, clean login fixture.



@pytest.mark.parametrize(
    ("operator_key", "expected_code"),
    [("seun_email", 200), ("other_email", 404)],
)
def test_operator_visibility_immediately_reflects_current_assignment(
    pilot, app, client, operator_key, expected_code,
):
    with app.app_context():
        seun_id = User.query.filter_by(email=pilot["seun_email"]).one().id
        job = db.session.get(PrinceJob, pilot["unassigned_id"])
        job.assigned_user_id = seun_id
        db.session.commit()
    _login(client, pilot[operator_key])
    assert client.get(
        f"{URL}/jobs/{pilot['unassigned_id']}"
    ).status_code == expected_code

def test_principal_assignment_rejects_stale_or_duplicate_request(pilot, app, client):
    token = _login(client, pilot["stephen_email"])
    with app.app_context():
        seun_id = User.query.filter_by(email=pilot["seun_email"]).one().id
        other_id = User.query.filter_by(name="Synthetic Other Operator").one().id
    endpoint = f"{URL}/jobs/{pilot['unassigned_id']}/assignment"
    payload = {
        "assignee_user_id": seun_id,
        "expected_assignee_user_id": other_id,
        "reason": "Reallocate due to available specialist capacity",
    }
    assert client.post(endpoint, json=payload,
                       headers={"X-CSRF-Token": token}).status_code == 200
    assert client.post(endpoint, json=payload,
                       headers={"X-CSRF-Token": token}).status_code == 409
    with app.app_context():
        assert PrinceJobAssignment.query.filter_by(
            job_id=pilot["unassigned_id"]
        ).count() == 1


def test_principal_assignment_rejects_outside_tenant_and_inactive_operator(pilot, app, client):
    token = _login(client, pilot["principal_email"])
    with app.app_context():
        other = User.query.filter_by(name="Synthetic Other Operator").one()
        stranger = User.query.filter_by(name="Synthetic Stranger").one()
        seun = User.query.filter_by(email=pilot["seun_email"]).one()
        other_id, stranger_id, seun_id = other.id, stranger.id, seun.id
    endpoint = f"{URL}/jobs/{pilot['unassigned_id']}/assignment"
    payload = {
        "assignee_user_id": stranger_id,
        "expected_assignee_user_id": other_id,
        "reason": "Assign outside workshop for testing",
    }
    assert client.post(endpoint, json=payload,
                       headers={"X-CSRF-Token": token}).status_code == 404
    with app.app_context():
        actor = User.query.filter_by(email=pilot["seun_email"]).one()
        member = PrinceMembership.query.filter_by(user_id=actor.id).one()
        member.active = False
        db.session.commit()
    payload["assignee_user_id"] = seun_id
    assert client.post(endpoint, json=payload,
                       headers={"X-CSRF-Token": token}).status_code == 404
    with app.app_context():
        assert PrinceJobAssignment.query.filter_by(
            job_id=pilot["unassigned_id"]
        ).count() == 0


def test_assignment_denies_closed_job_and_unapproved_inputs(pilot, app, client):
    token = _login(client, pilot["principal_email"])
    with app.app_context():
        other_id = User.query.filter_by(name="Synthetic Other Operator").one().id
        seun_id = User.query.filter_by(email=pilot["seun_email"]).one().id
    endpoint = f"{URL}/jobs/{pilot['unassigned_id']}/assignment"
    payload = {
        "assignee_user_id": seun_id,
        "expected_assignee_user_id": other_id,
        "reason": "Reassign job to available technician",
    }
    for invalid in [
        {**payload, "reason": "short"},
        {**payload, "reason": "Call +2348012345678 to discuss"},
        {**payload, "assignee_user_id": True},
        {**payload, "assignee_user_id": str(seun_id)},
        {**payload, "extra": "malicious"},
    ]:
        assert client.post(endpoint, json=invalid,
                           headers={"X-CSRF-Token": token}).status_code == 400
    assert client.post(endpoint, json=payload).status_code == 400
    with app.app_context():
        job = db.session.get(PrinceJob, pilot["unassigned_id"])
        job.status = "closed"
        db.session.commit()
    assert client.post(endpoint, json=payload,
                       headers={"X-CSRF-Token": token}).status_code == 409
    with app.app_context():
        assert PrinceJobAssignment.query.filter_by(
            job_id=pilot["unassigned_id"]
        ).count() == 0


def test_operator_cannot_reassign_even_own_job(pilot, app, client):
    token = _login(client, pilot["seun_email"])
    with app.app_context():
        other_id = User.query.filter_by(name="Synthetic Other Operator").one().id
        seun_id = User.query.filter_by(email=pilot["seun_email"]).one().id
    result = client.post(f"{URL}/jobs/{pilot['unassigned_id']}/assignment",
                         json={
                             "assignee_user_id": seun_id,
                             "expected_assignee_user_id": other_id,
                             "reason": "Reassign due to testing restriction",
                         }, headers={"X-CSRF-Token": token})
    assert result.status_code == 404


def test_principal_management_review_queue_excludes_attested_entry(pilot, app, client):
    token = _login(client, pilot["stephen_email"])
    job_id = pilot["unassigned_id"]
    created = client.post(f"{URL}/jobs/{job_id}/entries", json={
        "category": "reported_work",
        "body": "Reported repair is pending principal inspection",
    }, headers={"X-CSRF-Token": token})
    assert created.status_code == 201
    before = client.get(f"{URL}/manage").get_data(as_text=True)
    assert "Statements awaiting review" in before
    assert "Work reportedly performed (not verified)" in before
    entry_id = created.json["entry"]["id"]
    reviewed = client.post(
        f"{URL}/jobs/{job_id}/entries/{entry_id}/review",
        json={
            "decision": "needs_evidence",
            "basis": "not_applicable",
            "review_note": "Please provide additional workshop evidence before approval",
        },
        headers={"X-CSRF-Token": token},
    )
    assert reviewed.status_code == 201
    after = client.get(f"{URL}/manage").get_data(as_text=True)
    assert "No pending principal decisions" in after


def _advance(client, token, job_id, old, new, disposition=None):
    return client.post(
        f"{URL}/jobs/{job_id}/lifecycle",
        json={
            "expected_status": old, "new_status": new,
            "reason": "Record the workshop decision and next appropriate action.",
            "closure_disposition": disposition,
        },
        headers={"X-CSRF-Token": token},
    )


@pytest.mark.parametrize("principal_key", ["principal_email", "stephen_email"])
def test_lifecycle_principal_progression_and_audit(pilot, app, client, principal_key):
    token = _login(client, pilot[principal_key])
    job_id = pilot["unassigned_id"]
    assert _advance(client, token, job_id, "draft", "approved").status_code == 409
    for old, new in [
        ("draft", "awaiting_approval"),
        ("awaiting_approval", "approved"),
        ("approved", "in_progress"),
    ]:
        response = _advance(client, token, job_id, old, new)
        assert response.status_code == 200
        assert response.json["job_status"] == new
        assert _advance(client, token, job_id, old, new).status_code == 409
    assert _advance(
        client, token, job_id, "in_progress", "closed", "follow_up_required",
    ).status_code == 409
    history = client.get(f"{URL}/jobs/{job_id}/lifecycle").json
    assert len(history["history"]) == 3
    with app.app_context():
        assert PrinceJobTransition.query.filter_by(job_id=job_id).count() == 3
        assert PrinceAudit.query.filter_by(action="job_status_changed", target_id=job_id).count() == 3
    assert "Job progression" in client.get(f"{URL}/manage").get_data(as_text=True)


def test_operator_cannot_change_or_read_private_lifecycle(pilot, client):
    token = _login(client, pilot["other_email"])
    job_id = pilot["unassigned_id"]
    assert client.get(f"{URL}/jobs/{job_id}/lifecycle").status_code == 404
    assert _advance(client, token, job_id, "draft", "awaiting_approval").status_code == 404
    assert client.get(f"{URL}/jobs/{job_id}").json["job_status"] == "draft"


def test_lifecycle_cross_tenant_and_csrf_denied(pilot, client):
    token = _login(client, pilot["principal_email"])
    assert _advance(client, token, pilot["cross_id"], "draft", "awaiting_approval").status_code == 404
    assert client.get(f"{URL}/jobs/{pilot['cross_id']}/lifecycle").status_code == 404
    assert client.post(f"{URL}/jobs/{pilot['unassigned_id']}/lifecycle", json={
        "expected_status": "draft", "new_status": "awaiting_approval",
        "reason": "A principal should review the proposed scope.",
        "closure_disposition": None,
    }).status_code == 400


def test_lifecycle_closure_preserves_unresolved_concern(pilot, client):
    token = _login(client, pilot["stephen_email"])
    job_id = pilot["unassigned_id"]
    headers = {"X-CSRF-Token": token}
    for old, new in [
        ("draft", "awaiting_approval"), ("awaiting_approval", "approved"),
        ("approved", "in_progress"),
    ]:
        assert _advance(client, token, job_id, old, new).status_code == 200
    entry = client.post(f"{URL}/jobs/{job_id}/entries", json={
        "category": "outstanding_concern", "body": "Inspection remains necessary.",
    }, headers=headers)
    assert entry.status_code == 201
    entry_id = entry.json["entry"]["id"]
    assert _advance(
        client, token, job_id, "in_progress", "closed", "follow_up_required",
    ).status_code == 409
    reviewed = client.post(f"{URL}/jobs/{job_id}/entries/{entry_id}/review", json={
        "decision": "needs_evidence", "basis": "not_applicable",
        "review_note": "Further evidence is required; inspection remains pending.",
    }, headers=headers)
    assert reviewed.status_code == 201
    assert _advance(
        client, token, job_id, "in_progress", "closed", "no_follow_up_identified",
    ).status_code == 409
    assert _advance(
        client, token, job_id, "in_progress", "closed", "follow_up_required",
    ).status_code == 200
    state = client.get(f"{URL}/jobs/{job_id}/lifecycle").json
    assert state["job_status"] == "closed"
    assert state["next_status"] is None
    assert state["history"][-1]["closure_disposition"] == "follow_up_required"
    record = client.get(f"{URL}/jobs/{job_id}/entries").json["entries"][0]
    assert record["body"] == "Inspection remains necessary."
    assert record["review_state"] == "unverified"
    assert client.post(f"{URL}/jobs/{job_id}/entries", json={
        "category": "next_action", "body": "New note after closure.",
    }, headers=headers).status_code == 403


def test_lifecycle_requires_active_operator_before_start(pilot, app, client):
    token = _login(client, pilot["principal_email"])
    job_id = pilot["unassigned_id"]
    assert _advance(client, token, job_id, "draft", "awaiting_approval").status_code == 200
    assert _advance(client, token, job_id, "awaiting_approval", "approved").status_code == 200
    with app.app_context():
        assigned = db.session.get(PrinceJob, job_id).assigned_user_id
        member = PrinceMembership.query.filter_by(user_id=assigned).one()
        member.active = False
        db.session.commit()
    assert _advance(client, token, job_id, "approved", "in_progress").status_code == 409
