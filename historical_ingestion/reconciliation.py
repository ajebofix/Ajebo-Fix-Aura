"""Advisor-governed reconciliation of historical episode work.

Rina proposes missing historical interventions from an already-completed case
attribution. The advisor explicitly confirms what really happened. Only a
separate apply step writes confirmed work into durable TreatmentAction history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any

from flask import current_app

from evidence.models import EvidenceExtraction
from extensions import db
from historical_ingestion.case_attribution import (
    attribution_payload,
    episode_anchor_context,
)
from historical_ingestion.models import HistoricalServiceEpisode
from historical_ingestion.service import (
    HistoricalIngestionError,
    _authority,
    _payload_cipher,
    _trusted_vehicle_context,
    _utcnow_naive,
    decrypt_extraction_payload,
)
from historical_ingestion.whatsapp_bundle_analyzer import (
    WhatsAppBundleAdvisorAnalyzer,
)
from models import TreatmentPlan
from rina.providers.base import (
    RinaProviderError,
    RinaProviderTransientError,
)
from services.treatment_action_lifecycle import TreatmentActionLifecycleService
from services.treatment_plan_lifecycle import TreatmentPlanLifecycleService
from treatment.models import TreatmentActionCompletionDetail


PIPELINE = "historical_episode_reconciliation_v1"
_ALLOWED_KINDS = {
    "component_replacement",
    "service",
    "other_intervention",
}
_ALLOWED_EVIDENCE_STATES = {
    "recommended",
    "authorized",
    "workshop_claim",
    "completion_claim",
    "uncertain",
}
_ALLOWED_DECISIONS = {"confirmed", "not_done", "unsure"}
_ALLOWED_CONDITIONS = {
    "new",
    "preowned_tokunbo",
    "refurbished",
    "client_supplied",
    "unknown",
    "not_applicable",
}
_RECONCILIATION_SIGNAL_ROLES = {
    "recommendation",
    "authorization",
    "completed_work",
    "observation",
}
_REF_RE = re.compile(
    r"\b(?:CHAT m\d{6}|(?:IMAGE|DOCUMENT|AUDIO|VIDEO|TRANSCRIPT) evidence:\d+)\b"
)


class HistoricalReconciliationError(HistoricalIngestionError):
    """Safe failure for historical episode reconciliation."""


@dataclass(frozen=True)
class HistoricalReconciliationStart:
    episode_id: int
    attribution_extraction_id: int
    extraction_id: int
    status: str
    phase: str
    reused_existing: bool = False


@dataclass(frozen=True)
class HistoricalReconciliationStatus:
    episode_id: int
    extraction_id: int
    status: str
    phase: str
    message: str
    review_ready: bool = False


def _clip(value: object, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


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


def reconciliation_signal(payload: dict[str, Any]) -> bool:
    """Cheap deterministic signal that an attribution may need human reconciliation."""

    groups = payload.get("evidence_groups") if isinstance(payload, dict) else None
    if not isinstance(groups, list):
        return False
    for row in groups:
        if not isinstance(row, dict):
            continue
        if row.get("classification") != "matched":
            continue
        role = str(row.get("evidence_role") or "").strip().lower()
        if role in _RECONCILIATION_SIGNAL_ROLES:
            return True
    return False


def _known_attribution_refs(payload: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    groups = payload.get("evidence_groups")
    if not isinstance(groups, list):
        return refs
    for row in groups:
        if not isinstance(row, dict):
            continue
        for value in row.get("source_refs") or []:
            refs.update(_REF_RE.findall(str(value or "")))
    return refs


def durable_work_for_episode(
    episode: HistoricalServiceEpisode,
) -> list[dict[str, Any]]:
    plan = TreatmentPlan.query.filter_by(
        source_extraction_id=episode.anchor_extraction_id,
        record_origin="historical_document",
    ).first()
    if plan is None:
        return []

    rows: list[dict[str, Any]] = []
    for action in getattr(plan, "actions", []) or []:
        detail = getattr(action, "completion_detail", None)
        rows.append(
            {
                "treatment_action_id": action.id,
                "title": action.title,
                "completed_at": (
                    action.completed_at.isoformat()
                    if action.completed_at is not None
                    else None
                ),
                "kind": getattr(detail, "action_kind", None),
                "component_name": getattr(detail, "component_name", None),
                "component_location": getattr(detail, "component_location", None),
                "component_condition": getattr(detail, "component_condition", None),
                "verification_status": getattr(detail, "verification_status", None),
                "source_evidence_id": getattr(detail, "source_evidence_id", None),
                "addenda": [
                    {
                        "id": addendum.id,
                        "category": addendum.category,
                        "reason": addendum.reason,
                        "visibility": addendum.visibility,
                        "detail_text": addendum.detail_text,
                        "created_at": (
                            addendum.created_at.isoformat()
                            if addendum.created_at is not None
                            else None
                        ),
                        "created_by_user_id": addendum.created_by_user_id,
                        "created_by_name": (
                            (addendum.created_by.name or addendum.created_by.email)
                            if addendum.created_by is not None
                            else "Advisor"
                        ),
                    }
                    for addendum in (getattr(action, "addenda", []) or [])
                ],
            }
        )
    return rows


def latest_reconciliation(
    *,
    episode_id: int,
    attribution_extraction_id: int,
) -> EvidenceExtraction | None:
    attribution = db.session.get(EvidenceExtraction, attribution_extraction_id)
    if attribution is None:
        return None
    rows = (
        EvidenceExtraction.query.filter_by(
            evidence_id=attribution.evidence_id,
            extraction_type="historical_reconciliation",
        )
        .order_by(EvidenceExtraction.id.desc())
        .limit(40)
        .all()
    )
    for row in rows:
        provenance = row.provenance or {}
        if (
            provenance.get("analysis_pipeline") == PIPELINE
            and int(provenance.get("episode_id") or 0) == int(episode_id)
            and int(provenance.get("attribution_extraction_id") or 0)
            == int(attribution_extraction_id)
        ):
            return row
    return None


def _normalise_reconciliation_payload(
    payload: dict[str, Any],
    *,
    attribution: dict[str, Any],
) -> dict[str, Any]:
    known_refs = _known_attribution_refs(attribution)
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    raw_candidates = payload.get("candidates")
    if isinstance(raw_candidates, list):
        for index, raw in enumerate(raw_candidates[:80], start=1):
            if not isinstance(raw, dict):
                continue
            candidate_id = _clip(raw.get("candidate_id"), limit=24) or f"R{index:03d}"
            if candidate_id in seen_ids:
                candidate_id = f"R{index:03d}"
            seen_ids.add(candidate_id)

            kind = _clip(raw.get("kind"), limit=40).lower()
            if kind not in _ALLOWED_KINDS:
                kind = "other_intervention"

            state = _clip(raw.get("evidence_state"), limit=40).lower()
            if state not in _ALLOWED_EVIDENCE_STATES:
                state = "uncertain"

            refs: list[str] = []
            for value in raw.get("source_refs") or []:
                for ref in _REF_RE.findall(str(value or "")):
                    if ref in known_refs and ref not in refs:
                        refs.append(ref)
            if not refs:
                continue

            try:
                confidence = float(raw.get("confidence"))
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = min(max(confidence, 0.0), 1.0)

            rows.append(
                {
                    "candidate_id": candidate_id,
                    "title": _clip(raw.get("title"), limit=255)
                    or f"Historical reconciliation item {index}",
                    "kind": kind,
                    "component_name": _clip(raw.get("component_name"), limit=255) or None,
                    "component_location": _clip(raw.get("component_location"), limit=120) or None,
                    "suggested_occurred_at": _clip(
                        raw.get("suggested_occurred_at"),
                        limit=64,
                    )
                    or None,
                    "evidence_state": state,
                    "source_refs": refs[:30],
                    "evidence_basis": _clip(raw.get("evidence_basis"), limit=1800),
                    "confidence": confidence,
                    "reconciliation_reason": _clip(
                        raw.get("reconciliation_reason"),
                        limit=1600,
                    ),
                    "advisor_decision": "unsure",
                    "component_condition": (
                        "unknown" if kind == "component_replacement" else "not_applicable"
                    ),
                    "advisor_note": "",
                }
            )

    return {
        "schema_version": 1,
        "summary": _clip(payload.get("summary"), limit=3000),
        "advisor_notice": _clip(payload.get("advisor_notice"), limit=2200),
        "candidates": rows,
    }


def start_episode_reconciliation(
    *,
    episode_id: int,
    attribution_extraction_id: int,
    actor_user_id: int,
    analyzer: WhatsAppBundleAdvisorAnalyzer | None = None,
) -> HistoricalReconciliationStart:
    episode = db.session.get(HistoricalServiceEpisode, episode_id)
    attribution = db.session.get(EvidenceExtraction, attribution_extraction_id)
    if episode is None or episode.status != "active":
        raise HistoricalReconciliationError("Historical service episode was not found.")
    _authority(actor_user_id, episode.car_id)

    if (
        attribution is None
        or attribution.evidence is None
        or attribution.evidence.car_id != episode.car_id
        or attribution.extraction_type != "historical_case_attribution"
        or attribution.status != "completed"
        or int((attribution.provenance or {}).get("episode_id") or 0) != episode.id
    ):
        raise HistoricalReconciliationError(
            "A completed case attribution for this episode is required first."
        )

    case_payload = attribution_payload(attribution)
    if not case_payload:
        raise HistoricalReconciliationError(
            "The episode attribution result is unavailable."
        )
    if not reconciliation_signal(case_payload):
        raise HistoricalReconciliationError(
            "Rina did not find matched work claims that require reconciliation."
        )

    existing = latest_reconciliation(
        episode_id=episode.id,
        attribution_extraction_id=attribution.id,
    )
    if existing is not None and existing.status in {"processing", "completed"}:
        return HistoricalReconciliationStart(
            episode_id=episode.id,
            attribution_extraction_id=attribution.id,
            extraction_id=existing.id,
            status=existing.status,
            phase=str(
                (existing.provenance or {}).get("background_stage")
                or ("completed" if existing.status == "completed" else "reconciling")
            ),
            reused_existing=True,
        )

    analyzer = analyzer or WhatsAppBundleAdvisorAnalyzer()
    anchor = episode_anchor_context(episode)
    durable = durable_work_for_episode(episode)

    analysis = EvidenceExtraction(
        evidence_id=attribution.evidence_id,
        extraction_type="historical_reconciliation",
        provider=analyzer.provider_name,
        provider_model=getattr(analyzer, "model", None),
        status="processing",
        review_status="unreviewed",
        provenance={
            "analysis_pipeline": PIPELINE,
            "background_stage": "starting",
            "started_by_user_id": actor_user_id,
            "episode_id": episode.id,
            "anchor_evidence_id": episode.anchor_evidence_id,
            "anchor_extraction_id": episode.anchor_extraction_id,
            "attribution_extraction_id": attribution.id,
            "semantic_authority": "candidate_only",
            "schema_version": 1,
            "attribution_sha256": hashlib.sha256(
                repr(case_payload).encode("utf-8")
            ).hexdigest(),
        },
    )
    db.session.add(analysis)
    db.session.commit()

    try:
        response = analyzer.start_episode_reconciliation_background(
            episode_anchor=anchor,
            attribution_payload=case_payload,
            durable_work=durable,
            trusted_vehicle_context=_trusted_vehicle_context(attribution.evidence.car),
        )
    except RinaProviderError as exc:
        analysis.status = "failed"
        analysis.completed_at = _utcnow_naive()
        analysis.provenance = {
            **(analysis.provenance or {}),
            "background_stage": "failed",
            "failure_class": type(exc).__name__,
            "failure_detail": str(exc).replace("\n", " ")[:700],
        }
        db.session.commit()
        raise HistoricalReconciliationError(
            "Rina could not start historical work reconciliation."
        ) from exc

    analysis.provider_model = response.model
    analysis.provider_request_id = response.response_id
    analysis.provenance = {
        **(analysis.provenance or {}),
        "background_stage": "reconciling",
        "background_response_id": response.response_id,
    }
    db.session.commit()
    return HistoricalReconciliationStart(
        episode_id=episode.id,
        attribution_extraction_id=attribution.id,
        extraction_id=analysis.id,
        status="processing",
        phase="reconciling",
    )


def _status(
    analysis: EvidenceExtraction,
    *,
    message: str,
    review_ready: bool = False,
) -> HistoricalReconciliationStatus:
    provenance = analysis.provenance or {}
    return HistoricalReconciliationStatus(
        episode_id=int(provenance.get("episode_id") or 0),
        extraction_id=analysis.id,
        status=analysis.status,
        phase=str(provenance.get("background_stage") or analysis.status),
        message=message,
        review_ready=review_ready,
    )


def advance_episode_reconciliation(
    *,
    extraction_id: int,
    actor_user_id: int,
    analyzer: WhatsAppBundleAdvisorAnalyzer | None = None,
) -> HistoricalReconciliationStatus:
    analysis = db.session.get(EvidenceExtraction, extraction_id)
    if analysis is None or analysis.evidence is None:
        raise HistoricalReconciliationError("Historical reconciliation was not found.")

    provenance = dict(analysis.provenance or {})
    if (
        analysis.extraction_type != "historical_reconciliation"
        or provenance.get("analysis_pipeline") != PIPELINE
    ):
        raise HistoricalReconciliationError(
            "This analysis is not a historical reconciliation run."
        )

    episode = db.session.get(
        HistoricalServiceEpisode,
        int(provenance.get("episode_id") or 0),
    )
    attribution = db.session.get(
        EvidenceExtraction,
        int(provenance.get("attribution_extraction_id") or 0),
    )
    if (
        episode is None
        or attribution is None
        or episode.car_id != analysis.evidence.car_id
        or attribution.evidence_id != analysis.evidence_id
    ):
        raise HistoricalReconciliationError(
            "Historical reconciliation provenance is incomplete."
        )
    _authority(actor_user_id, episode.car_id)

    if analysis.status == "completed":
        return _status(
            analysis,
            message="Historical reconciliation is ready for advisor decisions.",
            review_ready=True,
        )
    if analysis.status == "failed":
        return _status(
            analysis,
            message="Historical reconciliation failed safely. No vehicle history changed.",
        )

    analyzer = analyzer or WhatsAppBundleAdvisorAnalyzer()
    response_id = str(
        provenance.get("background_response_id")
        or analysis.provider_request_id
        or ""
    )
    if not response_id:
        analysis.status = "failed"
        analysis.completed_at = _utcnow_naive()
        analysis.provenance = {
            **provenance,
            "background_stage": "failed",
            "failure_detail": "Background response id is missing.",
        }
        db.session.commit()
        return _status(
            analysis,
            message="Historical reconciliation failed safely. No vehicle history changed.",
        )

    try:
        response = analyzer.retrieve_background(response_id)
    except RinaProviderTransientError:
        return _status(
            analysis,
            message="Rina is preparing the advisor reconciliation checklist.",
        )
    except RinaProviderError as exc:
        analysis.status = "failed"
        analysis.completed_at = _utcnow_naive()
        analysis.provenance = {
            **provenance,
            "background_stage": "failed",
            "failure_class": type(exc).__name__,
            "failure_detail": str(exc).replace("\n", " ")[:700],
        }
        db.session.commit()
        return _status(
            analysis,
            message="Historical reconciliation failed safely. No vehicle history changed.",
        )

    if response.status in {"queued", "in_progress"}:
        return _status(
            analysis,
            message="Rina is preparing the advisor reconciliation checklist.",
        )
    if response.status != "completed" or not isinstance(response.payload, dict):
        analysis.status = "failed"
        analysis.completed_at = _utcnow_naive()
        analysis.provenance = {
            **provenance,
            "background_stage": "failed",
            "failure_detail": (
                f"Background response ended with status {response.status}."
            ),
        }
        db.session.commit()
        return _status(
            analysis,
            message="Historical reconciliation failed safely. No vehicle history changed.",
        )

    normalized = _normalise_reconciliation_payload(
        response.payload,
        attribution=attribution_payload(attribution),
    )
    cipher, version, digest = _payload_cipher(normalized)
    analysis.result_ciphertext = cipher
    analysis.result_key_version = version
    analysis.result_sha256 = digest
    analysis.provider_model = response.model
    analysis.provider_request_id = response.response_id
    analysis.status = "completed"
    analysis.completed_at = _utcnow_naive()
    analysis.provenance = {
        **provenance,
        "background_stage": "completed",
        "background_response_id": response.response_id,
        "provider_output_normalized": True,
        "reasoning_stage": "historical_work_reconciliation",
    }
    db.session.commit()

    current_app.logger.info(
        "historical_reconciliation_completed episode_id=%s extraction_id=%s candidates=%s",
        episode.id,
        analysis.id,
        len(normalized.get("candidates") or []),
    )
    return _status(
        analysis,
        message="Historical reconciliation is ready for advisor decisions.",
        review_ready=True,
    )


def reconciliation_payload(
    extraction: EvidenceExtraction,
    *,
    reviewed: bool = False,
) -> dict[str, Any]:
    if (
        extraction.extraction_type != "historical_reconciliation"
        or extraction.status != "completed"
    ):
        return {}
    return decrypt_extraction_payload(extraction, reviewed=reviewed)


def save_reconciliation_review(
    *,
    extraction_id: int,
    actor_user_id: int,
    reviewed_payload: dict[str, Any],
) -> None:
    extraction = db.session.get(EvidenceExtraction, extraction_id)
    if extraction is None or extraction.evidence is None:
        raise HistoricalReconciliationError("Historical reconciliation was not found.")
    provenance = extraction.provenance or {}
    episode = db.session.get(
        HistoricalServiceEpisode,
        int(provenance.get("episode_id") or 0),
    )
    if (
        episode is None
        or episode.car_id != extraction.evidence.car_id
        or extraction.extraction_type != "historical_reconciliation"
        or extraction.status != "completed"
    ):
        raise HistoricalReconciliationError(
            "Historical reconciliation provenance is incomplete."
        )
    _authority(actor_user_id, episode.car_id)

    candidates = reviewed_payload.get("candidates")
    if not isinstance(candidates, list):
        raise HistoricalReconciliationError("Reconciliation candidates are required.")

    for row in candidates:
        if not isinstance(row, dict):
            raise HistoricalReconciliationError("Invalid reconciliation candidate.")
        decision = _clip(row.get("advisor_decision"), limit=24).lower()
        if decision not in _ALLOWED_DECISIONS:
            raise HistoricalReconciliationError(
                "Every reconciliation candidate needs a valid advisor decision."
            )
        kind = _clip(row.get("kind"), limit=40).lower()
        if kind not in _ALLOWED_KINDS:
            raise HistoricalReconciliationError("Invalid reconciliation action kind.")
        condition = _clip(row.get("component_condition"), limit=40).lower()
        if condition not in _ALLOWED_CONDITIONS:
            raise HistoricalReconciliationError("Invalid component condition.")
        if decision == "confirmed":
            if _parse_when(row.get("occurred_at")) is None:
                raise HistoricalReconciliationError(
                    "A historical date is required for every confirmed item."
                )
            if kind == "component_replacement" and not _clip(
                row.get("component_name"),
                limit=255,
            ):
                raise HistoricalReconciliationError(
                    "A component name is required for confirmed replacements."
                )

    cipher, version, digest = _payload_cipher(reviewed_payload)
    extraction.reviewed_result_ciphertext = cipher
    extraction.reviewed_result_key_version = version
    extraction.reviewed_result_sha256 = digest
    extraction.review_status = "corrected"
    extraction.reviewed_by_user_id = actor_user_id
    extraction.reviewed_at = _utcnow_naive()
    extraction.review_reason_code = "historical_episode_reconciliation"
    db.session.flush()


def applied_reconciliation_plan(
    extraction_id: int,
) -> TreatmentPlan | None:
    return TreatmentPlan.query.filter_by(
        source_extraction_id=extraction_id,
        record_origin="historical_reconciliation",
    ).first()


def apply_reconciliation(
    *,
    extraction_id: int,
    actor_user_id: int,
) -> TreatmentPlan | None:
    extraction = db.session.get(EvidenceExtraction, extraction_id)
    if extraction is None or extraction.evidence is None:
        raise HistoricalReconciliationError("Historical reconciliation was not found.")

    provenance = extraction.provenance or {}
    episode = db.session.get(
        HistoricalServiceEpisode,
        int(provenance.get("episode_id") or 0),
    )
    if episode is None or episode.car_id != extraction.evidence.car_id:
        raise HistoricalReconciliationError(
            "Historical reconciliation provenance is incomplete."
        )
    _authority(actor_user_id, episode.car_id)

    if extraction.review_status not in {"accepted", "corrected"}:
        raise HistoricalReconciliationError(
            "Save the advisor reconciliation decisions before applying them."
        )

    existing = applied_reconciliation_plan(extraction.id)
    if existing is not None:
        return existing

    reviewed = reconciliation_payload(extraction, reviewed=True)
    rows = reviewed.get("candidates") if isinstance(reviewed, dict) else []
    confirmed = [
        row
        for row in rows
        if isinstance(row, dict) and row.get("advisor_decision") == "confirmed"
    ]
    if not confirmed:
        return None

    dated: list[tuple[dict[str, Any], datetime]] = []
    for row in confirmed:
        when = _parse_when(row.get("occurred_at"))
        if when is None:
            raise HistoricalReconciliationError(
                "A historical date is required before confirmed reconciliation can be applied."
            )
        dated.append((row, when))

    start_at = min(when for _, when in dated)
    finish_at = max(when for _, when in dated)
    reference = episode.job_reference or f"Episode {episode.id}"

    plan = TreatmentPlan(
        car_id=episode.car_id,
        advisor_id=actor_user_id,
        title=f"Historical reconciliation — {reference}"[:255],
        internal_instructions=(
            "Advisor-confirmed historical reconciliation addendum. Rina proposed "
            "candidate work from episode-specific WhatsApp attribution; the advisor "
            "personally confirmed the interventions recorded in this plan."
        ),
        client_summary=(
            f"Historical completed work reconciled by an Ajebo Fix advisor for {reference}."
        ),
        status="approved",
        record_origin="historical_reconciliation",
        source_evidence_id=extraction.evidence_id,
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
        source="historical_reconciliation",
        operation_key=f"reconciliation-{extraction.id}-start",
    )

    for row, when in dated:
        candidate_id = _clip(row.get("candidate_id"), limit=64) or "candidate"
        title = _clip(row.get("title"), limit=255) or "Historical intervention"
        source_refs = [
            _clip(ref, limit=120)
            for ref in (row.get("source_refs") or [])[:30]
            if str(ref or "").strip()
        ]
        action = TreatmentActionLifecycleService.create(
            plan_id=plan.id,
            actor_user_id=actor_user_id,
            creation_key=f"historical-reconciliation:{extraction.id}:{candidate_id}",
            title=title,
            client_summary=_clip(row.get("evidence_basis"), limit=3000) or None,
            internal_instructions=(
                "Advisor-reconciled historical completed work. Attribution source refs: "
                + (", ".join(source_refs) if source_refs else "not separately available")
                + "."
            ),
            visibility="advisor",
            occurred_at=when,
            source="historical_reconciliation",
        )
        TreatmentActionLifecycleService.schedule(
            action_id=action.id,
            actor_user_id=actor_user_id,
            scheduled_for=when,
            occurred_at=when,
            source="historical_reconciliation",
            operation_key=f"{candidate_id}-schedule",
        )
        TreatmentActionLifecycleService.start(
            action_id=action.id,
            actor_user_id=actor_user_id,
            occurred_at=when,
            source="historical_reconciliation",
            operation_key=f"{candidate_id}-start",
        )
        TreatmentActionLifecycleService.complete(
            action_id=action.id,
            actor_user_id=actor_user_id,
            occurred_at=when,
            source="historical_reconciliation",
            operation_key=f"{candidate_id}-complete",
        )

        kind = _clip(row.get("kind"), limit=40).lower()
        condition = _clip(row.get("component_condition"), limit=40).lower()
        detail = TreatmentActionCompletionDetail(
            treatment_action_id=action.id,
            action_kind=kind,
            component_name=(
                _clip(row.get("component_name"), limit=255) or None
            ),
            component_location=(
                _clip(row.get("component_location"), limit=120) or None
            ),
            component_condition=(
                condition
                if kind == "component_replacement"
                else "not_applicable"
            ),
            quantity=1 if kind == "component_replacement" else None,
            source_evidence_id=extraction.evidence_id,
            verification_status="advisor_reconciled",
            verified_by_user_id=actor_user_id,
            verified_at=datetime.utcnow(),
        )
        db.session.add(detail)
        db.session.flush()

    TreatmentPlanLifecycleService.complete(
        plan_id=plan.id,
        actor_user_id=actor_user_id,
        occurred_at=finish_at,
        source="historical_reconciliation",
        operation_key=f"reconciliation-{extraction.id}-complete",
    )
    db.session.flush()
    return plan
