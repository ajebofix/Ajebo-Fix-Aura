"""Pure preflight policy for a future owner-initiated clinical transfer.

This module does not query global Aura cars, create ownership, consume proof
tokens, or publish events. Inputs must come from trusted server-side records
and a separately authenticated owner-confirmation flow, never a Prince form.
Passing the policy is not proof of legal title or permission to publish.
"""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class IdentityContext:
    tenant_id: int
    job_id: int
    customer_id: int
    vehicle_id: int
    handoff_id: int
    source_vin: str | None
    source_vin_verified: bool
    handoff_active: bool
    destination_car_id: int
    destination_vin: str | None
    ownership_id: int
    ownership_car_id: int
    ownership_user_id: int
    ownership_active: bool
    ownership_ended: bool
    active_ownership_count: int
    authenticated_owner_id: int
    owner_account_active: bool
    owner_account_verified: bool


@dataclass(frozen=True)
class OwnerConfirmation:
    """Trusted, independently obtained confirmation; not a caller DTO."""
    tenant_id: int
    job_id: int
    customer_id: int
    vehicle_id: int
    handoff_id: int
    car_id: int
    ownership_id: int
    owner_user_id: int
    customer_identity_verified: bool
    exact_projection_consent: bool
    current_and_unrevoked: bool


def normalize_vin(value):
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", normalized) else None


def reconciliation_blockers(context: IdentityContext, confirmation: OwnerConfirmation | None):
    """Return all blocking reasons; empty tuple means preflight only."""
    blocked = []
    source, destination = normalize_vin(context.source_vin), normalize_vin(context.destination_vin)
    if not context.handoff_active:
        blocked.append("clinical_consent_missing_or_withdrawn")
    if not source or not context.source_vin_verified:
        blocked.append("verified_workshop_vin_required")
    if not destination or not source or destination != source:
        blocked.append("exact_vin_match_required")
    if (not context.ownership_active or context.ownership_ended
            or context.active_ownership_count != 1
            or context.ownership_car_id != context.destination_car_id):
        blocked.append("unique_current_ownership_required")
    if (not context.owner_account_active or not context.owner_account_verified
            or context.authenticated_owner_id != context.ownership_user_id):
        blocked.append("authenticated_current_owner_required")
    if confirmation is None:
        blocked.append("separate_owner_confirmation_required")
    else:
        expected = (context.tenant_id, context.job_id, context.customer_id, context.vehicle_id,
                    context.handoff_id, context.destination_car_id, context.ownership_id,
                    context.authenticated_owner_id)
        supplied = (confirmation.tenant_id, confirmation.job_id, confirmation.customer_id,
                    confirmation.vehicle_id, confirmation.handoff_id, confirmation.car_id,
                    confirmation.ownership_id, confirmation.owner_user_id)
        if expected != supplied:
            blocked.append("confirmation_scope_mismatch")
        if not confirmation.customer_identity_verified:
            blocked.append("workshop_customer_identity_not_verified")
        if not confirmation.exact_projection_consent or not confirmation.current_and_unrevoked:
            blocked.append("current_projection_consent_required")
    return tuple(blocked)
