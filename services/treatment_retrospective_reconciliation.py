"""Advisor-reviewed retrospective reconciliation of one existing live Treatment Plan.

This is an append-only record of retrospective *attestations*, not a shortcut
around owner consent or canonical Treatment Action transitions.
"""
from __future__ import annotations

from datetime import date, datetime

from evidence.models import VehicleEvidence
from extensions import db
from models import TreatmentPlan
from security.access import resolve_vehicle_authority
from treatment.models import TreatmentAction
from treatment.reconciliation_models import TreatmentReconciliation


class RetrospectiveReconciliationError(ValueError):
    pass


def _plan_for_actor(plan_id: int, actor_user_id: int) -> TreatmentPlan:
    plan = db.session.get(TreatmentPlan, int(plan_id))
    if plan is None:
        raise RetrospectiveReconciliationError("Treatment Plan not found.")
    if resolve_vehicle_authority(actor_user_id, plan.car_id) not in {
        "advisor", "administrator",
    }:
        raise RetrospectiveReconciliationError("Advisor vehicle authority is required.")
    if plan.record_origin != "live" or plan.status not in {
        "proposed", "authorized", "scheduled", "in_progress", "monitoring",
    }:
        raise RetrospectiveReconciliationError(
            "Retrospective review is available only for existing live, nonterminal plans."
        )
    return plan


def _date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise RetrospectiveReconciliationError(
            "Enter a valid historical date, or leave it empty if unknown."
        ) from exc
    if parsed > datetime.utcnow().date():
        raise RetrospectiveReconciliationError(
            "A retrospective event cannot have a future occurrence date."
        )
    return parsed


def _evidence(value: str | None, car_id: int) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        evidence_id = int(raw)
    except ValueError as exc:
        raise RetrospectiveReconciliationError("Select a valid accepted evidence record.") from exc
    row = db.session.get(VehicleEvidence, evidence_id)
    if (
        row is None or row.car_id != car_id or row.deleted_at is not None
        or row.review_status != "accepted" or row.storage_state != "available"
    ):
        raise RetrospectiveReconciliationError(
            "Evidence must belong to this vehicle and be accepted and available."
        )
    return evidence_id


def record_reconciliation(
    *,
    plan_id: int,
    actor_user_id: int,
    kind: str,
    finding: str,
    provenance: str,
    description: str,
    operation_key: str,
    action_id: int | None = None,
    occurred_on: str | None = None,
    evidence_id: str | None = None,
    recipient: str | None = None,
    location: str | None = None,
) -> tuple[TreatmentReconciliation, bool]:
    plan = _plan_for_actor(plan_id, actor_user_id)
    kind = (kind or "").strip()
    finding = (finding or "").strip()
    provenance = (provenance or "").strip()
    description = (description or "").strip()
    operation_key = (operation_key or "").strip()
    if len(operation_key) < 12 or len(operation_key) > 128:
        raise RetrospectiveReconciliationError("A valid form operation key is required.")
    if len(description) < 12 or len(description) > 3000:
        raise RetrospectiveReconciliationError("Enter 12–3000 characters explaining what was verified.")
    if provenance not in {"advisor_firsthand", "reviewed_evidence", "client_report"}:
        raise RetrospectiveReconciliationError("Choose the actual source of the information.")
    occurred = _date(occurred_on)
    evidence_pk = _evidence(evidence_id, plan.car_id)
    if provenance == "reviewed_evidence" and evidence_pk is None:
        raise RetrospectiveReconciliationError(
            "Reviewed-evidence provenance requires an accepted evidence attachment."
        )
    if provenance == "client_report" and finding in {"performed", "handed_over"}:
        raise RetrospectiveReconciliationError(
            "Client statements alone cannot verify work or handover."
        )

    recipient = (recipient or "").strip()[:200] or None
    location = (location or "").strip()[:240] or None
    if kind == "action":
        if finding not in {"performed", "not_performed", "unverified"}:
            raise RetrospectiveReconciliationError("Select a valid action finding.")
        action = db.session.get(TreatmentAction, action_id) if action_id else None
        if action is None or action.treatment_plan_id != plan.id or action.car_id != plan.car_id:
            raise RetrospectiveReconciliationError("Action does not belong to this Treatment Plan.")
        if action.status in {"completed", "cancelled"}:
            raise RetrospectiveReconciliationError(
                "Already terminal actions must be reviewed through their existing history."
            )
        recipient = None
        location = None
    elif kind == "handover":
        if finding != "handed_over" or action_id is not None or not recipient:
            raise RetrospectiveReconciliationError(
                "Handover requires recipient and verified transfer of custody."
            )
        if provenance == "client_report":
            raise RetrospectiveReconciliationError(
                "Client report alone cannot verify handover."
            )
    else:
        raise RetrospectiveReconciliationError("Unknown reconciliation type.")

    existing = TreatmentReconciliation.query.filter_by(operation_key=operation_key).first()
    if existing is not None:
        if (
            existing.treatment_plan_id != plan.id
            or existing.actor_user_id != actor_user_id
            or existing.kind != kind
            or existing.finding != finding
            or existing.treatment_action_id != action_id
            or existing.description != description
            or existing.provenance != provenance
            or existing.occurred_on != occurred
            or existing.evidence_id != evidence_pk
            or existing.recipient != recipient
            or existing.location != location
        ):
            raise RetrospectiveReconciliationError(
                "This operation key was already used for different information."
            )
        return existing, False

    record = TreatmentReconciliation(
        treatment_plan_id=plan.id,
        car_id=plan.car_id,
        treatment_action_id=action_id,
        actor_user_id=actor_user_id,
        kind=kind,
        finding=finding,
        provenance=provenance,
        occurred_on=occurred,
        description=description,
        evidence_id=evidence_pk,
        recipient=recipient,
        location=location,
        operation_key=operation_key,
    )
    db.session.add(record)
    db.session.flush()
    return record, True


def reconciliation_overview(plan_id: int):
    """Return latest attestation per action plus full immutable history."""
    history = (
        TreatmentReconciliation.query.filter_by(treatment_plan_id=int(plan_id))
        .order_by(TreatmentReconciliation.created_at.asc(), TreatmentReconciliation.id.asc())
        .all()
    )
    latest_actions = {}
    handover = None
    for record in history:
        if record.kind == "action":
            latest_actions[record.treatment_action_id] = record
        else:
            handover = record
    return latest_actions, handover, history
