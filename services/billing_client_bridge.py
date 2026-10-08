"""Read-only, owner-scoped commercial projection from existing Ajebo Fix Billing.

Aura does not own commercial truth. Billing remains canonical. The bridge
never modifies Supabase rows and never exposes service-role credentials to
the browser. All records must pass explicit vehicle + owner linkage checks.
"""
from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import requests

# Only explicitly client-publishable document states. Internal drafts,
# revisions superseded by a newer document, and revoked shared documents
# must never appear in the client projection.
CLIENT_DOCUMENT_STATES = {
    "estimate": frozenset({"accepted", "issued", "sent"}),
    "invoice": frozenset({"issued", "sent", "overdue", "partially_paid", "paid"}),
    "receipt": frozenset({"issued"}),
}
MAX_ROWS = 100


class BillingBridgeUnavailable(Exception):
    """Private failure without sensitive provider details."""


def _normalise_vin(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _valid_uuid(value: object) -> str | None:
    from uuid import UUID

    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _money(value: object) -> Decimal:
    try:
        amount = Decimal(str(value if value is not None else "0"))
        if not amount.is_finite() or amount < 0:
            raise InvalidOperation
        return amount
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise BillingBridgeUnavailable("Invalid billing amount") from exc


def client_billing_feature_enabled() -> bool:
    """Display navigation only after explicit, credentialled activation."""
    return (
        os.getenv("AURA_BILLING_CLIENT_VIEW_ENABLED", "").lower() == "true"
        and bool(os.getenv("AURA_BILLING_SUPABASE_URL", "").strip())
        and bool(os.getenv("AURA_BILLING_SUPABASE_SERVICE_ROLE_KEY", "").strip())
    )


def _client_ready() -> bool:
    return os.getenv("AURA_BILLING_CLIENT_VIEW_ENABLED", "").lower() == "true"


class _BillingReadGateway:
    def __init__(self) -> None:
        origin = os.getenv("AURA_BILLING_SUPABASE_URL", "").strip().rstrip("/")
        token = os.getenv("AURA_BILLING_SUPABASE_SERVICE_ROLE_KEY", "").strip()
        parsed = urlparse(origin)
        if (
            not token
            or parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".supabase.co")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise BillingBridgeUnavailable("Billing service not configured")
        self.origin = origin
        self.headers = {
            "apikey": token,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def get(self, table: str, *, select: str, filters: dict[str, str]) -> list[dict]:
        if table not in {
            "billing_vehicles",
            "billing_jobs",
            "billing_clients",
            "billing_documents",
            "billing_payments",
        }:
            raise BillingBridgeUnavailable("Disallowed billing resource")
        params = {"select": select, "limit": str(MAX_ROWS), **filters}
        try:
            response = requests.get(
                f"{self.origin}/rest/v1/{table}",
                headers=self.headers,
                params=params,
                timeout=(3.05, 8),
                allow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise BillingBridgeUnavailable("Billing read unavailable") from exc
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise BillingBridgeUnavailable("Unexpected billing response")
        return payload


def client_billing_snapshot(*, car_id: int, vin: str, owner_email: str | None) -> dict:
    """Return one client's published billing data, or a fail-closed empty state.

    Caller MUST verify the authenticated user has active owner access to
    this vehicle. Driver/admin identities are not eligible for this projection.

    Link requires an explicit Billing `aura_vehicle_ref`, full VIN equality,
    and exact owner billing email equality. Never use names/job numbers as keys.
    """
    empty = {"state": "not_linked", "documents": [], "payments": []}
    if not _client_ready():
        return {**empty, "state": "not_enabled"}

    car_vin = _normalise_vin(vin)
    email = str(owner_email or "").strip().casefold()
    if not isinstance(car_id, int) or car_id <= 0 or len(car_vin) != 17 or not email:
        return empty

    gateway = _BillingReadGateway()
    vehicles = gateway.get(
        "billing_vehicles",
        select="id,client_id,vin,aura_vehicle_ref",
        filters={"aura_vehicle_ref": f"eq.{car_id}"},
    )
    if len(vehicles) != 1 or _normalise_vin(vehicles[0].get("vin")) != car_vin:
        return empty

    billing_vehicle_id = _valid_uuid(vehicles[0].get("id"))
    billing_client_id = _valid_uuid(vehicles[0].get("client_id"))
    if not billing_vehicle_id or not billing_client_id:
        return empty

    clients = gateway.get(
        "billing_clients",
        select="id,email",
        filters={"id": f"eq.{billing_client_id}"},
    )
    if (
        len(clients) != 1
        or _valid_uuid(clients[0].get("id")) != billing_client_id
        or not clients[0].get("email")
        or str(clients[0]["email"]).strip().casefold() != email
    ):
        return empty

    rows = gateway.get(
        "billing_documents",
        select=(
            "id,doc_type,doc_number,status,issue_date,due_date,"
            "currency_symbol,total,amount_paid,vehicle_id,client_id,"
            "superseded_at,share_revoked_at"
        ),
        filters={
            "vehicle_id": f"eq.{billing_vehicle_id}",
            "client_id": f"eq.{billing_client_id}",
            "order": "issue_date.desc",
        },
    )

    documents = []
    invoice_ids = []
    for row in rows:
        kind = str(row.get("doc_type") or "").strip().lower()
        status = str(row.get("status") or "").strip().lower()
        document_id = _valid_uuid(row.get("id"))
        if (
            not document_id
            or kind not in CLIENT_DOCUMENT_STATES
            or status not in CLIENT_DOCUMENT_STATES[kind]
            or row.get("superseded_at")
            or row.get("share_revoked_at")
            or _valid_uuid(row.get("vehicle_id")) != billing_vehicle_id
            or _valid_uuid(row.get("client_id")) != billing_client_id
        ):
            continue
        total = _money(row.get("total"))
        paid = _money(row.get("amount_paid")) if kind == "invoice" else Decimal("0")
        documents.append({
            "id": document_id,
            "kind": kind,
            "number": str(row.get("doc_number") or "")[:80],
            "status": status,
            "issued": str(row.get("issue_date") or "")[:10],
            "due": str(row.get("due_date") or "")[:10],
            "currency": str(row.get("currency_symbol") or "₦")[:5],
            "total": str(total),
            "paid": str(paid),
            "balance": str(max(Decimal("0"), total - paid)),
        })
        if kind == "invoice":
            invoice_ids.append(document_id)

    payments = []
    if invoice_ids:
        paid_rows = gateway.get(
            "billing_payments",
            select="id,invoice_id,amount,paid_at,currency_symbol",
            filters={
                "invoice_id": "in.(" + ",".join(invoice_ids) + ")",
                "order": "paid_at.desc",
            },
        )
        known_invoices = set(invoice_ids)
        for row in paid_rows:
            if _valid_uuid(row.get("invoice_id")) not in known_invoices:
                continue
            if not _valid_uuid(row.get("id")):
                continue
            payments.append({
                "invoice_id": _valid_uuid(row["invoice_id"]),
                "amount": str(_money(row.get("amount"))),
                "paid_at": str(row.get("paid_at") or "")[:10],
                "currency": str(row.get("currency_symbol") or "₦")[:5],
            })

    return {"state": "linked", "documents": documents, "payments": payments}


def advisor_job_link_preflight(
    *, car_id: int, vin: str, owner_email: str | None, job_uuid: str
) -> dict:
    """Non-mutating advisor inspection. No link, publication or customer access.

    Job UUID is selected explicitly by the advisor. The same number or
    similar vehicle details alone are never sufficient to bind accounts.
    """
    job_id = _valid_uuid(job_uuid)
    aura_vin = _normalise_vin(vin)
    if not job_id or car_id <= 0 or len(aura_vin) != 17:
        return {"status": "invalid_input"}
    gateway = _BillingReadGateway()
    jobs = gateway.get(
        "billing_jobs",
        select="id,job_number,sow_number,status,vehicle_id,client_id",
        filters={"id": f"eq.{job_id}"},
    )
    if len(jobs) != 1 or _valid_uuid(jobs[0].get("id")) != job_id:
        return {"status": "not_found"}
    job = jobs[0]
    vehicle_id = _valid_uuid(job.get("vehicle_id"))
    client_id = _valid_uuid(job.get("client_id"))
    if not vehicle_id or not client_id:
        return {"status": "incomplete_reference"}

    vehicles = gateway.get(
        "billing_vehicles",
        select="id,client_id,vin,aura_vehicle_ref,make,model,year",
        filters={"id": f"eq.{vehicle_id}"},
    )
    if len(vehicles) != 1 or _valid_uuid(vehicles[0].get("id")) != vehicle_id:
        return {"status": "incomplete_reference"}
    vehicle = vehicles[0]
    if _valid_uuid(vehicle.get("client_id")) != client_id:
        return {"status": "client_mismatch"}
    if _normalise_vin(vehicle.get("vin")) != aura_vin:
        return {"status": "vin_mismatch"}
    existing_ref = str(vehicle.get("aura_vehicle_ref") or "").strip()
    if existing_ref and existing_ref != str(car_id):
        return {"status": "linked_elsewhere"}

    clients = gateway.get(
        "billing_clients",
        select="id,name,email",
        filters={"id": f"eq.{client_id}"},
    )
    if len(clients) != 1 or _valid_uuid(clients[0].get("id")) != client_id:
        return {"status": "incomplete_reference"}
    email = str(clients[0].get("email") or "").strip().casefold()
    match = bool(email and email == str(owner_email or "").strip().casefold())

    return {
        "status": "requires_advisor_identity_confirmation",
        "job_uuid": job_id,
        "job_reference": str(job.get("job_number") or "")[:80],
        "sow_reference": str(job.get("sow_number") or "")[:80],
        "commercial_status": str(job.get("status") or "")[:35],
        "vehicle": " ".join(
            str(vehicle.get(key) or "")[:35]
            for key in ("make", "model", "year")
        ).strip(),
        "vin_tail": aura_vin[-6:],
        "billing_client_name": str(clients[0].get("name") or "")[:100],
        "billing_email_status": (
            "matches" if match else "different" if email else "missing"
        ),
        "aura_ref_state": "already_set" if existing_ref else "not_linked",
        "decision": "manual_owner_identity_check_required",
    }
