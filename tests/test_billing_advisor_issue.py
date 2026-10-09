"""Advisor Review & Issue preserves a separate, opt-in send step."""
from __future__ import annotations

from unittest.mock import Mock
import re

import pytest

from extensions import db
from services.billing_client_bridge import BillingBridgeUnavailable
from services.billing_accounts_delivery import approve_native_estimate_issue
from test_rina_chat_cutover import _car, _own, _sign_in, _user


DOC_ID = "4338411f-5b6a-4632-8ff2-7c32ef8c9d24"


def draft():
    return {
        "id": DOC_ID, "kind": "estimate", "status": "draft",
        "group": "job_record", "number": "AJF-EST-2026-1009-001",
        "billed_to": "Christian Damilola Oyebola",
        "job_number": "JOB-2026-003", "revision": 3,
        "total": "685000.00", "currency": "₦",
        "updated_at": "2026-10-09T15:00:00+00:00",
        "terms": "90% (₦616,500) upfront and 10% (₦68,500) balance",
        "valid_until": "2026-10-16", "scope": "Collision restoration and A/C service",
    }


def test_owner_cannot_review_or_issue(app, client):
    owner = _user(suffix=981)
    car = _car(suffix=981)
    _own(owner=owner, car=car, suffix=981)
    db.session.commit()
    _sign_in(client, owner)
    uri = f"/admin/cars/{car.id}/billing/estimate/{DOC_ID}/issue"
    assert client.get(uri).status_code == 403
    # CSRF middleware rejects unauthorised POST requests before the view.
    assert client.post(uri, data={"confirmed": "yes"}).status_code in {400, 403}


def test_advisor_draft_preview_and_explicit_approval(app, client, monkeypatch):
    owner = _user(suffix=982)
    advisor = _user(suffix=983, role="admin")
    car = _car(suffix=982)
    _own(owner=owner, car=car, suffix=982)
    db.session.commit()
    _sign_in(client, advisor)
    uri = f"/admin/cars/{car.id}/billing/estimate/{DOC_ID}/issue"
    monkeypatch.setattr(
        "services.billing_client_routes.client_billing_document",
        lambda **kwargs: {"document": draft(), "brand": {}},
    )
    issued = []
    monkeypatch.setattr(
        "services.billing_client_routes.approve_native_estimate_issue",
        lambda **kwargs: issued.append(kwargs) or True,
    )
    response = client.get(uri)
    assert response.status_code == 200
    assert b"Confirm &amp; Issue Estimate" in response.data
    assert b"616,500" in response.data and b"68,500" in response.data
    token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert token is not None
    csrf = token.group(1).decode("ascii")

    # Missing approval cannot mutate Billing.
    response = client.post(uri, data={
        "csrf_token": csrf,
        "expected_total": draft()["total"],
        "expected_revision": "3",
        "expected_updated_at": draft()["updated_at"],
    })
    assert response.status_code == 302
    assert issued == []

    # Stale source timestamp cannot mutate Billing.
    response = client.post(uri, data={
        "csrf_token": csrf,
        "confirmed": "yes", "expected_total": draft()["total"],
        "expected_revision": "3",
        "expected_updated_at": "2026-10-08T15:00:00+00:00",
    })
    assert response.status_code == 302
    assert issued == []

    response = client.post(uri, data={
        "csrf_token": csrf,
        "confirmed": "yes", "expected_total": draft()["total"],
        "expected_revision": "3",
        "expected_updated_at": draft()["updated_at"],
    })
    assert response.status_code == 302
    assert response.location.endswith(f"/admin/cars/{car.id}/billing/workspace")
    assert len(issued) == 1
    assert issued[0]["document"]["status"] == "draft"


def test_signed_issue_helper_uses_gateway_and_does_not_send(app, monkeypatch):
    monkeypatch.setenv(
        "AURA_BILLING_BRIDGE_URL",
        "https://odtctmjhkcphyaozpcup.supabase.co/functions/v1/aura-billing-bridge",
    )
    calls = []

    def mock_post(url, *, data, headers, timeout, allow_redirects):
        import json
        payload = json.loads(data)
        calls.append((url, payload))
        assert "X-Aura-Signature" in headers
        return Mock(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"state": "issued", "document_id": DOC_ID},
        )

    monkeypatch.setattr("services.billing_accounts_delivery.requests.post", mock_post)
    with app.app_context():
        assert approve_native_estimate_issue(
            car_id=3, owner_user_id=9, advisor_user_id=1,
            vin="WDCGG5HB1EG276273", document=draft(),
        ) is True
    assert len(calls) == 1
    assert calls[0][1]["action"] == "issue_document"
    assert calls[0][1]["expected_total"] == "685000.00"
    assert calls[0][1]["expected_updated_at"] == draft()["updated_at"]


def test_invalid_pilot_terms_block_issue_before_network(app, monkeypatch):
    monkeypatch.setattr(
        "services.billing_accounts_delivery.requests.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("No network for invalid terms")
        ),
    )
    bad = {**draft(), "terms": "90% (₦585,000) and 10% (₦65,000)"}
    with app.app_context():
        with pytest.raises(BillingBridgeUnavailable):
            approve_native_estimate_issue(
                car_id=3, owner_user_id=9, advisor_user_id=1,
                vin="WDCGG5HB1EG276273", document=bad,
            )
