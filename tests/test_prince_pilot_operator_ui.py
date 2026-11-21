"""Private mobile operator workspace: route and rendered-data access tests."""
from __future__ import annotations

from extensions import db
from partnerships.prince_models import PrinceCustomer, PrinceJob, PrinceTenant
from tests.test_prince_pilot_api import _intake_payload, _login, pilot  # noqa: F401

URL = "/pilot/workshops/prince"


def test_operator_screen_disabled_without_feature_gate(client):
    assert client.get(f"{URL}/operator").status_code == 404


def test_operator_screen_requires_auth(app, client):
    app.config["AURA_PRINCE_PILOT_API_ENABLED"] = True
    assert client.get(f"{URL}/operator").status_code == 401


def test_operator_screen_shows_only_assigned_jobs(pilot, client):
    _login(client, pilot["seun_email"])
    response = client.get(f"{URL}/operator")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Register a customer" in page
    assert "Assigned vehicle jobs" in page
    assert "Unassigned Synthetic" not in page
    assert "Cross Tenant Synthetic" not in page
    assert "phone_ciphertext" not in page
    assert "email_ciphertext" not in page
    assert 'name="privacy_notice_version"' in page
    assert "pilot-notice-v1" in page
    assert response.headers["Cache-Control"] == "no-store"


def test_operator_screen_shows_only_allowed_customer_name_after_intake(pilot, client):
    token = _login(client, pilot["seun_email"])
    response = client.post(f"{URL}/intake", json=_intake_payload(),
                           headers={"X-CSRF-Token": token})
    assert response.status_code == 201
    page = client.get(f"{URL}/operator?saved=1").get_data(as_text=True)
    assert "Synthetic Owner" in page
    assert "S-Class W221" in page
    assert "Customer saved" in page
    assert "+2348000000000" not in page
    assert "private@example.test" not in page
    assert "Private Test Address" not in page
    assert "Unassigned Synthetic" not in page


def test_workshop_principal_can_view_only_own_tenant_jobs(pilot, client):
    _login(client, pilot["principal_email"])
    page = client.get(f"{URL}/operator").get_data(as_text=True)
    assert "Unassigned Synthetic" in page
    assert "Cross Tenant Synthetic" not in page


def test_intake_form_locked_when_partner_notice_disabled(pilot, app, client):
    with app.app_context():
        tenant = PrinceTenant.query.filter_by(slug="prince").one()
        tenant.intake_enabled = False
        db.session.commit()
    _login(client, pilot["seun_email"])
    page = client.get(f"{URL}/operator").get_data(as_text=True)
    assert "intake is locked" in page
    assert 'id="prince-intake-form"' not in page


def test_operator_response_redacts_pasted_phone_and_email(pilot, app, client):
    with app.app_context():
        job = db.session.get(PrinceJob, pilot["unassigned_id"])
        job.reported_concern = (
            "Vehicle cranks; contact +2348012345678 or private@example.test"
        )
        db.session.commit()
    _login(client, pilot["principal_email"])
    response = client.get(f"{URL}/operator")
    page = response.get_data(as_text=True)
    assert "Vehicle cranks" in page
    assert "private contact redacted" in page
    assert "private@example.test" not in page
    assert "+2348012345678" not in page


def test_intake_rejects_contact_information_in_concern(pilot, client):
    token = _login(client, pilot["seun_email"])
    payload = _intake_payload()
    payload["reported_concern"] = "Call the customer on 08012345678"
    response = client.post(f"{URL}/intake", json=payload,
                           headers={"X-CSRF-Token": token})
    assert response.status_code == 400
    payload["reported_concern"] = "Please email private@example.test"
    response = client.post(f"{URL}/intake", json=payload,
                           headers={"X-CSRF-Token": token})
    assert response.status_code == 400
