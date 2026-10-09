"""Advisor-approved Billing estimate publication and Resend delivery.

This component cannot create or alter Billing prices. The native Billing app
must issue a document with an authenticated Billing author first; the
restricted Billing Edge service independently verifies that provenance.
"""
from __future__ import annotations

import json
import os
import hashlib
from html import escape
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


def publish_native_billing_document(
    *, car_id: int, owner_user_id: int, advisor_user_id: int,
    vin: str, document_id: str,
) -> str:
    """Explicitly publish an eligible estimate, invoice or receipt.

    The restricted signed Billing gateway verifies the document's real kind,
    status, linked job, verified owner and (for receipts) payment source.
    """
    return publish_native_billing_estimate(
        car_id=car_id, owner_user_id=owner_user_id,
        advisor_user_id=advisor_user_id, vin=vin, document_id=document_id,
    )


def approve_native_estimate_issue(
    *, car_id: int, owner_user_id: int, advisor_user_id: int,
    vin: str, document: dict,
) -> bool:
    """Signed adviser issuance. No publication, payment or email is performed."""
    validate_estimate_delivery(document, 90, allow_draft=True)
    if document.get("status") != "draft":
        raise BillingBridgeUnavailable("Document is no longer a draft")
    doc_id = str(uuid.UUID(document["id"]))
    stamp = str(document.get("updated_at") or "")
    if not stamp or len(stamp) > 60:
        raise BillingBridgeUnavailable("Missing document review version")
    endpoint = os.getenv("AURA_BILLING_BRIDGE_URL", "").strip()
    parsed = urlparse(endpoint)
    if (parsed.scheme != "https" or
        parsed.hostname != "odtctmjhkcphyaozpcup.supabase.co" or
        parsed.path != "/functions/v1/aura-billing-bridge" or
        parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise BillingBridgeUnavailable("Billing gateway unavailable")
    norm_vin = _normalise_vin(vin)
    if len(norm_vin) != 17 or min(car_id, owner_user_id, advisor_user_id) < 1:
        raise BillingBridgeUnavailable("Invalid owner or vehicle")
    body, sig_headers = sign_billing_request({
        "action": "issue_document",
        "car_id": car_id,
        "owner_user_id": owner_user_id,
        "advisor_user_id": advisor_user_id,
        "vin": norm_vin,
        "document_id": doc_id,
        "expected_total": str(_money(document["total"])),
        "expected_revision": int(document["revision"]),
        "expected_updated_at": stamp,
    })
    try:
        response = requests.post(
            endpoint, data=body, headers={
                **sig_headers, "Accept": "application/json",
                "Content-Type": "application/json",
            }, timeout=(3.05, 12), allow_redirects=False,
        )
        if response.status_code in (404, 409):
            raise BillingBridgeUnavailable(
                "The draft changed or is no longer eligible. Refresh and review again."
            )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BillingBridgeUnavailable("Issuance could not be confirmed") from exc
    if (not isinstance(result, dict) or result.get("state") != "issued"
        or result.get("document_id") != doc_id):
        raise BillingBridgeUnavailable("Billing did not confirm issuance")
    return True


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


def validate_estimate_delivery(
    document: dict, upfront_percentage: int, *, allow_draft: bool = False,
) -> tuple[Decimal, Decimal, Decimal]:
    """Validate live Billing figures and the approved source terms before publication.

    Never reuse amounts from a previous estimate revision: each newly issued
    document is independently reviewed against its own commercial terms.
    """
    if type(upfront_percentage) is not int or upfront_percentage not in {
        50, 60, 70, 80, 90, 100,
    }:
        raise BillingBridgeUnavailable("Invalid approved mobilisation percentage")
    eligible = {"issued", "sent"} | ({"draft"} if allow_draft else set())
    if document.get("kind") != "estimate" or document.get("status") not in eligible:
        raise BillingBridgeUnavailable("Issue the revised estimate in Billing first")
    if document.get("group") != "job_record":
        raise BillingBridgeUnavailable("Estimate has no commercial job reference")

    total = _money(document.get("total"))
    if total <= 0:
        raise BillingBridgeUnavailable("Estimate total must be positive")
    upfront = (total * Decimal(upfront_percentage) / Decimal(100)).quantize(
        Decimal("0.01")
    )
    balance = total - upfront

    # Christian's negotiated 90/10 mobilisation schedule must be documented
    # on the CURRENT native Billing revision, not just an earlier revision.
    if document.get("job_number") == "JOB-2026-003":
        terms = str(document.get("terms") or "")
        upfront_text = f"{upfront:,.2f}".removesuffix(".00")
        balance_text = f"{balance:,.2f}".removesuffix(".00")
        if (
            upfront_percentage != 90
            or "90%" not in terms
            or upfront_text not in terms
            or balance_text not in terms
        ):
            raise BillingBridgeUnavailable(
                "Update the current Billing estimate payment terms to "
                f"90% upfront (₦{upfront_text}) and 10% balance "
                f"(₦{balance_text}) before publication or email delivery."
            )
    return total, upfront, balance


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
    total, upfront, balance = validate_estimate_delivery(
        document, upfront_percentage,
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


def send_accounts_document_via_resend(
    *, to: str, customer: str, vehicle: str,
    document: dict, car_id: int,
) -> str:
    """Send a published native invoice or payment receipt with a secure link.

    The invoice reflects the live Billing total and paid/balance. A receipt
    refers only to money already recorded in Billing. This function does
    not issue documents, approve publication, or record a payment.
    """
    kind = document.get("kind")
    status = document.get("status")
    eligible = {
        "invoice": {"issued", "sent", "overdue", "partially_paid", "paid"},
        "receipt": {"issued"},
    }
    if kind not in eligible or status not in eligible[kind]:
        raise BillingBridgeUnavailable("Invoice or receipt not eligible for delivery")
    if document.get("group") != "job_record":
        raise BillingBridgeUnavailable("Unlinked Billing document cannot be emailed")
    if current_app.config.get("MAIL_SUPPRESS_SEND"):
        raise BillingBridgeUnavailable("Sending disabled in this environment")
    key = os.getenv("RESEND_API_KEY") or current_app.config.get("RESEND_API_KEY")
    if not key:
        raise BillingBridgeUnavailable("Resend key not configured")
    try:
        doc_id = str(uuid.UUID(document["id"]))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise BillingBridgeUnavailable("Invalid Billing document ID") from exc
    total = _money(document.get("total"))
    paid = _money(document.get("paid"))
    balance = _money(document.get("balance"))
    if total <= 0 or paid < 0 or balance < 0:
        raise BillingBridgeUnavailable("Invalid source payment amounts")
    if kind == "invoice" and abs(total - (paid + balance)) > Decimal("0.01"):
        raise BillingBridgeUnavailable("Invoice balances do not reconcile")
    if kind == "invoice" and status == "partially_paid" and not (0 < paid < total):
        raise BillingBridgeUnavailable("Partial payment not verified")
    if kind == "receipt" and status != "issued":
        raise BillingBridgeUnavailable("Receipt is not issued")
    label = "invoice" if kind == "invoice" else "payment receipt"
    link = f"https://aura.ajebofix.com/cars/{car_id}/billing/documents/{doc_id}"
    money = lambda amount: f"₦{amount:,.2f}"
    if kind == "invoice":
        details = (
            f"Invoice total: {money(total)}\n"
            f"Payments recorded: {money(paid)}\n"
            f"Outstanding balance: {money(balance)}"
        )
        disclaimer = (
            "This invoice reflects the payment records available at the time "
            "of sending. The secure document shows the current Billing balance."
        )
    else:
        details = f"Payment acknowledged: {money(total)}"
        disclaimer = (
            "This receipt confirms only the stated recorded payment. "
            "It does not certify full settlement unless the original Billing "
            "records establish that."
        )
    subject = f"Ajebo Fix Accounts | {label} {document['number']}"
    body = (
        f"Hello {customer},\n\n"
        f"Your {label} for {vehicle} is available for private review.\n\n"
        f"Document: {document['number']}\n"
        f"{details}\n\n"
        f"View securely in Aura: {link}\n\n"
        f"{disclaimer}\n"
        "This link requires signing into the verified Aura owner account.\n\n"
        "Ajebo Fix Ltd · Accounts"
    )
    html = (
        '<div style="background:#f2f4f8;padding:24px;font-family:Arial,sans-serif">'
        '<div style="max-width:600px;margin:auto;background:white;padding:26px">'
        '<h2 style="color:#0a1628">AJEBO FIX · ACCOUNTS</h2>'
        '<p>Private Automotive Health Management</p>'
        f'<p>Hello {escape(customer)},</p>'
        f'<p>Your {escape(label)} for {escape(vehicle)} is ready for private review.</p>'
        f'<p><strong>{escape(str(document["number"]))}</strong></p>'
        f'<p style="white-space:pre-line">{escape(details)}</p>'
        f'<p><a href="{escape(link, quote=True)}">View document securely in Aura</a></p>'
        f'<p>{escape(disclaimer)}</p>'
        '<p style="font-size:12px">Sign-in is required. Ajebo Fix Ltd · Accounts</p>'
        '</div></div>'
    )
    signature = hashlib.sha256(
        f"{car_id}:{doc_id}:{to.lower()}:{kind}:v{document.get('revision',1)}".encode()
    ).hexdigest()
    payload = {
        "from": "Ajebo Fix Accounts <accounts@updates.ajebofix.com>",
        "to": [to], "reply_to": "ajebofix@gmail.com",
        "subject": subject, "text": body, "html": html,
        "tags": [
            {"name":"source","value":"aura_billing"},
            {"name":"type","value":kind},
        ],
    }
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
            timeout=12, allow_redirects=False,
        )
        response.raise_for_status()
        result = response.json()
        message_id = str(result.get("id") or "") if isinstance(result, dict) else ""
        if not message_id:
            raise BillingBridgeUnavailable("Resend did not confirm delivery submission")
        return message_id
    except (requests.RequestException, ValueError) as exc:
        current_app.logger.warning("Resend document submission failed")
        raise BillingBridgeUnavailable("Resend did not accept the document email") from exc
