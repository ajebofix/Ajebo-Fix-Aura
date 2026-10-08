"""Native Billing publication gate and Resend accounts send contracts."""
from __future__ import annotations

import json
import uuid

import pytest
import requests

from extensions import db
from models import AdvisorNote
from services.billing_accounts_delivery import (
    DELIVERY_PREFIX,
    already_delivered,
    publish_native_billing_estimate,
    send_accounts_estimate_via_resend,
)
from services.billing_client_bridge import BillingBridgeUnavailable
from test_rina_chat_cutover import _car, _own, _sign_in, _user


class FakeResponse:
    def __init__(self, status=200, result=None):
        self.status_code = status
        self.result = result or {}

    def raise_for_status(self):
        if not 200 <= self.status_code < 300:
            raise requests.HTTPError("private provider failure")

    def json(self):
        return self.result


DOC_ID = "33333333-3333-4333-8333-333333333333"


def _document():
    return {
        "id": DOC_ID,
        "kind": "estimate",
        "group": "job_record",
        "status": "sent",
        "job_number": "JOB-2026-003",
        "number": "AJF-EST-2026-1009-001",
        "billed_to": "Christian Damilola Oyebola",
        "total": "650000",
        "revision": 2,
        "terms": (
            "An upfront mobilisation payment of 90% (₦585,000) "
            "is payable. The 10% (₦65,000) balance is due "
            "after final inspection and before vehicle release."
        ),
    }


def test_resend_template_submit_contains_verified_document_link(
    app, monkeypatch
):
    app.config["MAIL_SUPPRESS_SEND"] = False
    monkeypatch.setenv("RESEND_API_KEY", "re_fake_value")
    captured = []

    def fake_request(url, *, json, headers, timeout, allow_redirects):
        captured.append((url, json, headers, timeout, allow_redirects))
        return FakeResponse(result={"id": "email_verified_123"})

    monkeypatch.setattr(
        "services.billing_accounts_delivery.requests.post", fake_request,
    )
    with app.app_context():
        message_id = send_accounts_estimate_via_resend(
            to="owner@example.com",
            customer="Client from Billing",
            vehicle="Mercedes-Benz",
            document=_document(),
            car_id=3,
            upfront_percentage=90,
        )
    assert message_id == "email_verified_123"
    endpoint, payload, headers, timeout, redirects = captured[0]
    assert endpoint == "https://api.resend.com/emails"
    assert payload["from"] == "Ajebo Fix Accounts <accounts@updates.ajebofix.com>"
    assert payload["template"]["id"] == "ajebo-accounts-secure-document"
    variables = payload["template"]["variables"]
    assert variables["TOTAL_AMOUNT"] == "₦650,000.00"
    assert variables["UPFRONT_AMOUNT"] == "₦585,000.00"
    assert variables["BALANCE_AMOUNT"] == "₦65,000.00"
    assert variables["DOCUMENT_URL"] == (
        f"https://aura.ajebofix.com/cars/3/billing/documents/{DOC_ID}"
    )
    assert payload["to"] == ["owner@example.com"]
    assert "Authorization" in headers
    assert "Idempotency-Key" in headers
    assert redirects is False


def test_resend_send_fails_before_network_for_mismatched_source_terms(
    app, monkeypatch
):
    app.config["MAIL_SUPPRESS_SEND"] = False
    monkeypatch.setenv("RESEND_API_KEY", "re_fake_value")

    def forbidden(*args, **kwargs):
        raise AssertionError("No network send for unapproved native terms")

    monkeypatch.setattr(
        "services.billing_accounts_delivery.requests.post", forbidden,
    )
    wrong = dict(_document())
    wrong["terms"] = "Payment after vehicle collection."
    with app.app_context():
        with pytest.raises(BillingBridgeUnavailable):
            send_accounts_estimate_via_resend(
                to="owner@example.com", customer="Client",
                vehicle="GLK", document=wrong, car_id=3,
                upfront_percentage=90,
            )


def test_native_publication_requires_signed_service_acknowledgment(
    app, monkeypatch
):
    monkeypatch.setenv(
        "AURA_BILLING_BRIDGE_URL",
        "https://odtctmjhkcphyaozpcup.supabase.co/functions/v1/aura-billing-bridge",
    )
    seen = []

    def ok(url, *, data, headers, timeout, allow_redirects):
        seen.append(json.loads(data))
        assert headers["X-Aura-Signature"]
        assert "service_role" not in str(headers).lower()
        return FakeResponse(result={"state": "published"})

    monkeypatch.setattr(
        "services.billing_accounts_delivery.requests.post", ok,
    )
    with app.app_context():
        outcome = publish_native_billing_estimate(
            car_id=3, owner_user_id=5, advisor_user_id=1,
            vin="WDCGG5HB1EG276273", document_id=DOC_ID,
        )
    assert outcome == "published"
    assert seen[0]["action"] == "publish_document"
    assert seen[0]["advisor_user_id"] == 1


def test_non_native_billing_provenance_block_does_not_send(
    app, monkeypatch
):
    monkeypatch.setenv(
        "AURA_BILLING_BRIDGE_URL",
        "https://odtctmjhkcphyaozpcup.supabase.co/functions/v1/aura-billing-bridge",
    )

    def refused(*args, **kwargs):
        return FakeResponse(status=409, result={"error": "Native issued estimate required"})

    monkeypatch.setattr(
        "services.billing_accounts_delivery.requests.post", refused,
    )
    with app.app_context():
        with pytest.raises(BillingBridgeUnavailable):
            publish_native_billing_estimate(
                car_id=3, owner_user_id=5, advisor_user_id=1,
                vin="WDCGG5HB1EG276273", document_id=DOC_ID,
            )


def test_aura_client_cannot_use_account_send_action(app, client):
    owner = _user(suffix=906)
    car = _car(suffix=906)
    _own(owner=owner, car=car, suffix=906)
    db.session.commit()
    _sign_in(client, owner)
    assert client.get(
        f"/admin/cars/{car.id}/billing/estimate/{DOC_ID}/send"
    ).status_code == 403


def test_audited_send_prevents_repeated_submission(app):
    owner = _user(suffix=910)
    advisor = _user(suffix=911, role="admin")
    car = _car(suffix=910)
    _own(owner=owner, car=car, suffix=910)
    db.session.add(AdvisorNote(
        user_id=owner.id, car_id=car.id, advisor_id=advisor.id,
        note=DELIVERY_PREFIX + json.dumps({
            "event": "submitted", "document_id": DOC_ID,
            "provider_message_id": "email_verified_123",
        }),
    ))
    db.session.commit()
    assert already_delivered(
        car_id=car.id, owner_user_id=owner.id, document_id=DOC_ID,
    ) is True
    assert already_delivered(
        car_id=car.id, owner_user_id=owner.id,
        document_id=str(uuid.uuid4()),
    ) is False
