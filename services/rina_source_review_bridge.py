"""Bridge a completed standalone historical document analysis into supervised Rina review.

The source analyzer produces candidate evidence only. This bridge copies only the
minimum source-supported work candidates into a historical-reconciliation draft so
an Ajebo Fix advisor can review them conversationally. No durable vehicle history
is written here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from evidence.models import EvidenceExtraction, VehicleEvidence
from extensions import db
from historical_ingestion.service import (
    _payload_cipher,
    decrypt_extraction_payload,
    latest_structured_extraction,
)
from services.rina_context_resolver import RinaResolvedContext


DIRECT_STANDALONE_SOURCE_PIPELINE = "standalone_document_conversational_review_v1"

_ALLOWED_KINDS = {"service", "component_replacement", "other_intervention"}
_ALLOWED_CONDITIONS = {
    "new",
    "preowned_tokunbo",
    "refurbished",
    "client_supplied",
    "unknown",
    "not_applicable",
}


def _clip(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _existing_draft(
    *,
    evidence_id: int,
    structured_extraction_id: int,
) -> EvidenceExtraction | None:
    rows = (
        EvidenceExtraction.query.filter_by(
            evidence_id=int(evidence_id),
            extraction_type="historical_reconciliation",
        )
        .order_by(EvidenceExtraction.id.desc())
        .limit(80)
        .all()
    )
    for row in rows:
        provenance = row.provenance or {}
        if (
            provenance.get("analysis_pipeline") == DIRECT_STANDALONE_SOURCE_PIPELINE
            and int(provenance.get("source_structured_extraction_id") or 0)
            == int(structured_extraction_id)
            and row.status == "completed"
        ):
            return row
    return None


def _episode_dates(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    values = sorted(
        {
            _clip(item.get("occurred_at"), 64)
            for item in (payload.get("candidates") or [])
            if isinstance(item, dict) and _clip(item.get("occurred_at"), 64)
        }
    )
    if values:
        return values[0], values[-1]

    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    document_date = _clip(document.get("document_date"), 64)
    if document_date:
        return document_date, document_date
    return None, None


def _source_excerpt(payload: dict[str, Any]) -> str:
    excerpts: list[str] = []
    for item in payload.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        excerpt = _clip(item.get("source_excerpt"), 500)
        if excerpt and excerpt not in excerpts:
            excerpts.append(excerpt)
        if len(excerpts) >= 3:
            break
    if excerpts:
        return " | ".join(excerpts)[:1800]
    return _clip(payload.get("rina_summary"), 1800)


def _work_candidates(
    *,
    payload: dict[str, Any],
    evidence_id: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in payload.get("candidates") or []:
        if not isinstance(source, dict) or source.get("category") != "work_item":
            continue

        # A standalone source may describe paid/authorised/recommended scope without
        # proving completion. Those rows are valuable intake context, but they must
        # never be shaped like completed work merely because the advisor is reviewing
        # the source. Only source-classified completed work can seed a recordable
        # candidate; otherwise Rina enters clarification/intake mode.
        if _clip(source.get("state"), 40) != "completed":
            continue

        action = source.get("action") if isinstance(source.get("action"), dict) else {}
        kind = _clip(action.get("kind"), 40)
        if kind not in _ALLOWED_KINDS:
            continue

        condition = _clip(action.get("component_condition"), 40)
        if condition not in _ALLOWED_CONDITIONS:
            condition = "unknown" if kind == "component_replacement" else "not_applicable"

        state = _clip(source.get("state"), 40)
        evidence_state = {
            "completed": "completion_claim",
            "authorized": "authorized",
            "recommended": "recommended",
        }.get(state, "uncertain")

        source_refs = [f"DOCUMENT evidence:{int(evidence_id)}"]
        source_refs.extend(
            _clip(ref, 120)
            for ref in (source.get("source_fact_ids") or [])[:12]
            if _clip(ref, 120)
        )

        title = _clip(source.get("title"), 255) or "Historical work item"
        basis = (
            _clip(source.get("detail"), 1800)
            or _clip(source.get("source_excerpt"), 1800)
            or "Source-supported historical work candidate."
        )
        rows.append(
            {
                "candidate_id": f"D{len(rows) + 1:03d}",
                "title": title,
                "kind": kind,
                "component_name": _clip(action.get("component_name"), 255) or None,
                "component_location": _clip(action.get("component_location"), 120) or None,
                "suggested_occurred_at": _clip(source.get("occurred_at"), 64) or None,
                "occurred_at": None,
                "evidence_state": evidence_state,
                "source_refs": source_refs,
                "evidence_basis": basis,
                "confidence": source.get("confidence"),
                "reconciliation_reason": (
                    "This item came from a completed standalone-document analysis. "
                    f"The source classified it as {state or 'unknown'}; advisor "
                    "confirmation is required before any durable history write."
                ),
                "advisor_decision": "unsure",
                "component_condition": condition,
                "advisor_note": "",
                "reviewed_by_advisor": False,
            }
        )
    return rows[:40]


def stage_standalone_source_review(
    *,
    context: RinaResolvedContext,
    evidence_id: int,
    actor_user_id: int,
) -> EvidenceExtraction:
    """Create or reuse a candidate-only reconciliation draft for one source."""

    evidence = db.session.get(VehicleEvidence, int(evidence_id))
    if (
        evidence is None
        or evidence.car_id != int(context.car_id)
        or evidence.deleted_at is not None
        or evidence.evidence_type != "document"
        or evidence.historical_source_type != "standalone_document"
    ):
        raise ValueError("That standalone historical source is not available for this vehicle.")

    structured = latest_structured_extraction(evidence.id)
    if (
        structured is None
        or structured.status != "completed"
        or structured.extraction_type != "structured_fields"
    ):
        raise ValueError("Rina needs a completed document analysis before conversational review.")

    existing = _existing_draft(
        evidence_id=evidence.id,
        structured_extraction_id=structured.id,
    )
    if existing is not None:
        return existing

    payload = decrypt_extraction_payload(structured, reviewed=False)
    if not isinstance(payload, dict):
        raise ValueError("The completed document analysis is unavailable.")

    rows = _work_candidates(payload=payload, evidence_id=evidence.id)
    date_start, date_end = _episode_dates(payload)
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    episode_title = (
        _clip(document.get("title"), 255)
        or _clip(document.get("job_reference"), 255)
        or _clip(evidence.safe_display_name, 255)
        or "Historical document review"
    )

    draft = {
        "schema_version": 1,
        "summary": _clip(payload.get("rina_summary"), 1800),
        "source_excerpt": _source_excerpt(payload),
        "advisor_notice": (
            "This conversational draft was staged from Rina's completed document "
            "analysis. Source claims remain candidate evidence until the Ajebo Fix "
            "advisor confirms what actually happened."
        ),
        "episode_title": episode_title,
        "episode_date_start": date_start,
        "episode_date_end": date_end,
        "source_document_reference": _clip(document.get("reference"), 120) or None,
        "advisor_intake_required": not bool(rows),
        "advisor_intake_resolved": bool(rows),
        "advisor_intake_outcome": None,
        "candidates": rows,
        "advisor_context_notes": [],
    }

    cipher, version, digest = _payload_cipher(draft)
    row = EvidenceExtraction(
        evidence_id=evidence.id,
        extraction_type="historical_reconciliation",
        provider="aura",
        provider_model="standalone-document-conversational-bridge-v1",
        status="completed",
        review_status="unreviewed",
        result_ciphertext=cipher,
        result_key_version=version,
        result_sha256=digest,
        completed_at=datetime.utcnow(),
        provenance={
            "analysis_pipeline": DIRECT_STANDALONE_SOURCE_PIPELINE,
            "background_stage": "completed",
            "source_structured_extraction_id": structured.id,
            "episode_title": episode_title,
            "selected_car_id": int(context.car_id),
            "staged_by_user_id": int(actor_user_id),
            "semantic_authority": "candidate_only",
            "schema_version": 1,
        },
    )
    db.session.add(row)
    db.session.flush()
    return row


def direct_source_review_drafts_for_car(car_id: int) -> list[EvidenceExtraction]:
    rows = (
        EvidenceExtraction.query.join(
            VehicleEvidence,
            VehicleEvidence.id == EvidenceExtraction.evidence_id,
        )
        .filter(
            VehicleEvidence.car_id == int(car_id),
            EvidenceExtraction.extraction_type == "historical_reconciliation",
            EvidenceExtraction.status == "completed",
        )
        .order_by(EvidenceExtraction.id.desc())
        .limit(120)
        .all()
    )
    return [
        row
        for row in rows
        if (row.provenance or {}).get("analysis_pipeline")
        == DIRECT_STANDALONE_SOURCE_PIPELINE
    ]
