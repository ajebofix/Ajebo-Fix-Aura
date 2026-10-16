"""Internal Gulf pilot preparation ledger.

Reference-only confirmations here are never contractual signatures, vehicle
fitment approvals, schedule confirmations, or campaign activation. All those
remain separate, explicit, operational gates.
"""
from dataclasses import dataclass

STATUSES = frozenset({
    "not_started", "waiting_on_partner", "in_progress", "confirmed", "blocked",
})

@dataclass(frozen=True)
class ReadinessRequirement:
    key: str
    title: str
    owner: str
    group: str
    description: str
    evidence_hint: str
    launch_critical: bool = True


REQUIREMENTS = (
    ReadinessRequirement(
        "agreement", "Signed partnership agreement", "Ebice + Ajebo Fix",
        "Commercial", "Identify the contracting entity, territory, responsibilities, quality and liability provisions.",
        "Signed agreement date and authorised signatories",
    ),
    ReadinessRequirement(
        "labour_rates", "Campaign labour pricing", "Ebice + Ajebo Fix",
        "Commercial", "Agree vehicle tiers, service inclusions, exception pricing and minimum economics in writing.",
        "Countersigned tariff schedule or acknowledged written quotation",
    ),
    ReadinessRequirement(
        "payment_terms", "Ebice payment and claims terms", "Ebice + Ajebo Fix",
        "Commercial", "Agree invoice/claim workflow, payee, payment timetable, disputes and reconciliation.",
        "Payment terms agreed in writing",
    ),
    ReadinessRequirement(
        "start_date", "Campaign start and first service day", "Ebice",
        "Campaign", "Clarify whether 1 November is marketing launch or first customer service date; Sunday opens only with explicit approval.",
        "Partner's written confirmation of first servicing date",
    ),
    ReadinessRequirement(
        "redemption", "Voucher verification and customer rules", "Ebice",
        "Campaign", "Confirm issued voucher format, fraud checks, expiry, redemptions and customer communication.",
        "Voucher/redemption procedure approved by partner",
    ),
    ReadinessRequirement(
        "oil_range", "Gulf oil range and specifications", "Ebice + Ajebo Fix",
        "Technical", "Obtain product technical sheets and confirm eligible grades/specifications per vehicle.",
        "Product sheets and technician-approved technical scope",
    ),
    ReadinessRequirement(
        "filter_supply", "Sakura supplier and stock", "Ebice + Distributor",
        "Technical", "Verify actual Nigerian supplier, Sakura SKUs, availability, authenticity and dispatch origin.",
        "Supplier catalogue and written stock/fulfilment confirmation",
    ),
    ReadinessRequirement(
        "fitment", "Vehicle-specific filter fitment procedure", "Ajebo Fix + Distributor",
        "Technical", "Agree mandatory VIN/engine/OE matching; reject unverified alternatives. Manufacturer catalogue candidates are not installation approvals.",
        "Documented VIN/OE compatibility workflow",
    ),
    ReadinessRequirement(
        "inspection", "Ten-point inspection scope", "Ebice + Ajebo Fix",
        "Service", "Confirm actual ten items, wording, exceptions and what is not included (not a comprehensive paid vehicle assessment).",
        "Agreed ten-point inspection checklist",
    ),
    ReadinessRequirement(
        "dispatch", "Yaba dispatch and delivery procedure", "Ebice",
        "Operations", "Specify notice period, receiving check, shortage/wrong-item procedure and customer rescheduling.",
        "Approved delivery and rejected-product process",
    ),
    ReadinessRequirement(
        "equipment", "Workshop equipment and safety check", "Ajebo Fix",
        "Operations", "Document oil handling, safe access, torque tools, spill controls, service reset and vehicle exclusions.",
        "Dated workshop readiness inspection and exclusions",
    ),
    ReadinessRequirement(
        "service_rehearsal", "Synthetic end-to-end service rehearsal", "Ajebo Fix",
        "Operations", "Test enquiry, eligibility, dispatch, scheduling, documented work, handover and settlement with fictional data.",
        "Staging test results and staff walk-through",
    ),
    ReadinessRequirement(
        "technicians", "Qualified technician roster", "Ajebo Fix",
        "Workforce", "Review existing technicians' qualifications and intern supervision. Do not request shifts before the campaign start is confirmed.",
        "Current roster and qualifications reviewed",
    ),
    ReadinessRequirement(
        "branding", "Coverall and banner artwork approval", "Ebice + Ajebo Fix",
        "Branding", "Approve artwork, accurate service messaging, partner role and removal rights. No brand use without approval.",
        "Approved proof/mock-up and permitted brand placement", False,
    ),
)

BY_KEY = {r.key: r for r in REQUIREMENTS}
DEFAULT_STATUS = {
    "agreement": "waiting_on_partner",
    "labour_rates": "waiting_on_partner",
    "payment_terms": "waiting_on_partner",
    "start_date": "waiting_on_partner",
    "redemption": "waiting_on_partner",
    "oil_range": "waiting_on_partner",
    "filter_supply": "waiting_on_partner",
    "fitment": "in_progress",
    "inspection": "waiting_on_partner",
    "dispatch": "waiting_on_partner",
    "equipment": "not_started",
    "service_rehearsal": "not_started",
    "technicians": "in_progress",
    "branding": "waiting_on_partner",
}

def readiness_summary(entries):
    """Summarise manual status only, never imply a launch authorisation."""
    data = {x.key: x.status for x in entries}
    critical = [r for r in REQUIREMENTS if r.launch_critical]
    confirmed = sum(data.get(r.key, DEFAULT_STATUS[r.key]) == "confirmed" for r in critical)
    return {"confirmed": confirmed, "required": len(critical),
            "outstanding": len(critical) - confirmed}


def launch_configuration(config):
    """Read actual operational activation controls without modifying them."""
    checklist = config.get("AURA_GULF_CHECKLIST_ITEMS") or []
    checks = {
        "Campaign switch": bool(config.get("AURA_GULF_CAMPAIGN_ACTIVE", False)),
        "Signed partner approval flag": bool(config.get("AURA_GULF_PARTNER_SIGNED", False)),
        "Approved filter approval flag": bool(config.get("AURA_GULF_FILTERS_APPROVED", False)),
        "Agreed rates approval flag": bool(config.get("AURA_GULF_RATES_APPROVED", False)),
        "Ten distinct inspection items configured": (
            isinstance(checklist, (list, tuple))
            and len(checklist) == 10 and len(set(checklist)) == 10
        ),
        "Car labour rate configured": bool(config.get("AURA_GULF_CAR_FEE_NGN")),
        "SUV labour rate configured": bool(config.get("AURA_GULF_SUV_FEE_NGN")),
    }
    return checks
