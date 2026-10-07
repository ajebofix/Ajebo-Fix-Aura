"""Advisor-proposed live collision repair Treatment Plans.

This service creates a proposed care pathway plus planned Treatment Actions for an
active collision-repair job. It never authorizes treatment on behalf of the owner and
never marks work complete. Callers own the transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone

from evidence.models import VehicleEvidence
from extensions import db
from models import TreatmentPlan
from security.access import resolve_vehicle_authority
from services.treatment_action_lifecycle import TreatmentActionLifecycleService
from services.treatment_event_emission import emit_treatment_plan_event


class LiveCollisionPlanError(ValueError):
    pass


_COLLISION_ACTIONS = (
    (
        "Pre-repair condition inspection and baseline documentation",
        "Document visible collision condition before dismantling. This action does not diagnose hidden damage.",
        "Capture baseline photos/video, visible damage, odometer/dashboard and unrelated pre-existing damage before dismantling.",
        "client",
    ),
    (
        "Dismantling and hidden-damage verification",
        "Expose affected front-end areas and document any additional damage before expanding the repair scope.",
        "Do not treat later-discovered damage as part of the original source document. Record it separately with evidence and advisor review.",
        "client",
    ),
    (
        "Front bumper condition decision and restoration",
        "Inspect the front bumper and carry out the approved repair or replacement path once suitability is confirmed.",
        "Client is supplying agreed replacement parts directly. Do not record a bumper as received, fitted or completed until separately evidenced.",
        "client",
    ),
    (
        "Bonnet condition decision and restoration",
        "Inspect bonnet damage and carry out the approved repair or replacement path once suitability is confirmed.",
        "Client-supplied part responsibility remains separate from Ajebo Fix labour and completion evidence.",
        "client",
    ),
    (
        "Left headlamp fitment and functional verification",
        "Confirm the supplied/available left headlamp is suitable before fitment, then verify basic operation after installation.",
        "Do not infer authenticity, compatibility, installation or successful operation from a purchase/payment record alone.",
        "client",
    ),
    (
        "Cooling-area inspection for condenser and radiator",
        "Inspect and test the condenser/radiator area before deciding whether repair or replacement is required.",
        "The source document lists condenser and radiator values but does not by itself prove either component requires replacement.",
        "client",
    ),
    (
        "Panel alignment and body restoration",
        "Restore affected body alignment and panel condition to the approved repair scope.",
        "Record measurable/visible findings separately from the fact that bodywork was performed.",
        "client",
    ),
    (
        "Paint and refinishing",
        "Refinish repaired/replaced exterior panels after bodywork and fitment checks are ready.",
        "Completion of paint work does not by itself establish successful mechanical or cooling-system operation.",
        "client",
    ),
    (
        "Reassembly",
        "Reassemble affected front-end/body components after approved repair work and parts fitment.",
        "Record each material deviation from the planned scope before closing this action.",
        "client",
    ),
    (
        "Functional checks and road test",
        "Perform appropriate post-repair functional checks and a road test where safe and applicable.",
        "Record outcomes separately. Do not equate action completion with vehicle-health resolution.",
        "client",
    ),
    (
        "Final quality inspection and delivery preparation",
        "Complete final repair-quality review, document remaining advisories and prepare the vehicle for handover.",
        "Delivery readiness must preserve unresolved findings and must not imply unrelated vehicle concerns were resolved.",
        "client",
    ),
)


def _require_advisor(car_id: int, actor_user_id: int) -> str:
    authority = resolve_vehicle_authority(actor_user_id, car_id)
    if authority not in {"advisor", "administrator"}:
        raise LiveCollisionPlanError(
            "Creating a live collision Treatment Plan requires advisor access."
        )
    return authority


def _source_evidence(car_id: int, evidence_id: int | None) -> VehicleEvidence | None:
    if evidence_id is None:
        return None
    evidence = db.session.get(VehicleEvidence, int(evidence_id))
    if evidence is None or evidence.car_id != int(car_id) or evidence.deleted_at is not None:
        raise LiveCollisionPlanError(
            "The selected source evidence is not available for this vehicle."
        )
    return evidence


def propose_live_collision_plan(
    *,
    car_id: int,
    actor_user_id: int,
    source_evidence_id: int | None = None,
    job_reference: str | None = None,
    client_supplies_parts: bool = True,
    source: str = "advisor.live_collision_plan",
) -> TreatmentPlan:
    """Create/reuse one proposed live collision plan and scaffold planned actions."""

    _require_advisor(car_id, actor_user_id)
    evidence = _source_evidence(car_id, source_evidence_id)

    reference = (job_reference or "").strip()
    title = "Collision Repair & Body Restoration"
    if reference:
        title = f"{title} — {reference}"[:255]

    existing_query = TreatmentPlan.query.filter_by(
        car_id=car_id,
        record_origin="live",
        title=title,
    )
    if evidence is not None:
        existing_query = existing_query.filter_by(source_evidence_id=evidence.id)
    plan = existing_query.order_by(TreatmentPlan.id.desc()).first()

    if plan is None:
        parts_note = (
            "The client will procure the agreed replacement parts directly. "
            if client_supplies_parts
            else ""
        )
        plan = TreatmentPlan(
            car_id=car_id,
            advisor_id=actor_user_id,
            title=title,
            client_summary=(
                "Live collision-repair and body-restoration pathway. "
                + parts_note
                + "Each listed intervention remains subject to inspection, suitability "
                "checks and separate documented completion."
            ).strip(),
            internal_instructions=(
                "Use Repair Journey for custody/location/progress updates; use Treatment "
                "Actions for professional interventions and Treatment Outcomes for what "
                "inspection/testing proves. "
                + (
                    "CLIENT-SUPPLIED PARTS: do not treat quoted/listed parts as Ajebo Fix "
                    "parts sales or as received/fitted merely because they appear in the "
                    "job document. Inspect apparent suitability before fitment; record "
                    "receipt, fitment and outcome separately. "
                    if client_supplies_parts
                    else ""
                )
                + "Source scope may mention bumper, left headlamp, bonnet, condenser and "
                "radiator, but the source alone does not prove each requires replacement."
            ),
            status="proposed",
            record_origin="live",
            source_evidence_id=(evidence.id if evidence is not None else None),
        )
        db.session.add(plan)
        db.session.flush()

        when = datetime.now(timezone.utc)
        emit_treatment_plan_event(
            car_id=car_id,
            plan_id=plan.id,
            event_type="treatment.proposed",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Live collision treatment plan proposed",
            previous_state=None,
            new_state="proposed",
            idempotency_key=f"treatment-plan:{plan.id}:proposed:live-collision",
            visibility="client",
            description=(
                "Advisor proposed a live collision-repair care pathway. "
                "Owner authorization remains separate."
            ),
            evidence_refs=(
                [{"type": "vehicle_evidence", "id": evidence.id}]
                if evidence is not None
                else []
            ),
            data={
                "job_reference": reference or None,
                "client_supplies_parts": bool(client_supplies_parts),
                "source_evidence_id": evidence.id if evidence is not None else None,
            },
        )

    for index, (action_title, client_summary, internal, visibility) in enumerate(
        _COLLISION_ACTIONS,
        start=1,
    ):
        TreatmentActionLifecycleService.create(
            plan_id=plan.id,
            actor_user_id=actor_user_id,
            creation_key=f"live-collision-v1:{index:02d}",
            title=action_title,
            client_summary=client_summary,
            internal_instructions=internal,
            visibility=visibility,
            source=source,
        )

    return plan
