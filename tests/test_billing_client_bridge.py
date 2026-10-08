"""Billing owner bridge: verified identity, publication, isolation and outages."""
from __future__ import annotations

import requests
import json

from services.billing_bridge_signing import public_key_document
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import base64

from extensions import db
from services.billing_client_bridge import client_billing_snapshot
from test_rina_chat_cutover import _car, _own, _sign_in, _user


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.status_code = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError("Bridge failure")

    def json(self):
        return self.payload


def _configure(monkeypatch):
    monkeypatch.setenv("AURA_BILLING_CLIENT_VIEW_ENABLED", "true")
    monkeypatch.setenv(
        "AURA_BILLING_BRIDGE_URL",
        "https://odtctmjhkcphyaozpcup.supabase.co/functions/v1/aura-billing-bridge",
    )
    monkeypatch.delenv("AURA_BILLING_BRIDGE_TOKEN", raising=False)
    # It is critical that Aura needs neither Supabase admin keys nor client email.
    monkeypatch.delenv("AURA_BILLING_SUPABASE_SERVICE_ROLE_KEY", raising=False)


def _mock_gateway(monkeypatch, *, state="linked", documents=None, payments=None):
    seen = []

    def mock_post(url, *, headers, data, timeout, allow_redirects):
        assert url == (
            "https://odtctmjhkcphyaozpcup.supabase.co"
            "/functions/v1/aura-billing-bridge"
        )
        assert "Authorization" not in headers
        assert "service_role" not in str(headers).lower()
        assert headers["X-Aura-Key-Id"] == "aura-billing-v1"
        payload = json.loads(data)
        assert "email" not in payload
        assert payload["car_id"] > 0
        assert payload["owner_user_id"] > 0
        assert len(payload["vin"]) == 17
        doc = public_key_document()
        keybytes = base64.urlsafe_b64decode(doc["x"] + "==")
        sig = base64.urlsafe_b64decode(headers["X-Aura-Signature"] + "==")
        signed_bytes = (
            headers["X-Aura-Timestamp"] + "." +
            headers["X-Aura-Nonce"] + "."
        ).encode("ascii") + data
        Ed25519PublicKey.from_public_bytes(keybytes).verify(sig, signed_bytes)
        assert allow_redirects is False
        assert timeout[1] <= 12
        seen.append(payload)
        return FakeResponse({
            "state": state,
            "documents": documents if documents is not None else [],
            "payments": payments if payments is not None else [],
        })

    monkeypatch.setattr("services.billing_client_bridge.requests.post", mock_post)
    return seen


def test_feature_disabled_by_default(app, client, monkeypatch):
    monkeypatch.delenv("AURA_BILLING_CLIENT_VIEW_ENABLED", raising=False)
    owner = _user(suffix=451)
    car = _car(suffix=451)
    _own(owner=owner, car=car, suffix=451)
    db.session.commit()
    _sign_in(client, owner)
    resp = client.get(f"/cars/{car.id}/billing")
    assert resp.status_code == 200
    assert b"connection has not been enabled" in resp.data
    assert b"Client" not in resp.data


def test_verified_owner_without_billing_email_can_view_released_docs(
    app, client, monkeypatch
):
    _configure(monkeypatch)
    owner = _user(suffix=452)
    car = _car(suffix=452)
    _own(owner=owner, car=car, suffix=452)
    db.session.commit()
    seen = _mock_gateway(
        monkeypatch,
        documents=[{
            "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "kind": "invoice", "group": "vehicle_history", "number": "INV-2026-TEST",
            "status": "partially_paid", "issued": "2026-10-08",
            "due": "", "currency": "₦", "total": "150000",
            "paid": "50000", "balance": "100000",
        }, {
            "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "kind": "receipt", "group": "vehicle_history", "number": "RCP-2026-TEST",
            "status": "issued", "issued": "2026-10-08", "due": "",
            "currency": "₦", "total": "50000", "paid": "0", "balance": "50000",
        }],
        payments=[{"amount": "50000", "paid_at": "2026-10-08", "currency": "₦"}],
    )
    _sign_in(client, owner)
    resp = client.get(f"/cars/{car.id}/billing")
    assert resp.status_code == 200
    assert b"INV-2026-TEST" in resp.data
    assert b"RCP-2026-TEST" in resp.data
    assert b"100,000" in resp.data
    assert len(seen) == 1
    assert seen[0]["owner_user_id"] == owner.id
    assert seen[0]["car_id"] == car.id
    assert seen[0]["vin"] == car.vin


def test_linked_vehicle_but_not_published_shows_no_money(app, client, monkeypatch):
    _configure(monkeypatch)
    owner = _user(suffix=453)
    car = _car(suffix=453)
    _own(owner=owner, car=car, suffix=453)
    db.session.commit()
    _mock_gateway(monkeypatch, state="not_published")
    _sign_in(client, owner)
    resp = client.get(f"/cars/{car.id}/billing")
    assert resp.status_code == 200
    assert b"awaiting publication" in resp.data
    assert b"INVOICE" not in resp.data


def test_cross_owner_and_revoked_ownership_denied_before_remote_access(
    app, client, monkeypatch
):
    _configure(monkeypatch)
    owner = _user(suffix=454)
    other = _user(suffix=455)
    car = _car(suffix=454)
    ownership = _own(owner=owner, car=car, suffix=454)
    db.session.commit()

    def forbidden(*args, **kwargs):
        raise AssertionError("Remote Billing request must not happen")

    monkeypatch.setattr("services.billing_client_bridge.requests.post", forbidden)
    _sign_in(client, other)
    assert client.get(f"/cars/{car.id}/billing").status_code == 404
    db.session.delete(ownership)
    db.session.commit()
    _sign_in(client, owner)
    assert client.get(f"/cars/{car.id}/billing").status_code == 404


def test_driver_and_advisor_cannot_use_owner_financial_view(
    app, client, monkeypatch
):
    _configure(monkeypatch)
    driver = _user(suffix=456, role="driver")
    advisor = _user(suffix=457, role="admin")
    car = _car(suffix=456)
    _own(owner=driver, car=car, suffix=456)
    db.session.commit()
    _sign_in(client, driver)
    assert client.get(f"/cars/{car.id}/billing").status_code == 403
    _sign_in(client, advisor)
    assert client.get(f"/cars/{car.id}/billing").status_code == 403


def test_unverified_owner_never_receives_financial_data(
    app, client, monkeypatch
):
    _configure(monkeypatch)
    owner = _user(suffix=458)
    owner.email_verified_at = None
    car = _car(suffix=458)
    _own(owner=owner, car=car, suffix=458)
    db.session.commit()
    _sign_in(client, owner)
    assert client.get(f"/cars/{car.id}/billing").status_code == 403


def test_billing_provider_outage_does_not_leak_details(
    app, client, monkeypatch
):
    _configure(monkeypatch)
    owner = _user(suffix=459)
    car = _car(suffix=459)
    _own(owner=owner, car=car, suffix=459)
    db.session.commit()

    def fail(*args, **kwargs):
        raise requests.Timeout("private service exception")

    monkeypatch.setattr("services.billing_client_bridge.requests.post", fail)
    _sign_in(client, owner)
    resp = client.get(f"/cars/{car.id}/billing")
    assert resp.status_code == 200
    assert b"temporarily unavailable" in resp.data
    assert b"private service exception" not in resp.data


def test_gateway_invalid_state_or_amount_fails_closed(app, monkeypatch):
    from services.billing_client_bridge import BillingBridgeUnavailable

    _configure(monkeypatch)
    _mock_gateway(monkeypatch, state="unexpected")
    car = _car(suffix=460)
    owner = _user(suffix=460)
    db.session.commit()
    try:
        client_billing_snapshot(car_id=car.id, owner_user_id=owner.id, vin=car.vin)
    except BillingBridgeUnavailable:
        pass
    else:
        assert False, "Unrecognised bridge publication state must fail closed"

    _mock_gateway(monkeypatch, documents=[{
        "kind": "invoice", "group": "vehicle_history", "number": "BAD", "status": "paid",
        "currency": "₦", "total": "-5", "paid": "0", "balance": "0",
    }])
    try:
        client_billing_snapshot(car_id=car.id, owner_user_id=owner.id, vin=car.vin)
    except BillingBridgeUnavailable:
        pass
    else:
        assert False, "Invalid provider amount must fail closed"


def test_navigation_only_appears_when_bridge_configured(app, client, monkeypatch):
    _configure(monkeypatch)
    owner = _user(suffix=461)
    car = _car(suffix=461)
    _own(owner=owner, car=car, suffix=461)
    db.session.commit()
    _sign_in(client, owner)
    assert f"/cars/{car.id}/billing".encode() in client.get(f"/cars/{car.id}").data
    monkeypatch.setenv("AURA_BILLING_CLIENT_VIEW_ENABLED", "false")
    assert f"/cars/{car.id}/billing".encode() not in client.get(f"/cars/{car.id}").data


def test_public_verification_key_contains_no_private_material(app, client):
    response = client.get("/.well-known/aura-billing-public-key")
    assert response.status_code == 200
    document = response.get_json()
    assert document["kty"] == "OKP"
    assert document["crv"] == "Ed25519"
    assert document["kid"] == "aura-billing-v1"
    assert len(base64.urlsafe_b64decode(document["x"] + "==")) == 32
    assert "private" not in str(document).lower()
    assert "seed" not in str(document).lower()
    assert "secret" not in str(document).lower()
    assert response.headers["Cache-Control"] == "no-store"


def test_signature_is_body_bound_and_non_reusable(app):
    from services.billing_bridge_signing import sign_billing_request

    public = public_key_document()
    pub = Ed25519PublicKey.from_public_bytes(
        base64.urlsafe_b64decode(public["x"] + "==")
    )
    body, headers = sign_billing_request({
        "car_id": 3, "owner_user_id": 1, "vin": "WDCGG5HB1EG276273"
    })
    message = (
        headers["X-Aura-Timestamp"] + "." +
        headers["X-Aura-Nonce"] + "."
    ).encode() + body
    signature = base64.urlsafe_b64decode(headers["X-Aura-Signature"] + "==")
    pub.verify(signature, message)
    try:
        pub.verify(signature, message.replace(b'"car_id":3', b'"car_id":4'))
    except Exception:
        pass
    else:
        raise AssertionError("Tampering with vehicle ID must invalidate signature")
    _, other = sign_billing_request({
        "car_id": 3, "owner_user_id": 1, "vin": "WDCGG5HB1EG276273"
    })
    assert other["X-Aura-Nonce"] != headers["X-Aura-Nonce"]


def test_production_handshake_route_hidden_by_default(app, client, monkeypatch):
    monkeypatch.delenv("AURA_BILLING_SMOKE_TEST_ENABLED", raising=False)
    assert client.get("/internal/health/billing-bridge").status_code == 404


def test_read_only_handshake_authenticates_without_owner_data(
    app, client, monkeypatch
):
    _configure(monkeypatch)
    monkeypatch.setenv("AURA_BILLING_SMOKE_TEST_ENABLED", "true")
    seen = []

    def bridge(_url, *, data, headers, timeout, allow_redirects):
        payload = json.loads(data)
        assert payload == {
            "car_id": 2147483647,
            "owner_user_id": 2147483647,
            "vin": "00000000000000000",
        }
        seen.append(payload)
        if "X-Aura-Signature" not in headers:
            return FakeResponse({"error": "Unauthorized"}, status=401)
        if len(seen) == 2:
            return FakeResponse({
                "state": "not_published", "documents": [], "payments": []
            })
        return FakeResponse({"error": "Unauthorized"}, status=401)

    monkeypatch.setattr("requests.post", bridge)
    response = client.get("/internal/health/billing-bridge")
    assert response.status_code == 200
    assert response.get_json() == {
        "handshake": "authenticated", "publication": "none",
        "unsigned": "rejected", "replay": "rejected",
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert len(seen) == 3


def test_owner_can_open_only_valid_published_source_document(
    app, client, monkeypatch
):
    _configure(monkeypatch)
    owner = _user(suffix=782)
    car = _car(suffix=782)
    _own(owner=owner, car=car, suffix=782)
    db.session.commit()
    doc_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    observed = []

    def service(_endpoint, *, data, headers, timeout, allow_redirects):
        payload = json.loads(data)
        observed.append(payload)
        assert payload["owner_user_id"] == owner.id
        assert payload["car_id"] == car.id
        assert payload["document_id"] == doc_id
        assert payload["action"] == "document"
        assert headers["X-Aura-Signature"]
        assert allow_redirects is False
        return FakeResponse({
            "state": "linked",
            "brand": {
                "name": "Ajebo Fix Ltd",
                "tagline": "Luxury Automotive Health & Concierge",
                "footer": "Discretion. Precision. Excellence.",
                "logo": "", "website": "www.ajebofix.com",
            },
            "document": {
                "id": doc_id, "kind": "estimate", "group": "job_record",
                "number": "AJF-EST-TEST", "status": "issued",
                "issued": "2026-10-08", "valid_until": "2026-10-15",
                "due": "", "revision": 1, "currency": "₦",
                "total": "650000", "paid": "0", "balance": "650000",
                "scope": "Body restoration and painting",
                "terms": "Ninety percent upfront",
                "sections": [{
                    "title": "Restoration services",
                    "rows": [
                        {"description": "Bodywork", "quantity": "1",
                         "unit_price": "250000", "amount": "250000"},
                        {"description": "Painting", "quantity": "1",
                         "unit_price": "180000", "amount": "180000"},
                        {"description": "Professional management",
                         "quantity": "1", "unit_price": "220000",
                         "amount": "220000"},
                    ],
                }],
            },
        })

    monkeypatch.setattr(
        "services.billing_client_bridge.requests.post", service
    )
    _sign_in(client, owner)
    response = client.get(f"/cars/{car.id}/billing/documents/{doc_id}")
    assert response.status_code == 200
    assert b"AJF-EST-TEST" in response.data
    assert b"650,000" in response.data
    assert b"Discretion. Precision. Excellence." in response.data
    assert b"Bodywork" in response.data
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Referrer-Policy"] in {"no-referrer", "strict-origin-when-cross-origin"}
    assert len(observed) == 1


def test_owner_detail_hides_revoked_and_other_owner_documents(
    app, client, monkeypatch
):
    _configure(monkeypatch)
    owner = _user(suffix=786)
    another_owner = _user(suffix=787)
    car = _car(suffix=786)
    _own(owner=owner, car=car, suffix=786)
    db.session.commit()
    doc_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    def denied(_endpoint, *, data, headers, timeout, allow_redirects):
        return FakeResponse({"error": "Document not available"}, status=404)

    monkeypatch.setattr("services.billing_client_bridge.requests.post", denied)
    _sign_in(client, owner)
    assert client.get(f"/cars/{car.id}/billing/documents/{doc_id}").status_code == 404

    _sign_in(client, another_owner)
    assert client.get(f"/cars/{car.id}/billing/documents/{doc_id}").status_code == 404


def test_invalid_document_id_requires_no_remote_call(app, client, monkeypatch):
    _configure(monkeypatch)
    owner = _user(suffix=789)
    car = _car(suffix=789)
    _own(owner=owner, car=car, suffix=789)
    db.session.commit()
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid ID must not reach provider")
    monkeypatch.setattr(
        "services.billing_client_bridge.requests.post", forbidden
    )
    _sign_in(client, owner)
    assert client.get(f"/cars/{car.id}/billing/documents/not-a-document").status_code == 404


def test_advisor_previews_source_without_releasing_to_owner(
    app, client, monkeypatch
):
    _configure(monkeypatch)
    owner = _user(suffix=792)
    advisor = _user(suffix=793, role="admin")
    car = _car(suffix=792)
    _own(owner=owner, car=car, suffix=792)
    db.session.commit()
    doc_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    def provider(_endpoint, *, data, headers, timeout, allow_redirects):
        payload = json.loads(data)
        assert payload["action"] == "advisor_preview"
        assert payload["owner_user_id"] == owner.id
        assert payload["advisor_user_id"] == advisor.id
        assert payload["document_id"] == doc_id
        assert headers["X-Aura-Signature"]
        return FakeResponse({
            "state":"linked", "preview":True,
            "brand":{"name":"Ajebo Fix Ltd", "tagline":"Luxury",
                     "footer":"Discretion. Precision. Excellence.",
                     "website":"", "logo":""},
            "document":{
                "id":doc_id, "kind":"estimate", "group":"job_record",
                "number":"AJF-EST-TEST", "status":"issued",
                "issued":"2026-10-08", "valid_until":"2026-10-15",
                "due":"", "revision":1, "currency":"₦",
                "total":"650000", "paid":"0", "balance":"650000",
                "scope":"Body restoration",
                "terms":"90 percent upfront", "sections":[]
            }
        })
    monkeypatch.setattr("services.billing_client_bridge.requests.post",provider)
    _sign_in(client, advisor)
    response=client.get(f"/admin/cars/{car.id}/billing/preview/{doc_id}")
    assert response.status_code == 200
    assert b"Advisor-only document preview" in response.data
    assert b"not</strong> a native Billing PDF" in response.data
    assert b"AJF-EST-TEST" in response.data
    assert b"Print / Save as PDF" not in response.data

def test_owner_cannot_access_advisor_unpublished_preview(app, client, monkeypatch):
    _configure(monkeypatch)
    owner=_user(suffix=795)
    car=_car(suffix=795)
    _own(owner=owner,car=car,suffix=795)
    db.session.commit()
    _sign_in(client,owner)
    doc_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    assert client.get(f"/admin/cars/{car.id}/billing/preview/{doc_id}").status_code == 403


def test_advisor_can_preview_only_current_owners_released_financial_records(
    app, client, monkeypatch
):
    _configure(monkeypatch)
    owner = _user(suffix=812)
    advisor = _user(suffix=813, role="admin")
    car = _car(suffix=812)
    _own(owner=owner, car=car, suffix=812)
    db.session.commit()
    doc_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    seen = _mock_gateway(
        monkeypatch,
        documents=[{
            "id": doc_id,
            "kind": "invoice", "group": "vehicle_history",
            "number": "AJF-INVOICE-PUBLISHED", "status": "partially_paid",
            "issued": "2026-10-08", "currency": "₦",
            "total": "500000", "paid": "490000", "balance": "10000",
        }],
    )
    _sign_in(client, advisor)
    response = client.get(f"/admin/cars/{car.id}/billing/preview")
    assert response.status_code == 200
    assert b"Preview as Client" in response.data
    assert b"AJF-INVOICE-PUBLISHED" in response.data
    assert b"10,000" in response.data
    assert f"/admin/cars/{car.id}/billing/preview/{doc_id}".encode() in response.data
    assert b"owner_treatment_plans" not in response.data
    assert len(seen) == 1
    assert seen[0]["owner_user_id"] == owner.id


def test_client_cannot_enter_advisor_financial_preview(app, client, monkeypatch):
    _configure(monkeypatch)
    owner = _user(suffix=816)
    car = _car(suffix=816)
    _own(owner=owner, car=car, suffix=816)
    db.session.commit()
    def forbidden(*args, **kwargs):
        raise AssertionError("Disallowed client preview must not query Billing")
    monkeypatch.setattr("services.billing_client_bridge.requests.post", forbidden)
    _sign_in(client, owner)
    assert client.get(f"/admin/cars/{car.id}/billing/preview").status_code == 403
