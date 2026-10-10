"""Invoice and payment receipt publication is approved separately from sending."""
from __future__ import annotations

import re
from unittest.mock import Mock

import pytest

from extensions import db
from services.billing_accounts_delivery import (
    already_delivered, send_accounts_document_via_resend,
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


def test_previously_emailed_invoice_shows_each_new_unreceipted_payment(
    app, client, monkeypatch
):
    uri, owner, advisor, car, doc, entry = _setup(
        app, client, monkeypatch, kind="invoice", published=True,
    )
    payments = [
        {"id":"2e6fffbd-71ee-4360-a863-12e78de63931",
         "invoice_id":doc["id"],"amount":"400000.00",
         "paid_at":"2026-10-10","has_receipt":False,
         "receipt_document_id":None},
        {"id":"75a25e5a-295f-4baf-8149-1814e4e863ff",
         "invoice_id":doc["id"],"amount":"200000.00",
         "paid_at":"2026-10-09","has_receipt":False,
         "receipt_document_id":None},
    ]
    monkeypatch.setattr(
        "services.billing_client_routes.advisor_billing_inventory",
        lambda **kwargs: {
            "state": "linked", "documents": [entry], "payments": payments,
        },
    )
    from models import AdvisorNote
    from services.billing_accounts_delivery import DELIVERY_PREFIX
    import json
    db.session.add(AdvisorNote(
        user_id=owner.id, car_id=car.id, advisor_id=advisor.id,
        note=DELIVERY_PREFIX+json.dumps({
            "event":"submitted","document_id":doc["id"],
            "provider_message_id":"already-submitted-invoice",
        }),
    ))
    db.session.commit()
    view = client.get(uri)
    assert view.status_code == 200
    assert b"Already submitted" in view.data
    assert b"New payment receipts" in view.data
    assert b"400,000.00" in view.data
    assert b"200,000.00" in view.data
    assert b"Billing" in view.data
    assert b"Generate Payment Receipt" in view.data
    assert b"Send Invoice via Resend" not in view.data

    entry["amount"] = doc["total"]
    entry["currency"] = "₦"
    entry["issued"] = "2026-10-09"
    workspace = client.get(f"/admin/cars/{car.id}/billing/workspace")
    assert workspace.status_code == 200
    assert b"New payments awaiting individual receipts" in workspace.data
    assert b"400,000.00" in workspace.data
    assert b"200,000.00" in workspace.data
    assert b"Generate Receipts" in workspace.data


def test_payment_receipt_status_does_not_unlock_original_invoice_resend(
    app, client, monkeypatch
):
    uri, owner, advisor, car, doc, entry = _setup(
        app, client, monkeypatch, kind="invoice", published=True,
    )
    payment = {"id":"2e6fffbd-71ee-4360-a863-12e78de63931",
               "invoice_id":doc["id"],"amount":"400000.00",
               "paid_at":"2026-10-10","has_receipt":True,
               "receipt_document_id":"eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}
    monkeypatch.setattr(
        "services.billing_client_routes.advisor_billing_inventory",
        lambda **kwargs: {"state": "linked", "documents": [entry],
                          "payments": [payment]},
    )
    response = client.get(uri)
    assert response.status_code == 200
    assert b"0 recorded payments" not in response.data
    assert b"No outstanding individual receipt creation is shown" in response.data


def test_policy_reference_and_explicit_partial_payment_receipt_email(app,monkeypatch):
    app.config["MAIL_SUPPRESS_SEND"]=False
    monkeypatch.setenv("RESEND_API_KEY","re_mock")
    sent=[]
    def fake(url,*,json,headers,timeout,allow_redirects):
        sent.append(json)
        return Mock(raise_for_status=lambda:None,json=lambda:{"id":"test_send_id"})
    monkeypatch.setattr("services.billing_accounts_delivery.requests.post",fake)
    receipt={**_receipt(),"receipt_kind":"payment",
             "source_invoice_number":"AJF-INV-2026-1009-001",
             "source_invoice_balance":"65000.00"}
    with app.app_context():
        assert send_accounts_document_via_resend(
            to="client@example.com",customer="Client",vehicle="GLK",
            document=receipt,car_id=3
        )=="test_send_id"
    assert "Current invoice balance: ₦65,000.00" in sent[0]["text"]
    assert "not evidence that the full invoice has been settled" in sent[0]["text"]
    assert "https://ajebofix.com/service-terms" not in sent[0]["text"]
    assert "specifically agreed terms of your job remain applicable" in sent[0]["text"].lower()


def test_final_settlement_only_when_source_invoice_is_reconciled(app,monkeypatch):
    app.config["MAIL_SUPPRESS_SEND"]=False
    monkeypatch.setenv("RESEND_API_KEY","re_mock")
    sent=[]
    def fake(url,*,json,headers,timeout,allow_redirects):
        sent.append(json)
        return Mock(raise_for_status=lambda:None,json=lambda:{"id":"settlement_mock"})
    monkeypatch.setattr("services.billing_accounts_delivery.requests.post",fake)
    final={**_receipt(),"receipt_kind":"consolidated",
           "source_invoice_number":"INV-TEST",
           "source_invoice_balance":"0.00"}
    with app.app_context():
        assert send_accounts_document_via_resend(
            to="client@example.com",customer="Client",vehicle="GLK",
            document=final,car_id=3
        )=="settlement_mock"
        with pytest.raises(BillingBridgeUnavailable,match="Final settlement"):
            send_accounts_document_via_resend(
                to="client@example.com",customer="Client",vehicle="GLK",
                document={**final,"source_invoice_balance":"65000.00"},car_id=3
            )
    assert len(sent)==1
    assert "Final invoice: INV-TEST" in sent[0]["text"]
    assert "Outstanding balance: ₦0.00" in sent[0]["text"]
    assert "not represent an additional payment" in sent[0]["text"]


def test_issued_receipt_review_has_policy_link(app,client,monkeypatch):
    uri, owner, advisor, car, doc, entry=_setup(
        app,client,monkeypatch,kind="receipt",published=False,
    )
    page=client.get(uri)
    assert page.status_code==200
    assert b"Review &amp; Publish Receipt" in page.data


def test_account_statement_is_independent_and_never_resends_invoice(
    app,client,monkeypatch
):
    from models import AdvisorNote
    from services.billing_accounts_delivery import DELIVERY_PREFIX
    import json

    uri, owner, advisor, car, doc, entry = _setup(
        app,client,monkeypatch,kind="invoice",published=True
    )
    db.session.add(AdvisorNote(
        user_id=owner.id,car_id=car.id,advisor_id=advisor.id,
        note=DELIVERY_PREFIX+json.dumps({
            "event":"submitted","document_id":doc["id"],
            "provider_message_id":"existing_invoice_email",
        }),
    ))
    db.session.commit()
    account_emails=[]
    monkeypatch.setattr(
        "services.billing_client_routes.send_account_statement_via_resend",
        lambda **kwargs: account_emails.append(kwargs) or "statement_provider_id",
    )
    monkeypatch.setattr(
        "services.billing_client_routes.send_accounts_document_via_resend",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("Original invoice must not resend")
        ),
    )
    view=client.get(uri)
    assert b"Already submitted" in view.data
    assert b"Send Updated Account Statement" in view.data
    form=_payload(doc,_csrf(view.data),"statement")
    response=client.post(uri,data=form)
    assert response.status_code == 302
    assert len(account_emails)==1
    assert account_emails[0]["document"]["balance"]=="485000.00"
    followup=client.get(uri)
    assert b"balance update for these exact amounts" in followup.data
    assert b"Send Updated Account Statement" not in followup.data

    # A genuinely different payment position may be communicated separately.
    doc["paid"]="300000.00"
    doc["balance"]="385000.00"
    doc["updated_at"]="2026-10-10T19:20:00+00:00"
    fresh=client.get(uri)
    assert b"Send Updated Account Statement" in fresh.data
    assert client.post(uri,data=_payload(doc,_csrf(fresh.data),"statement")).status_code==302
    assert len(account_emails)==2


def test_statement_rejects_ledger_mismatch_before_email(app,monkeypatch):
    from services.billing_accounts_delivery import send_account_statement_via_resend
    app.config["MAIL_SUPPRESS_SEND"]=False
    monkeypatch.setenv("RESEND_API_KEY","re_mock")
    with app.app_context():
        with pytest.raises(BillingBridgeUnavailable,match="inconsistent"):
            send_account_statement_via_resend(
                to="client@example.com",customer="Client",vehicle="GLK",
                document={**_invoice(),"paid":"600000","balance":"65000"},
                car_id=3,
            )
