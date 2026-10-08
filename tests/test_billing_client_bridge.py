"""Owner Billing bridge: authorization, linkage and read-only filtering."""
from __future__ import annotations

import requests

from extensions import db
from services.billing_client_bridge import client_billing_snapshot
from test_rina_chat_cutover import _car, _own, _sign_in, _user

BILLING_CLIENT = "11111111-1111-4111-8111-111111111111"
BILLING_VEHICLE = "22222222-2222-4222-8222-222222222222"
ESTIMATE = "33333333-3333-4333-8333-333333333333"
INVOICE = "44444444-4444-4444-8444-444444444444"
RECEIPT = "55555555-5555-4555-8555-555555555555"
PAYMENT = "66666666-6666-4666-8666-666666666666"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _enable(monkeypatch):
    monkeypatch.setenv("AURA_BILLING_CLIENT_VIEW_ENABLED", "true")
    monkeypatch.setenv(
        "AURA_BILLING_SUPABASE_URL",
        "https://odtctmjhkcphyaozpcup.supabase.co",
    )
    monkeypatch.setenv("AURA_BILLING_SUPABASE_SERVICE_ROLE_KEY", "test-secret")


def _fake_gateway(monkeypatch, *, car, owner, vin=None, client_email=None):
    calls = []

    def fake_get(url, *, params, headers, timeout, allow_redirects):
        assert url.startswith("https://odtctmjhkcphyaozpcup.supabase.co/rest/v1/")
        assert headers["apikey"] == "test-secret"
        assert headers["Authorization"] == "Bearer test-secret"
        assert timeout[1] <= 8
        assert allow_redirects is False
        table = url.rsplit("/", 1)[-1]
        calls.append((table, dict(params)))
        if table == "billing_vehicles":
            assert params["aura_vehicle_ref"] == f"eq.{car.id}"
            return FakeResponse([
                {"id": BILLING_VEHICLE, "client_id": BILLING_CLIENT,
                 "vin": vin if vin is not None else car.vin,
                 "aura_vehicle_ref": str(car.id)}
            ])
        if table == "billing_clients":
            return FakeResponse([
                {"id": BILLING_CLIENT, "email": (
                    client_email if client_email is not None else owner.email
                )}
            ])
        if table == "billing_documents":
            assert params["vehicle_id"] == f"eq.{BILLING_VEHICLE}"
            assert params["client_id"] == f"eq.{BILLING_CLIENT}"
            common = {
                "vehicle_id": BILLING_VEHICLE,
                "client_id": BILLING_CLIENT,
                "issue_date": "2026-10-08",
                "due_date": None,
                "currency_symbol": "₦",
                "amount_paid": "0",
                "superseded_at": None,
                "share_revoked_at": None,
            }
            return FakeResponse([
                {**common, "id": ESTIMATE, "doc_type": "estimate",
                 "doc_number": "EST-2026-1", "status": "accepted", "total": "75000"},
                {**common, "id": INVOICE, "doc_type": "invoice",
                 "doc_number": "INV-2026-1", "status": "partially_paid",
                 "total": "150000", "amount_paid": "50000"},
                {**common, "id": RECEIPT, "doc_type": "receipt",
                 "doc_number": "RCT-2026-1", "status": "issued",
                 "total": "50000"},
                {**common, "id": "77777777-7777-4777-8777-777777777777",
                 "doc_type": "invoice", "doc_number": "INTERNAL-DRAFT",
                 "status": "draft", "total": "9000000"},
                {**common, "id": "88888888-8888-4888-8888-888888888888",
                 "doc_type": "estimate", "doc_number": "SUPERSEDED",
                 "status": "accepted", "total": "80000",
                 "superseded_at": "2026-10-09T10:00:00Z"},
            ])
        if table == "billing_payments":
            assert params["invoice_id"] == f"in.({INVOICE})"
            return FakeResponse([
                {"id": PAYMENT, "invoice_id": INVOICE, "amount": "50000",
                 "paid_at": "2026-10-08T10:00:00Z", "currency_symbol": "₦"},
                {"id": "99999999-9999-4999-8999-999999999999",
                 "invoice_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                 "amount": "400000", "paid_at": "2026-10-08",
                 "currency_symbol": "₦"},
            ])
        raise AssertionError(f"Unexpected table {table}")

    monkeypatch.setattr("services.billing_client_bridge.requests.get", fake_get)
    return calls


def test_billing_page_disabled_without_exposing_financial_records(
    app, client, monkeypatch
):
    monkeypatch.delenv("AURA_BILLING_CLIENT_VIEW_ENABLED", raising=False)
    owner = _user(suffix=401)
    car = _car(suffix=401)
    _own(owner=owner, car=car, suffix=401)
    db.session.commit()
    _sign_in(client, owner)
    response = client.get(f"/cars/{car.id}/billing")
    assert response.status_code == 200
    assert b"connection has not been enabled" in response.data
    assert b"INV-2026" not in response.data


def test_owner_can_view_published_billing_without_any_writes(
    app, client, monkeypatch
):
    _enable(monkeypatch)
    owner = _user(suffix=402)
    car = _car(suffix=402)
    _own(owner=owner, car=car, suffix=402)
    db.session.commit()
    calls = _fake_gateway(monkeypatch, car=car, owner=owner)
    _sign_in(client, owner)

    response = client.get(f"/cars/{car.id}/billing")
    assert response.status_code == 200
    assert b"EST-2026-1" in response.data
    assert b"INV-2026-1" in response.data
    assert b"RCT-2026-1" in response.data
    assert b"100000" in response.data  # invoice balance
    assert b"INTERNAL-DRAFT" not in response.data
    assert b"SUPERSEDED" not in response.data
    assert b"9000000" not in response.data
    assert [item[0] for item in calls] == [
        "billing_vehicles", "billing_clients", "billing_documents",
        "billing_payments",
    ]


def test_other_owner_and_revoked_owner_cannot_query_billing(
    app, client, monkeypatch
):
    _enable(monkeypatch)
    owner = _user(suffix=403)
    stranger = _user(suffix=404)
    car = _car(suffix=403)
    ownership = _own(owner=owner, car=car, suffix=403)
    db.session.commit()

    def forbid(*args, **kwargs):
        raise AssertionError("billing provider should not be called")

    monkeypatch.setattr("services.billing_client_bridge.requests.get", forbid)
    _sign_in(client, stranger)
    assert client.get(f"/cars/{car.id}/billing").status_code == 404

    db.session.delete(ownership)
    db.session.commit()
    # Original owner has no active ownership after transfer/revocation.
    _sign_in(client, owner)
    assert client.get(f"/cars/{car.id}/billing").status_code == 404


def test_driver_cannot_read_billing_even_with_car_assignment(
    app, client, monkeypatch
):
    _enable(monkeypatch)
    driver = _user(suffix=405, role="driver")
    car = _car(suffix=405)
    _own(owner=driver, car=car, suffix=405)
    db.session.commit()
    _sign_in(client, driver)
    assert client.get(f"/cars/{car.id}/billing").status_code == 403


def test_mismatched_vin_and_email_fail_closed(app, monkeypatch):
    _enable(monkeypatch)
    owner = _user(suffix=406)
    car = _car(suffix=406)
    db.session.commit()
    _fake_gateway(monkeypatch, car=car, owner=owner, vin="WRONGVIN000000000")
    mismatch = client_billing_snapshot(car_id=car.id, vin=car.vin, owner_email=owner.email)
    assert mismatch["state"] == "not_linked"
    assert mismatch["documents"] == []

    _fake_gateway(monkeypatch, car=car, owner=owner, client_email="other@example.com")
    mismatch = client_billing_snapshot(car_id=car.id, vin=car.vin, owner_email=owner.email)
    assert mismatch["state"] == "not_linked"
    assert mismatch["documents"] == []


def test_provider_outage_is_generic_and_non_disclosing(app, client, monkeypatch):
    _enable(monkeypatch)
    owner = _user(suffix=407)
    car = _car(suffix=407)
    _own(owner=owner, car=car, suffix=407)
    db.session.commit()
    _sign_in(client, owner)

    def timeout(*args, **kwargs):
        raise requests.Timeout("private upstream incident")

    monkeypatch.setattr("services.billing_client_bridge.requests.get", timeout)
    response = client.get(f"/cars/{car.id}/billing")
    assert response.status_code == 200
    assert b"temporarily unavailable" in response.data
    assert b"private upstream incident" not in response.data


def test_unverified_account_cannot_read_billing(app, client, monkeypatch):
    _enable(monkeypatch)
    owner = _user(suffix=408)
    owner.email_verified_at = None
    car = _car(suffix=408)
    _own(owner=owner, car=car, suffix=408)
    db.session.commit()
    _sign_in(client, owner)
    assert client.get(f"/cars/{car.id}/billing").status_code == 403


def test_client_vehicle_page_links_into_billing_portal(app, client):
    owner = _user(suffix=409)
    car = _car(suffix=409)
    _own(owner=owner, car=car, suffix=409)
    db.session.commit()
    _sign_in(client, owner)
    response = client.get(f"/cars/{car.id}")
    assert response.status_code == 200
    assert f'/cars/{car.id}/billing'.encode() in response.data
