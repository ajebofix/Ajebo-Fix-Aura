"""Deterministic safety gates for partner-funded campaign jobs.

This module is intentionally independent of Flask, ORM, external partners and
AI. Its booleans represent previously verified human/system facts; it does
NOT perform voucher validation or lubricant compatibility determination.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AppointmentReadiness:
    campaign_active: bool = False
    voucher_verified: bool = False
    human_technical_approval: bool = False
    product_received_and_accepted: bool = False
    capacity_reserved: bool = False
    technician_confirmed_for_window: bool = False
    customer_confirmed: bool = False


def appointment_blockers(readiness: AppointmentReadiness) -> tuple[str, ...]:
    """Why a tentative appointment may not become a confirmed appointment."""
    requirements = (
        ("campaign_inactive", readiness.campaign_active),
        ("voucher_unverified", readiness.voucher_verified),
        ("technical_approval_missing", readiness.human_technical_approval),
        ("products_not_accepted", readiness.product_received_and_accepted),
        ("capacity_not_reserved", readiness.capacity_reserved),
        ("technician_not_committed", readiness.technician_confirmed_for_window),
        ("customer_confirmation_missing", readiness.customer_confirmed),
    )
    return tuple(code for code, satisfied in requirements if not satisfied)


@dataclass(frozen=True)
class ServiceStartReadiness:
    appointment_confirmed: bool = False
    voucher_reserved_for_this_job: bool = False
    vehicle_identity_rechecked: bool = False
    correct_products_at_bay: bool = False
    authorised_technician_assigned: bool = False


def service_start_blockers(readiness: ServiceStartReadiness) -> tuple[str, ...]:
    """No real service may start from a merely tentative voucher request."""
    requirements = (
        ("appointment_unconfirmed", readiness.appointment_confirmed),
        ("voucher_not_reserved_for_job", readiness.voucher_reserved_for_this_job),
        ("vehicle_identity_not_rechecked", readiness.vehicle_identity_rechecked),
        ("products_not_at_bay", readiness.correct_products_at_bay),
        ("technician_not_assigned", readiness.authorised_technician_assigned),
    )
    return tuple(code for code, satisfied in requirements if not satisfied)


@dataclass(frozen=True)
class SettlementReadiness:
    agreed_rate_available: bool = False
    voucher_redemption_acknowledged: bool = False
    service_completed: bool = False
    service_evidence_reviewed: bool = False
    handover_confirmed: bool = False
    existing_claim: bool = False


def settlement_blockers(readiness: SettlementReadiness) -> tuple[str, ...]:
    """Guard partner labour claim eligibility; database uniqueness still required."""
    requirements = (
        ("rate_not_agreed", readiness.agreed_rate_available),
        ("voucher_redemption_not_confirmed", readiness.voucher_redemption_acknowledged),
        ("service_not_completed", readiness.service_completed),
        ("evidence_not_reviewed", readiness.service_evidence_reviewed),
        ("handover_unconfirmed", readiness.handover_confirmed),
        ("duplicate_claim", not readiness.existing_claim),
    )
    return tuple(code for code, satisfied in requirements if not satisfied)
