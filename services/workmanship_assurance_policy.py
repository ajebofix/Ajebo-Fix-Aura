"""Ajebo Fix Workmanship Assurance — draft, advisor-only decision contract.

This module never grants, publishes, emails, or automatically infers a warranty.
It prepares a documented recommendation for human approval after legal review.
Do not convert readiness into a customer-facing promise.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Mapping


POLICY_VERSION = "AJEBO-WA-PROPOSED-2026-10-10"
LEGAL_STATUS = "pending_nigerian_legal_review"

# Decisions relate only to an ADDITIONAL voluntary contractual assurance.
# Statutory service rights and remedies continue irrespective of this choice.
DECISION_OPTIONS = frozenset(
    {
        "documented_service_no_additional_guarantee",
        "limited_additional_workmanship_assurance_proposed",
        "tailored_extended_assurance_proposed",
        "decline_or_redesign_unsafe_scope",
    }
)
COVERAGE_OPTIONS = frozenset(
    {
        "limited_additional_workmanship_assurance_proposed",
        "tailored_extended_assurance_proposed",
    }
)
PARTS_SOURCES = frozenset(
    {
        "new_oem",
        "new_aftermarket",
        "reconditioned",
        "used_local",
        "preowned_tokunbo",
        "client_supplied",
        "mixed",
        "not_applicable",
    }
)

REQUIRED_EVIDENCE_FIELDS = (
    "approved_scope",
    "vehicle_condition_baseline",
    "prior_damage_and_repairs",
    "parts_provenance_and_supplier",
    "seller_testing_return_terms",
    "materials_and_repair_method",
    "technical_risk_and_hidden_damage",
    "testing_and_quality_control",
    "client_choices_and_declined_recommendations",
    "environmental_use_and_aftercare",
    "advisor_reason",
)


class AssuranceProposalError(ValueError):
    """The internal record is incomplete or could imply an unsafe promise."""


def _text(value: object, *, limit: int = 2500) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def validate_proposed_assurance(
    record: Mapping[str, object],
) -> dict[str, object]:
    """Evaluate evidence completeness; never create an automatic guarantee.

    Review readiness is intentionally not client acceptance, warranty issuance,
    a statutory-rights waiver, or an approval to send/publish anything.
    """
    car_id, advisor_id = record.get("car_id"), record.get("advisor_user_id")
    if type(car_id) is not int or car_id < 1:
        raise AssuranceProposalError("A verified Aura vehicle is required")
    if type(advisor_id) is not int or advisor_id < 1:
        raise AssuranceProposalError("An identified advisor is required")
    job_reference = _text(record.get("job_reference"), limit=100)
    if not job_reference:
        raise AssuranceProposalError("Link the proposal to a specific job")

    try:
        labour = Decimal(str(record.get("approved_labour_ngn")))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise AssuranceProposalError("Record the agreed labour amount") from exc
    if not labour.is_finite() or labour < 0 or labour > Decimal("1000000000"):
        raise AssuranceProposalError("Invalid agreed labour amount")

    decision = _text(record.get("proposed_decision"), limit=80)
    if decision not in DECISION_OPTIONS:
        raise AssuranceProposalError("Select a recognised advisor proposal")
    parts_category = _text(record.get("parts_category"), limit=40)
    if parts_category not in PARTS_SOURCES:
        raise AssuranceProposalError("Identify the parts sourcing category")

    missing = [field for field in REQUIRED_EVIDENCE_FIELDS if not _text(record.get(field))]
    proposal_scope = _text(record.get("additional_coverage_scope"))
    proposed_start = _text(record.get("coverage_start_date"), limit=20)
    proposed_end = _text(record.get("coverage_end_date"), limit=20)
    coverage_conditions = _text(record.get("additional_coverage_conditions"))

    if decision in COVERAGE_OPTIONS:
        if not proposal_scope:
            missing.append("additional_coverage_scope")
        if not coverage_conditions:
            missing.append("additional_coverage_conditions")
        if not proposed_start or not proposed_end:
            missing.append("individual_coverage_dates")
        else:
            try:
                starts = date.fromisoformat(proposed_start)
                ends = date.fromisoformat(proposed_end)
            except ValueError as exc:
                raise AssuranceProposalError("Coverage dates must be valid ISO dates") from exc
            if ends <= starts:
                raise AssuranceProposalError("Individually agreed expiry must follow start")
    elif proposal_scope or proposed_start or proposed_end or coverage_conditions:
        raise AssuranceProposalError(
            "Do not attach optional coverage promises to no-guarantee/declined decisions"
        )

    # A client-supplied component is not an automatic coverage denial, and
    # a short seller guarantee never becomes the Ajebo Fix workmanship period.
    # Price and environmental exposure are recorded, never used for automatic
    # approval or blanket claim rejection.
    return {
        "policy_version": POLICY_VERSION,
        "legal_status": LEGAL_STATUS,
        "car_id": car_id,
        "job_reference": job_reference,
        "advisor_user_id": advisor_id,
        "proposed_decision": decision,
        "evidence_complete": not missing,
        "missing_evidence": tuple(missing),
        "review_status": "ready_for_human_review" if not missing else "needs_evidence",
        "requires_manual_approval": True,
        "additional_contractual_assurance_issued": False,
        "external_publication_permitted": False,
        "statutory_rights_unaffected": True,
    }
