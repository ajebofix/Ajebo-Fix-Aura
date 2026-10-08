"""Least-privilege Aura Billing projection via the restricted Billing Edge gateway.

Billing is canonical for commercial history. This client-facing gateway has no
write operations, no Supabase service-role key, and no email-fallback access.
The Edge gateway requires a published owner binding AND per-document approval.
"""
from __future__ import annotations

import os
import re
import uuid

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
        document_id = str(item.get("id") or "")
        try:
            document_id = str(uuid.UUID(document_id))
        except (ValueError, AttributeError):
            raise BillingBridgeUnavailable("Invalid published document ID")
        safe_docs.append({
            "id": document_id,
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


def client_billing_document(
    *, car_id: int, owner_user_id: int, vin: str, document_id: str,
    advisor_user_id: int | None = None,
) -> dict | None:
    """Read one previously published document from Billing's authoritative record.

    The Billing gateway independently verifies account + vehicle + VIN, active
    per-document publication, non-revoked current revision and document status.
    Returns no unreviewed files, share tokens, bank accounts or internal notes.
    """
    if not client_billing_feature_enabled():
        return None
    try:
        normalized_id = str(uuid.UUID(document_id))
    except (ValueError, TypeError, AttributeError):
        return None
    vin_value = _normalise_vin(vin)
    if (
        type(car_id) is not int or car_id < 1
        or type(owner_user_id) is not int or owner_user_id < 1
        or len(vin_value) != 17
    ):
        return None
    endpoint = os.getenv("AURA_BILLING_BRIDGE_URL", "").strip()
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https" or parsed.hostname !=
        "odtctmjhkcphyaozpcup.supabase.co"
        or parsed.path != "/functions/v1/aura-billing-bridge"
        or parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise BillingBridgeUnavailable("Invalid document gateway")
    payload = {
        "action": "advisor_preview" if advisor_user_id else "document",
        "car_id": car_id,
        "owner_user_id": owner_user_id,
        "vin": vin_value,
        "document_id": normalized_id,
    }
    if advisor_user_id is not None:
        if type(advisor_user_id) is not int or advisor_user_id < 1:
            return None
        payload["advisor_user_id"] = advisor_user_id
    body, signed_headers = sign_billing_request(payload)
    try:
        response = requests.post(
            endpoint,
            data=body,
            headers={
                **signed_headers,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=(3.05, 12),
            allow_redirects=False,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BillingBridgeUnavailable("Document retrieval unavailable") from exc
    if not isinstance(data, dict) or data.get("state") != "linked":
        return None
    doc = data.get("document")
    brand = data.get("brand")
    if not isinstance(doc, dict) or not isinstance(brand, dict):
        raise BillingBridgeUnavailable("Invalid document detail")
    if doc.get("id") != normalized_id:
        raise BillingBridgeUnavailable("Document identity mismatch")
    if doc.get("kind") not in {"estimate", "invoice", "receipt"}:
        raise BillingBridgeUnavailable("Invalid document type")
    if doc.get("group") not in {"vehicle_history", "job_record"}:
        raise BillingBridgeUnavailable("Invalid document provenance")
    sections = doc.get("sections")
    if not isinstance(sections, list) or len(sections) > 25:
        raise BillingBridgeUnavailable("Invalid document sections")
    clean_sections = []
    for section in sections:
        if not isinstance(section, dict):
            raise BillingBridgeUnavailable("Invalid source section")
        lines = section.get("rows")
        if not isinstance(lines, list) or len(lines) > 100:
            raise BillingBridgeUnavailable("Invalid line items")
        safe_lines = []
        for line in lines:
            if not isinstance(line, dict):
                raise BillingBridgeUnavailable("Invalid line")
            safe_lines.append({
                "description": str(line.get("description") or "")[:220],
                "quantity": str(_money(line.get("quantity"))),
                "unit_price": str(_money(line.get("unit_price"))),
                "amount": str(_money(line.get("amount"))),
            })
        clean_sections.append({
            "title": str(section.get("title") or "")[:160],
            "rows": safe_lines,
        })
    clean_doc = {
        "id": normalized_id,
        "kind": doc["kind"],
        "billed_to": str(doc.get("billed_to") or "")[:120],
        "vin": _normalise_vin(doc.get("vin"))[:17],
        "job_number": str(doc.get("job_number") or "")[:80],
        "sow_number": str(doc.get("sow_number") or "")[:80],
        "group": doc["group"],
        "number": str(doc.get("number") or "")[:80],
        "status": str(doc.get("status") or "")[:40],
        "issued": str(doc.get("issued") or "")[:10],
        "valid_until": str(doc.get("valid_until") or "")[:10],
        "due": str(doc.get("due") or "")[:10],
        "revision": int(doc.get("revision") or 1),
        "currency": str(doc.get("currency") or "₦")[:5],
        "total": str(_money(doc.get("total"))),
        "paid": str(_money(doc.get("paid"))),
        "balance": str(max(_money(doc.get("total")) - _money(doc.get("paid")), Decimal("0"))),
        "scope": str(doc.get("scope") or "")[:1400],
        "terms": str(doc.get("terms") or "")[:2400],
        "sections": clean_sections,
    }
    return {
        "document": clean_doc,
        "brand": {
            "name": str(brand.get("name") or "Ajebo Fix Ltd")[:100],
            "tagline": str(brand.get("tagline") or "")[:140],
            "website": str(brand.get("website") or "")[:150],
            "footer": str(brand.get("footer") or "")[:160],
            "logo": str(brand.get("logo") or "")[:600]
            if str(brand.get("logo") or "").startswith(
                "https://odtctmjhkcphyaozpcup.supabase.co/storage/"
            ) else "",
        },
    }


def advisor_billing_inventory(
    *, car_id: int, owner_user_id: int, advisor_user_id: int, vin: str
) -> dict:
    """List native Billing document references for one admin-verified vehicle.

    This is a distinct privileged action; owner/driver routes cannot invoke it.
    The restricted Billing service verifies the active owner/vehicle mapping,
    full VIN and signer before returning a bounded whitelisted inventory.
    """
    empty = {"state": "not_published", "documents": []}
    if not client_billing_feature_enabled():
        return {"state": "not_enabled", "documents": []}
    if (
        type(car_id) is not int or car_id < 1
        or type(owner_user_id) is not int or owner_user_id < 1
        or type(advisor_user_id) is not int or advisor_user_id < 1
    ):
        return empty
    vin_value = _normalise_vin(vin)
    if len(vin_value) != 17:
        return empty
    endpoint = os.getenv("AURA_BILLING_BRIDGE_URL", "").strip()
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "odtctmjhkcphyaozpcup.supabase.co"
        or parsed.path != "/functions/v1/aura-billing-bridge"
        or parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise BillingBridgeUnavailable("Invalid restricted Billing gateway")

    body, signature_headers = sign_billing_request({
        "action": "advisor_documents",
        "car_id": car_id,
        "owner_user_id": owner_user_id,
        "advisor_user_id": advisor_user_id,
        "vin": vin_value,
    })
    try:
        resp = requests.post(
            endpoint, data=body,
            headers={
                **signature_headers,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=(3.05, 12), allow_redirects=False,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise BillingBridgeUnavailable("Billing document inventory unavailable") from exc
    if not isinstance(data, dict) or data.get("state") not in {
        "linked", "not_linked", "not_published",
    }:
        raise BillingBridgeUnavailable("Invalid document inventory state")
    if data["state"] != "linked":
        return {"state": data["state"], "documents": []}
    docs = data.get("documents")
    if not isinstance(docs, list) or len(docs) > 75:
        raise BillingBridgeUnavailable("Invalid document inventory")
    clean_docs = []
    for item in docs:
        if not isinstance(item, dict):
            raise BillingBridgeUnavailable("Invalid inventory entry")
        try:
            doc_id = str(uuid.UUID(item.get("id", "")))
        except (TypeError, ValueError, AttributeError) as exc:
            raise BillingBridgeUnavailable("Invalid inventory identity") from exc
        kind = item.get("kind")
        status = item.get("status")
        if kind not in {"estimate", "invoice", "receipt"} or status not in {
            "draft", "issued", "sent", "accepted", "paid",
            "partially_paid", "overdue",
        }:
            raise BillingBridgeUnavailable("Invalid inventory status")
        if type(item.get("native_created")) is not bool or type(item.get("published")) is not bool or type(item.get("job_linked")) is not bool:
            raise BillingBridgeUnavailable("Invalid document provenance")
        clean_docs.append({
            "id": doc_id,
            "kind": kind,
            "number": str(item.get("number") or "")[:80],
            "status": status,
            "issued": str(item.get("issued") or "")[:10],
            "amount": str(_money(item.get("amount"))),
            "currency": str(item.get("currency") or "₦")[:5],
            "native_created": item["native_created"],
            "job_linked": item["job_linked"],
            "published": item["published"],
        })
    return {"state": "linked", "documents": clean_docs}
