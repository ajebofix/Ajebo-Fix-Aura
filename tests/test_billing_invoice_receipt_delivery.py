"""Invoice and payment receipt publication is approved separately from sending."""
from __future__ import annotations

import json
import re
import uuid
from unittest.mock import Mock

import pytest

from extensions import db
from models import AdvisorNote
from services.billing_accounts_delivery import (
    DELIVERY_PREFIX, already_delivered, send_accounts_document_via_resend,
)
from services.billing_client_bridge import BillingBridgeUnavailable
from test_rina_chat_cutover import _car, _own, _sign_in, _user

INVOICE_ID = "b053d1ea-3aad-4d40-8677-add3f6eeff3f"
RECEIPT_ID = "75a25e5a-295f-4baf-8149-1814e4e863ff"


def _invoice():
    return {
        "id": INVOICE_ID, "kind": "invoice", "group": "job_record",
        "status": "partially_paid", "number": "AJF-INV-2026-1009-001",
        "job_number": "JOB-2026-003", "billed_to": "Christian Damilola Oyebola",
        "total": "685000.00", "paid": "200000.00", "balance": "485000.00",
        "revision": 1, "currency": "₦",
        "updated_at": "2026-10-09T18:10:48+00:00",
        "scope": "Bodywork, painting and A/C",
        "terms": "₦616,500 upfront, balance before release",
    }


def _receipt():
    return {
        **_invoice(), "id": RECEIPT_ID, "kind": "receipt",
        "number": "AJF-RCP-SAMPLE", "status": "issued",
        "total": "200000.00", "paid": "0.00", "balance": "200000.00",
        "terms": "Acknowledgment of the recorded payment only",
    }


def _setup(app, client, monkeypatch, *, kind="invoice", published=False):
    owner = _user(suffix=964)
    advisor = _user(suffix=965, role="admin")
    car = _car(suffix=964)
    _own(owner=owner,car=car,suffix=964)
    db.session.commit()
    _sign_in(client, advisor)
    document = _invoice() if kind == "invoice" else _receipt()
    entry = {
        "id": document["id"], "kind": kind,
        "number": document["number"], "native_created": True,
        "job_linked": True, "status": document["status"],
        "published": published,
    }
    monkeypatch.setattr(
        "services.billing_client_routes.advisor_billing_inventory",
        lambda **kwargs: {"state": "linked", "documents": [entry]},
    )
    monkeypatch.setattr(
        "services.billing_client_routes.client_billing_document",
        lambda **kwargs: {"document": document, "brand": {}},
    )
    uri = f"/admin/cars/{car.id}/billing/documents/{document['id']}/delivery"
    return uri, owner, advisor, car, document, entry


def _csrf(data):
    found = re.search(rb'name="csrf_token" value="([^"]+)"', data)
    assert found
    return found.group(1).decode("ascii")


def _payload(doc, csrf, action):
    return {
        "csrf_token": csrf, "action": action, "confirmed": "yes",
        "expected_updated_at": doc["updated_at"],
        "expected_total": doc["total"],
        "expected_paid": doc["paid"],
        "expected_status": doc["status"],
    }


def test_invoice_is_separate_review_publish_then_review_send(app, client, monkeypatch):
    uri, owner, advisor, car, doc, entry = _setup(
        app, client, monkeypatch, kind="invoice", published=False,
    )
    published_calls = []
    sent_calls = []
    monkeypatch.setattr(
        "services.billing_client_routes.publish_native_billing_document",
        lambda **kwargs: published_calls.append(kwargs) or "published",
    )
    monkeypatch.setattr(
        "services.billing_client_routes.send_accounts_document_via_resend",
        lambda **kwargs: sent_calls.append(kwargs) or "test_provider_message",
    )
    get = client.get(uri)
    assert get.status_code == 200
    assert b"Review &amp; Publish Invoice" in get.data
    assert b"485,000.00" in get.data
    csrf = _csrf(get.data)

    # Explicit publication does not email and does not record a payment.
    resp = client.post(uri, data=_payload(doc, csrf, "publish"))
    assert resp.status_code == 302
    assert len(published_calls) == 1
    assert sent_calls == []
    entry["published"] = True

    get = client.get(uri)
    assert b"Review &amp; Send Invoice" in get.data
    resp = client.post(uri, data=_payload(doc, _csrf(get.data), "send"))
    assert resp.status_code == 302
    assert len(sent_calls) == 1
    assert sent_calls[0]["document"]["paid"] == "200000.00"
    assert already_delivered(
        car_id=car.id, owner_user_id=owner.id, document_id=doc["id"],
    )
    assert b"Already submitted" in client.get(uri).data


def test_receipt_requires_distinct_approval(app, client, monkeypatch):
    uri, owner, advisor, car, doc, entry = _setup(
        app, client, monkeypatch, kind="receipt", published=False,
    )
    sent = []
    monkeypatch.setattr(
        "services.billing_client_routes.publish_native_billing_document",
        lambda **kwargs: "published",
    )
    monkeypatch.setattr(
        "services.billing_client_routes.send_accounts_document_via_resend",
        lambda **kwargs: sent.append(kwargs) or "receipt_message",
    )
    view = client.get(uri)
    assert b"Review &amp; Publish Receipt" in view.data
    bad = _payload(doc, _csrf(view.data), "send")
    assert client.post(uri, data=bad).status_code == 302
    assert not sent
    assert client.post(uri, data=_payload(doc, _csrf(client.get(uri).data), "publish")).status_code == 302
    entry["published"] = True
    view = client.get(uri)
    assert b"Review &amp; Send Receipt" in view.data
    assert client.post(uri, data=_payload(doc, _csrf(view.data), "send")).status_code == 302
    assert len(sent) == 1


def test_stale_amount_blocks_email_before_provider(app, client, monkeypatch):
    uri, owner, advisor, car, doc, entry = _setup(
        app, client, monkeypatch, published=True,
    )
    monkeypatch.setattr(
        "services.billing_client_routes.send_accounts_document_via_resend",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Must not send")),
    )
    view = client.get(uri)
    fields = _payload(doc, _csrf(view.data), "send")
    fields["expected_paid"] = "0.00"
    assert client.post(uri,data=fields).status_code == 302


def test_owner_cannot_open_advisor_delivery(app,client):
    owner=_user(suffix=966)
    car=_car(suffix=966)
    _own(owner=owner,car=car,suffix=966)
    db.session.commit()
    _sign_in(client,owner)
    assert client.get(
        f"/admin/cars/{car.id}/billing/documents/{INVOICE_ID}/delivery"
    ).status_code == 403


def test_send_invoice_discloses_actual_balance_not_90_percent(app,monkeypatch):
    app.config["MAIL_SUPPRESS_SEND"]=False
    monkeypatch.setenv("RESEND_API_KEY","re_fake")
    captured=[]
    def fake(url, *, json,headers,timeout,allow_redirects):
        captured.append(json)
        return Mock(raise_for_status=lambda:None,json=lambda:{"id":"email_verified"})
    monkeypatch.setattr("services.billing_accounts_delivery.requests.post",fake)
    with app.app_context():
        assert send_accounts_document_via_resend(
            to="owner@example.com",customer="Christian",vehicle="GLK 350",
            document=_invoice(),car_id=3,
        )=="email_verified"
        assert send_accounts_document_via_resend(
            to="owner@example.com",customer="Christian",vehicle="GLK 350",
            document=_receipt(),car_id=3,
        )=="email_verified"
    assert "₦200,000.00" in captured[0]["text"]
    assert "₦485,000.00" in captured[0]["text"]
    assert "Outstanding balance" in captured[0]["text"]
    assert "Payment acknowledged: ₦200,000.00" in captured[1]["text"]
    assert "Outstanding balance" not in captured[1]["text"]


def test_send_rejects_wrong_partpayment_status(app,monkeypatch):
    app.config["MAIL_SUPPRESS_SEND"] = False
    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    doc = {**_invoice(),"paid":"0.00","balance":"685000.00"}
    with app.app_context():
        with pytest.raises(BillingBridgeUnavailable,match="Partial payment"):
            send_accounts_document_via_resend(
                to="owner@example.com",customer="Christian",
                vehicle="GLK",document=doc,car_id=3,
            )
