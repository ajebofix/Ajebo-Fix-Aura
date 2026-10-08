"""Least-privilege Aura Billing projection via the restricted Billing Edge gateway.

Billing is canonical for commercial history. This client-facing gateway has no
write operations, no Supabase service-role key, and no email-fallback access.
The Edge gateway requires a published owner binding AND per-document approval.
"""
from __future__ import annotations

import os
import re

from services.billing_bridge_signing import sign_billing_request
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import requests


class BillingBridgeUnavailable(Exception):
    """Internal commercial projection failure; never reveal provider internals."""


def _normalise_vin(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _money(value: object) -> Decimal:
    try:
        v = Decimal(str(value if value is not None else "0"))
        if not v.is_finite() or v < 0:
            raise InvalidOperation
        return v
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise BillingBridgeUnavailable("Invalid commercial amount") from exc


def client_billing_feature_enabled() -> bool:
    return (
        os.getenv("AURA_BILLING_CLIENT_VIEW_ENABLED", "").strip().lower() == "true"
        and bool(os.getenv("AURA_BILLING_BRIDGE_URL", "").strip())
    )


def client_billing_snapshot(
    *, car_id: int, owner_user_id: int, vin: str
) -> dict:
    """Caller first enforces a current authenticated owner and verified account.

    Billing's protected gateway independently enforces an explicitly published
    mapping of BOTH Aura car ID AND Aura owner user ID to the Billing vehicle,
    a full 17-character VIN match, and per-document publication approval.
    """
    empty = {"state": "not_published", "documents": [], "payments": []}
    if not client_billing_feature_enabled():
        return {**empty, "state": "not_enabled"}
    norm_vin = _normalise_vin(vin)
    if (
        type(car_id) is not int or car_id < 1
        or type(owner_user_id) is not int or owner_user_id < 1
        or len(norm_vin) != 17
    ):
        return empty

    endpoint = os.getenv("AURA_BILLING_BRIDGE_URL", "").strip()
    uri = urlparse(endpoint)
    if (
        uri.scheme != "https"
        or not uri.hostname
        or not uri.hostname.endswith(".supabase.co")
        or uri.path != "/functions/v1/aura-billing-bridge"
        or uri.username or uri.password or uri.query or uri.fragment
    ):
        raise BillingBridgeUnavailable("Invalid Billing gateway configuration")
    try:
        body, signature_headers = sign_billing_request({
            "car_id": car_id,
            "owner_user_id": owner_user_id,
            "vin": norm_vin,
        })
        resp = requests.post(
            endpoint,
            headers={
                **signature_headers,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            data=body,
            timeout=(3.05, 12),
            allow_redirects=False,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise BillingBridgeUnavailable("Billing gateway read failed") from exc
    if not isinstance(data, dict):
        raise BillingBridgeUnavailable("Invalid Billing gateway result")
    state = data.get("state")
    if state not in ("not_published", "not_linked", "linked"):
        raise BillingBridgeUnavailable("Unexpected Billing publication status")
    if state != "linked":
        return {**empty, "state": state}

    rows = data.get("documents")
    payment_rows = data.get("payments")
    if (
        not isinstance(rows, list) or len(rows) > 100
        or not isinstance(payment_rows, list) or len(payment_rows) > 100
    ):
        raise BillingBridgeUnavailable("Invalid Billing documents")
    safe_docs = []
    for item in rows:
        if not isinstance(item, dict):
            raise BillingBridgeUnavailable("Invalid Billing document")
        kind = str(item.get("kind", ""))
        group = str(item.get("group", ""))
        if kind not in ("invoice", "estimate", "receipt"):
            raise BillingBridgeUnavailable("Unrecognised commercial document")
        if group not in ("vehicle_history", "job_record"):
            raise BillingBridgeUnavailable("Missing commercial document provenance")
        total = _money(item.get("total"))
        paid = _money(item.get("paid"))
        safe_docs.append({
            "kind": kind,
            "group": group,
            "number": str(item.get("number") or "")[:80],
            "status": str(item.get("status") or "")[:36],
            "issued": str(item.get("issued") or "")[:10],
            "due": str(item.get("due") or "")[:10],
            "currency": str(item.get("currency") or "₦")[:5],
            "total": str(total),
            "paid": str(paid),
            "balance": str(max(Decimal("0"), total - paid)),
        })
    safe_payments = []
    for payment in payment_rows:
        if not isinstance(payment, dict):
            raise BillingBridgeUnavailable("Invalid Billing payment")
        safe_payments.append({
            "amount": str(_money(payment.get("amount"))),
            "currency": str(payment.get("currency") or "₦")[:5],
            "paid_at": str(payment.get("paid_at") or "")[:10],
        })
    return {"state": "linked", "documents": safe_docs, "payments": safe_payments}
