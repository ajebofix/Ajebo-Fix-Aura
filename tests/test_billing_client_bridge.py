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
            "kind": "invoice", "number": "INV-2026-TEST",
            "status": "partially_paid", "issued": "2026-10-08",
            "due": "", "currency": "₦", "total": "150000",
            "paid": "50000", "balance": "100000",
        }, {
            "kind": "receipt", "number": "RCP-2026-TEST",
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
    assert b"100000" in resp.data
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
        "kind": "invoice", "number": "BAD", "status": "paid",
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
