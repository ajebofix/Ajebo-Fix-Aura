"""Fail-closed authorization primitives for the proposed Prince workshop pilot.

This module intentionally contains no routes or persistence integration.
Do not expose customer records through a new API until every read/search/export
and attachment endpoint is protected by server-side tenant and assignment checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class PrinceRole(str, Enum):
    PRINCIPAL = "principal"
    OPERATOR = "operator"
    PILOT_ADMIN = "pilot_admin"


class PrinceAction(str, Enum):
    INTAKE_CREATE = "intake_create"
    JOB_READ = "job_read"
    JOB_EDIT = "job_edit"
    MANAGEMENT_VIEW = "management_view"
    JOB_ASSIGN = "job_assign"
    JOB_TRANSITION = "job_transition"
    ESTIMATE_DRAFT = "estimate_draft"
    ESTIMATE_APPROVE = "estimate_approve"
    CUSTOMER_AUTHORIZATION = "customer_authorization"
    INVOICE_ISSUE = "invoice_issue"
    PAYMENT_REPORT = "payment_report"
    PAYMENT_VERIFY = "payment_verify"
    SERVICE_CONFIRM = "service_confirm"
    CONTACT_READ = "contact_read"
    CONTACT_CORRECT = "contact_correct"
    DIRECTORY_EXPORT = "directory_export"


# Deliberately minimal: a full name is still personal data.
OPERATOR_JOB_PUBLIC_FIELDS = frozenset({
    "customer_id", "full_name", "job_id", "vehicle_id",
    "brand", "model", "year", "job_status", "reported_concern", "work_authorization",
})
OPERATOR_INTAKE_INPUT_FIELDS = frozenset({
    "full_name", "vehicle_brand", "vehicle_model", "vehicle_year",
    "reported_concern", "privacy_notice_version", "participation_confirmed",
})
OPERATOR_INTAKE_RESPONSE_FIELDS = frozenset({"customer_id", "full_name", "job_id"})
OPERATOR_ALLOWED_ACTIONS = frozenset({
    PrinceAction.INTAKE_CREATE,
    PrinceAction.JOB_READ,
    PrinceAction.JOB_EDIT,
    PrinceAction.ESTIMATE_DRAFT,
    PrinceAction.PAYMENT_REPORT,
})


@dataclass(frozen=True)
class PrinceActor:
    user_id: int
    tenant_id: str
    role: PrinceRole
    active: bool = True


def authorize(
    actor: PrinceActor,
    *,
    tenant_id: str,
    action: PrinceAction,
    assigned_user_ids: frozenset[int] = frozenset(),
) -> bool:
    """Deny by default; every operation must be scoped to the exact tenant.

    assigned_user_ids is supplied by the database after an authoritative
    assignment lookup, NOT by the caller's request payload.
    """
    if not actor.active or not actor.tenant_id or actor.tenant_id != tenant_id:
        return False
    if actor.role == PrinceRole.PRINCIPAL:
        return True
    if actor.role == PrinceRole.PILOT_ADMIN:
        # Technical administrators do not inherit workshop ownership.
        # Customer contacts, financial approval and service attestation
        # require a principal membership, never a global Aura admin role.
        return action not in {
            PrinceAction.PAYMENT_VERIFY, PrinceAction.CONTACT_READ,
            PrinceAction.CONTACT_CORRECT, PrinceAction.SERVICE_CONFIRM,
            PrinceAction.ESTIMATE_APPROVE, PrinceAction.CUSTOMER_AUTHORIZATION,
            PrinceAction.INVOICE_ISSUE, PrinceAction.DIRECTORY_EXPORT,
            PrinceAction.MANAGEMENT_VIEW, PrinceAction.JOB_ASSIGN, PrinceAction.JOB_TRANSITION,
        }
    if actor.role != PrinceRole.OPERATOR or action not in OPERATOR_ALLOWED_ACTIONS:
        return False
    if action == PrinceAction.INTAKE_CREATE:
        return True
    return actor.user_id in assigned_user_ids


def redact_operator_job(record: Mapping[str, Any]) -> dict[str, Any]:
    """Positive allowlist; never serialize ORM entities for operators."""
    return {
        key: record[key]
        for key in OPERATOR_JOB_PUBLIC_FIELDS
        if key in record
    }


def operator_intake_response(record: Mapping[str, Any]) -> dict[str, Any]:
    """Never echo phone/email/address or full submitted request on save."""
    return {
        key: record[key]
        for key in OPERATOR_INTAKE_RESPONSE_FIELDS
        if key in record
    }


def validate_operator_intake_keys(payload: Mapping[str, Any]) -> bool:
    """Reject unexpected keys rather than silently persisting extras."""
    return bool(payload.get("full_name")) and set(payload) <= OPERATOR_INTAKE_INPUT_FIELDS
