"""Apply advisor-approved historical extraction facts to Aura's treatment domains.

Only explicitly accepted completed-work and outcome candidates are applied in this
slice. Other reviewed candidates remain governed context for the advisor/Rina and
are never silently converted into domain truth.
"""

from __future__ import annotations

from datetime import datetime, timezone

from evidence.models import EvidenceExtraction
from extensions import db
from historical_ingestion.service import (
    HistoricalIngestionError,
    decrypt_extraction_payload,
)
from models import TreatmentPlan
from security.access import resolve_vehicle_authority
from services.treatment_action_lifecycle import TreatmentActionLifecycleService
from services.treatment_evidence_linking import (
    link_accepted_evidence_to_treatment_subject,
)
from services.treatment_outcome_recording import TreatmentOutcomeRecordingService
from services.treatment_plan_lifecycle import TreatmentPlanLifecycleService
from treatment.models import TreatmentActionCompletionDetail


class HistoricalApplicationError(HistoricalIngestionError):
    pass


def _parse_when(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text + "T12:00:00")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _accepted_candidates(payload: dict) -> list[dict]:
    rows = payload.get("candidates") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    return [
        row
        for row in rows
        if isinstance(row, dict) and row.get("review_decision") == "accepted"
    ]


def _require_advisor(actor_user_id: int, car_id: int) -> None:
    if resolve_vehicle_authority(actor_user_id, car_id) not in {
        "advisor",
        "administrator",
    }:
        raise HistoricalApplicationError(
            "Historical record application requires advisor authority."
        )


def apply_reviewed_historical_treatment(
    *,
    extraction_id: int,
    actor_user_id: int,
) -> TreatmentPlan | None:
    extraction = db.session.get(EvidenceExtraction, extraction_id)
    if extraction is None or extraction.evidence is None:
        raise HistoricalApplicationError("Historical extraction was not found.")
    evidence = extraction.evidence
    _require_advisor(actor_user_id, evidence.car_id)

    if extraction.review_status not in {"accepted", "corrected"}:
        raise HistoricalApplicationError("Review the extraction before applying it.")
    if evidence.review_status != "accepted":
        raise HistoricalApplicationError(
            "Source evidence must be advisor-accepted first."
        )

    existing = TreatmentPlan.query.filter_by(
        source_extraction_id=extraction.id,
        record_origin="historical_document",
    ).first()
    if existing is not None:
        return existing

    reviewed = decrypt_extraction_payload(extraction, reviewed=True)
    accepted = _accepted_candidates(reviewed)
    work = [
        row
        for row in accepted
        if row.get("state") == "completed"
        and row.get("suggested_destination") == "treatment_action"
        and row.get("completion_confirmed") is True
    ]
    outcomes = [
        row
        for row in accepted
        if row.get("suggested_destination") == "treatment_outcome"
    ]

    if not work and not outcomes:
        return None

    missing_dates = [
        row.get("title") or "Completed work"
        for row in work
        if _parse_when(row.get("occurred_at")) is None
    ]
    if missing_dates:
        raise HistoricalApplicationError(
            "A completion date is required before completed work can become durable history: "
            + ", ".join(missing_dates[:5])
        )

    dated_work = [
        (row, _parse_when(row.get("occurred_at")))
        for row in work
    ]
    dated_work = [(row, when) for row, when in dated_work if when is not None]
    dated_outcomes = [
        (row, _parse_when(row.get("occurred_at")))
        for row in outcomes
    ]
    all_dates = [when for _, when in dated_work] + [
        when for _, when in dated_outcomes if when is not None
    ]
    if not all_dates:
        raise HistoricalApplicationError(
            "At least one reviewed historical date is required before applying treatment history."
        )

    start_at = min(all_dates)
    finish_at = max(all_dates)
    document = (
        reviewed.get("document")
        if isinstance(reviewed.get("document"), dict)
        else {}
    )
    reference = str(
        document.get("job_reference")
        or document.get("reference")
        or f"Evidence {evidence.id}"
    )

    plan = TreatmentPlan(
        car_id=evidence.car_id,
        advisor_id=actor_user_id,
        title=f"Historical care record — {reference}"[:255],
        internal_instructions=(
            "Advisor-reviewed historical import. Source evidence remains authoritative "
            f"for provenance; extraction #{extraction.id} is not autonomous diagnosis."
        ),
        client_summary=(
            str(reviewed.get("rina_summary") or "").strip()[:3000]
            or "Historical vehicle-care work reconstructed from advisor-reviewed records."
        ),
        status="approved",
        record_origin="historical_document",
        source_evidence_id=evidence.id,
        source_extraction_id=extraction.id,
        created_at=start_at.replace(tzinfo=None),
        updated_at=start_at.replace(tzinfo=None),
    )
    db.session.add(plan)
    db.session.flush()

    TreatmentPlanLifecycleService.start(
        plan_id=plan.id,
        actor_user_id=actor_user_id,
        occurred_at=start_at,
        source="historical_document_import",
        operation_key=f"historical-extraction-{extraction.id}-start",
    )

    for row, when in dated_work:
        candidate_id = str(row.get("candidate_id") or "candidate")[:64]
        title = str(row.get("title") or "Completed intervention").strip()[:255]
        detail = str(row.get("detail") or "").strip() or None
        visibility = (
            evidence.visibility
            if evidence.visibility in {"client", "advisor"}
            else "advisor"
        )
        action = TreatmentActionLifecycleService.create(
            plan_id=plan.id,
            actor_user_id=actor_user_id,
            creation_key=f"historical-extraction:{extraction.id}:{candidate_id}",
            title=title,
            client_summary=detail,
            internal_instructions=(
                "Historical completed-work fact approved by an advisor from source "
                f"evidence #{evidence.id}."
            ),
            visibility=visibility,
            occurred_at=when,
            source="historical_document_import",
        )
        TreatmentActionLifecycleService.schedule(
            action_id=action.id,
            actor_user_id=actor_user_id,
            scheduled_for=when,
            occurred_at=when,
            source="historical_document_import",
            operation_key=f"{candidate_id}-schedule",
        )
        TreatmentActionLifecycleService.start(
            action_id=action.id,
            actor_user_id=actor_user_id,
            occurred_at=when,
            source="historical_document_import",
            operation_key=f"{candidate_id}-start",
        )
        TreatmentActionLifecycleService.complete(
            action_id=action.id,
            actor_user_id=actor_user_id,
            occurred_at=when,
            source="historical_document_import",
            operation_key=f"{candidate_id}-complete",
        )

        action_meta = row.get("action") if isinstance(row.get("action"), dict) else {}
        detail_row = TreatmentActionCompletionDetail(
            treatment_action_id=action.id,
            action_kind=str(action_meta.get("kind") or "other_intervention"),
            component_name=(
                str(action_meta.get("component_name") or "").strip() or None
            ),
            component_location=(
                str(action_meta.get("component_location") or "").strip() or None
            ),
            component_condition=str(
                action_meta.get("component_condition") or "unknown"
            ),
            quantity=action_meta.get("quantity"),
            odometer_km=action_meta.get("odometer_km"),
            source_evidence_id=evidence.id,
            verification_status="advisor_confirmed",
            verified_by_user_id=actor_user_id,
            verified_at=datetime.utcnow(),
        )
        db.session.add(detail_row)
        db.session.flush()

        link_accepted_evidence_to_treatment_subject(
            actor_user_id=actor_user_id,
            evidence_id=evidence.id,
            subject_type="treatment_action",
            subject_id=action.id,
            relationship_type="documents",
        )

    TreatmentPlanLifecycleService.complete(
        plan_id=plan.id,
        actor_user_id=actor_user_id,
        occurred_at=finish_at,
        source="historical_document_import",
        operation_key=f"historical-extraction-{extraction.id}-complete",
    )

    for row, when in dated_outcomes:
        if when is None:
            continue
        direction = str(
            row.get("outcome_direction") or "insufficient_evidence"
        )
        visibility = (
            evidence.visibility
            if evidence.visibility in {"client", "advisor"}
            else "advisor"
        )
        TreatmentOutcomeRecordingService.record(
            plan_id=plan.id,
            actor_user_id=actor_user_id,
            recording_key=(
                f"historical-extraction:{extraction.id}:"
                f"{row.get('candidate_id')}"
            ),
            progression_direction=direction,
            summary=str(
                row.get("detail") or row.get("title") or "Historical outcome"
            ),
            provenance_kind="reviewed_evidence",
            evidence_ids=[evidence.id],
            advisor_note=(
                "Advisor-approved historical outcome extracted from source evidence."
            ),
            visibility=visibility,
            observed_at=when,
            occurred_at=when,
            source="historical_document_import",
        )

    db.session.flush()
    return plan
