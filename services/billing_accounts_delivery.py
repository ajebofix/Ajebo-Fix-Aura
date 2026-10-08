"""Advisor-approved Billing estimate publication and Resend delivery.

This component cannot create or alter Billing prices. The native Billing app
must issue a document with an authenticated Billing author first; the
restricted Billing Edge service independently verifies that provenance.
"""
from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from urllib.parse import urlparse

import requests
from flask import current_app

from models import AdvisorNote
from services.billing_bridge_signing import sign_billing_request
from services.billing_client_bridge import (
    BillingBridgeUnavailable, _normalise_vin, _money,
)

DELIVERY_PREFIX = "[AURA_BILLING_RESEND_DELIVERY_V1]"
TEMPLATE_ID = "ajebo-accounts-secure-document"


def publish_native_billing_estimate(
    *, car_id: int, owner_user_id: int, advisor_user_id: int,
    vin: str, document_id: str,
) -> str:
    """Trigger single-source publication only after native Billing proof."""
    try:
        doc_uuid = str(uuid.UUID(document_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise BillingBridgeUnavailable("Invalid estimate reference") from exc
    endpoint = os.getenv("AURA_BILLING_BRIDGE_URL", "").strip()
    uri = urlparse(endpoint)
    if (
        uri.scheme != "https"
        or uri.hostname != "odtctmjhkcphyaozpcup.supabase.co"
        or uri.path != "/functions/v1/aura-billing-bridge"
        or uri.username or uri.password or uri.query or uri.fragment
    ):
        raise BillingBridgeUnavailable("Invalid restricted Billing gateway")
    norm_vin = _normalise_vin(vin)
    if len(norm_vin) != 17 or min(car_id, owner_user_id, advisor_user_id) < 1:
        raise BillingBridgeUnavailable("Invalid vehicle or advisor")
    body, headers = sign_billing_request({
        "action": "publish_document",
        "car_id": car_id,
        "owner_user_id": owner_user_id,
        "advisor_user_id": advisor_user_id,
        "vin": norm_vin,
        "document_id": doc_uuid,
    })
    try:
        resp = requests.post(
            endpoint, data=body,
            headers={**headers, "Accept": "application/json",
                     "Content-Type": "application/json"},
            timeout=(3.05, 12), allow_redirects=False,
        )
        if resp.status_code in {404, 409}:
            raise BillingBridgeUnavailable(
                "Native Billing estimate not eligible for release."
            )
        resp.raise_for_status()
        result = resp.json()
    except requests.RequestException as exc:
        raise BillingBridgeUnavailable("Billing publication unavailable") from exc
    if not isinstance(result, dict) or result.get("state") not in {
        "published", "already_published",
    }:
        raise BillingBridgeUnavailable("Billing publication not confirmed")
    return result["state"]


def already_delivered(*, car_id: int, owner_user_id: int, document_id: str) -> bool:
    """Prevent a second dispatch after an accepted Resend provider response."""
    notes = AdvisorNote.query.filter_by(
        car_id=car_id, user_id=owner_user_id,
    ).order_by(AdvisorNote.id.desc()).limit(300).all()
    for item in notes:
        if not str(item.note).startswith(DELIVERY_PREFIX):
            continue
        try:
            payload = json.loads(item.note[len(DELIVERY_PREFIX):])
        except (TypeError, ValueError):
            continue
        if (
            payload.get("document_id") == document_id
            and payload.get("provider_message_id")
            and payload.get("event") == "submitted"
        ):
            return True
    return False


def send_accounts_estimate_via_resend(
    *, to: str, customer: str, vehicle: str, document: dict,
    car_id: int, upfront_percentage: int,
) -> str:
    """Submit one published native Billing estimate with a secure Aura link."""
    if current_app.config.get("MAIL_SUPPRESS_SEND"):
        raise BillingBridgeUnavailable("Sending disabled in this environment")
    key = os.getenv("RESEND_API_KEY") or current_app.config.get("RESEND_API_KEY")
    if not key:
        raise BillingBridgeUnavailable("Resend key not configured")
    if upfront_percentage not in {50, 60, 70, 80, 90, 100}:
        raise BillingBridgeUnavailable("Invalid approved mobilisation percentage")
    if document.get("kind") != "estimate" or document.get("status") != "issued":
        raise BillingBridgeUnavailable("Only native issued estimates may be emailed")
    if document.get("group") != "job_record":
        raise BillingBridgeUnavailable("Estimate has no commercial job reference")
    total = _money(document["total"])
    upfront = (total * Decimal(upfront_percentage) / Decimal(100)).quantize(
        Decimal("0.01")
    )
    balance = total - upfront
    terms = str(document.get("terms") or "").lower()
    # Pilot contract is fixed at 90% and must be part of the SOURCE estimate.
    if document.get("job_number") == "JOB-2026-003" and (
        upfront_percentage != 90 or "90%" not in terms or
        "585,000" not in terms or "65,000" not in terms
    ):
        raise BillingBridgeUnavailable(
            "The native estimate must contain the approved 90% payment terms."
        )
    doc_id = str(uuid.UUID(document["id"]))
    link = (
        f"https://aura.ajebofix.com/cars/{car_id}/billing/documents/{doc_id}"
    )
    money = lambda amount: f"₦{amount:,.2f}"
    payload = {
        "from": "Ajebo Fix Accounts <accounts@updates.ajebofix.com>",
        "to": [to],
        "reply_to": "ajebofix@gmail.com",
        "template": {
            "id": TEMPLATE_ID,
            "variables": {
                "CUSTOMER_NAME": customer,
                "DOC_TYPE": "estimate",
                "VEHICLE": vehicle,
                "DOC_NUMBER": document["number"],
                "TOTAL_AMOUNT": money(total),
                "UPFRONT_AMOUNT": money(upfront),
                "BALANCE_AMOUNT": money(balance),
                "DOCUMENT_URL": link,
            },
        },
        "tags": [
            {"name": "source", "value": "aura_billing"},
            {"name": "type", "value": "estimate"},
        ],
    }
    import hashlib
    signature = hashlib.sha256(
        f"{car_id}:{doc_id}:{to.lower()}:v{document.get('revision',1)}".encode()
    ).hexdigest()
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Idempotency-Key": f"aura-billing-{signature}",
                "User-Agent": "AjeboFixAura/1.0",
            },
            timeout=12,
            allow_redirects=False,
        )
        response.raise_for_status()
        out = response.json()
        msg_id = str(out.get("id") or "") if isinstance(out, dict) else ""
        if not msg_id:
            raise BillingBridgeUnavailable("Resend did not confirm an email ID")
        return msg_id
    except (requests.RequestException, ValueError) as exc:
        current_app.logger.warning("Resend estimate submission failed")
        raise BillingBridgeUnavailable("Resend did not accept the email") from exc
