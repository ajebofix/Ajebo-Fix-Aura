"""Bridge Historical Intelligence episode candidates into supervised Ask Rina review.

Historical Intelligence v2 can reconstruct source-supported service episodes from a
WhatsApp corpus before a formal HistoricalServiceEpisode exists.  This bridge stages
one of those candidates as an encrypted, candidate-only historical reconciliation
draft so an advisor can review it conversationally.  No durable vehicle history is
written here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from evidence.models import EvidenceExtraction, VehicleEvidence
from extensions import db
from historical_ingestion.reconciliation import applied_reconciliation_plan
from historical_ingestion.service import _payload_cipher
from services.rina_advisor_360 import build_rina_historical_copilot_context
from services.rina_context_resolver import RinaResolvedContext


DIRECT_HISTORICAL_INTELLIGENCE_PIPELINE = (
    "historical_intelligence_candidate_reconciliation_v1"
)

_ALLOWED_COMPARISONS = {
    "missing_from_durable_history",
    "partially_represented",
    "conflicting",
    "uncertain",
}


@dataclass(frozen=True)
class HistoricalIntelligenceEpisodeChoice:
    choice_key: str
    evidence_id: int
    structured_extraction_id: int
    episode_candidate_id: str
    title: str
    comparison: str
    summary: str
    date_start: str | None
    date_end: str | None
    source_refs: tuple[str, ...]
    source_excerpt: str
    completed_interventions: tuple[str, ...]
    intake_required: bool
    confidence: float


def _clip(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _norm(value: object) -> str:
    return " ".join(str(value or "").lower().split())


def _as_int(value: object) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _choice_key(
    *,
    evidence_id: int,
    structured_extraction_id: int,
    episode_candidate_id: str,
) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_.:-]", "", episode_candidate_id)[:80]
    return f"{evidence_id}:{structured_extraction_id}:{safe_id}"


def _selected_vehicle_match(
    *,
    context: RinaResolvedContext,
    episode: dict[str, Any],
    comparison: dict[str, Any],
    vehicle_candidates: dict[str, dict[str, Any]],
) -> bool:
    matched_car_id = _as_int(comparison.get("matched_car_id"))
    if matched_car_id is not None:
        return matched_car_id == int(context.car_id)

    vehicle_candidate_id = str(episode.get("vehicle_candidate_id") or "").strip()
    if not vehicle_candidate_id:
        return False
    vehicle = vehicle_candidates.get(vehicle_candidate_id)
    if not isinstance(vehicle, dict):
        return False
    return str(vehicle.get("identity_state") or "") == "selected_vehicle_match"


def _unrepresented_completed_interventions(
    *,
    episode: dict[str, Any],
    comparison: dict[str, Any],
) -> list[str]:
    completed = [
        _clip(value, 255)
        for value in (episode.get("completed_interventions") or [])
        if _clip(value, 255)
    ]
    if not completed:
        return []

    comparison_state = str(comparison.get("comparison") or "")
    if comparison_state == "missing_from_durable_history":
        return completed

    represented = {
        _norm(value)
        for value in (comparison.get("already_represented_facts") or [])
        if _norm(value)
    }
    missing = {
        _norm(value)
        for value in (comparison.get("missing_facts") or [])
        if _norm(value)
    }

    result: list[str] = []
    for intervention in completed:
        normalized = _norm(intervention)
        if normalized in represented:
            continue
        if comparison_state == "partially_represented" and missing:
            # The canonical comparison is the stronger filter when it explicitly
            # identified what remains missing.
            if normalized not in missing:
                continue
        result.append(intervention)
    return result


def discover_intelligence_episode_choices(
    context: RinaResolvedContext,
) -> list[HistoricalIntelligenceEpisodeChoice]:
    """Return source-supported episode candidates eligible for advisor review."""

    copilot = build_rina_historical_copilot_context(context) or {}
    choices: list[HistoricalIntelligenceEpisodeChoice] = []

    for source in copilot.get("source_candidate_backlog") or []:
        if not isinstance(source, dict):
            continue
        evidence_id = _as_int(source.get("evidence_id"))
        structured_extraction_id = _as_int(source.get("structured_extraction_id"))
        if evidence_id is None or structured_extraction_id is None:
            continue

        vehicle_candidates = {
            str(row.get("candidate_id") or ""): row
            for row in (source.get("vehicle_candidates") or [])
            if isinstance(row, dict) and row.get("candidate_id")
        }
        comparisons = {
            str(row.get("episode_candidate_id") or ""): row
            for row in (source.get("canonical_comparisons") or [])
            if isinstance(row, dict) and row.get("episode_candidate_id")
        }

        for episode in source.get("service_episode_candidates") or []:
            if not isinstance(episode, dict):
                continue
            episode_candidate_id = str(
                episode.get("episode_candidate_id") or ""
            ).strip()
            if not episode_candidate_id:
                continue
            comparison = comparisons.get(episode_candidate_id)
            if not isinstance(comparison, dict):
                continue
            comparison_state = str(comparison.get("comparison") or "")
            if comparison_state not in _ALLOWED_COMPARISONS:
                continue
            if not _selected_vehicle_match(
                context=context,
                episode=episode,
                comparison=comparison,
                vehicle_candidates=vehicle_candidates,
            ):
                continue

            completed = _unrepresented_completed_interventions(
                episode=episode,
                comparison=comparison,
            )
            intake_required = not completed and comparison_state in {
                "uncertain",
                "conflicting",
            }
            if not completed and not intake_required:
                continue

            try:
                confidence = float(episode.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = min(max(confidence, 0.0), 1.0)

            title = (
                _clip(episode.get("title"), 255)
                or f"Historical service candidate {episode_candidate_id}"
            )
            existing = _existing_direct_draft(
                evidence_id=evidence_id,
                structured_extraction_id=structured_extraction_id,
                episode_candidate_id=episode_candidate_id,
            )
            if (
                existing is not None
                and applied_reconciliation_plan(existing.id) is not None
            ):
                continue

            choices.append(
                HistoricalIntelligenceEpisodeChoice(
                    choice_key=_choice_key(
                        evidence_id=evidence_id,
                        structured_extraction_id=structured_extraction_id,
                        episode_candidate_id=episode_candidate_id,
                    ),
                    evidence_id=evidence_id,
                    structured_extraction_id=structured_extraction_id,
                    episode_candidate_id=episode_candidate_id,
                    title=title,
                    comparison=comparison_state,
                    summary=_clip(episode.get("summary"), 1800),
                    date_start=_clip(episode.get("date_start"), 64) or None,
                    date_end=_clip(episode.get("date_end"), 64) or None,
                    source_refs=tuple(
                        _clip(ref, 120)
                        for ref in (episode.get("source_refs") or [])[:30]
                        if _clip(ref, 120)
                    ),
                    source_excerpt=_clip(episode.get("source_excerpt"), 1800),
                    completed_interventions=tuple(completed),
                    intake_required=intake_required,
                    confidence=confidence,
                )
            )

    return choices[:40]


def _existing_direct_draft(
    *,
    evidence_id: int,
    structured_extraction_id: int,
    episode_candidate_id: str,
) -> EvidenceExtraction | None:
    rows = (
        EvidenceExtraction.query.filter_by(
            evidence_id=evidence_id,
            extraction_type="historical_reconciliation",
        )
        .order_by(EvidenceExtraction.id.desc())
        .limit(120)
        .all()
    )
    for row in rows:
        provenance = row.provenance or {}
        if (
            provenance.get("analysis_pipeline")
            == DIRECT_HISTORICAL_INTELLIGENCE_PIPELINE
            and int(provenance.get("source_structured_extraction_id") or 0)
            == int(structured_extraction_id)
            and str(provenance.get("episode_candidate_id") or "")
            == str(episode_candidate_id)
            and row.status == "completed"
        ):
            return row
    return None


def discover_intelligence_episode_choices_for_source(
    context: RinaResolvedContext,
    *,
    evidence_id: int,
) -> list[HistoricalIntelligenceEpisodeChoice]:
    """Return eligible episode choices from one exact imported source only."""

    return [
        item
        for item in discover_intelligence_episode_choices(context)
        if item.evidence_id == int(evidence_id)
    ]


def stage_intelligence_episode_candidate(
    *,
    context: RinaResolvedContext,
    choice_key: str,
    actor_user_id: int,
) -> EvidenceExtraction:
    """Create or reuse an encrypted candidate-only reconciliation draft."""

    choice = next(
        (
            item
            for item in discover_intelligence_episode_choices(context)
            if item.choice_key == choice_key
        ),
        None,
    )
    if choice is None:
        raise ValueError(
            "That Historical Intelligence episode candidate is no longer eligible "
            "for this selected vehicle."
        )

    evidence = db.session.get(VehicleEvidence, choice.evidence_id)
    extraction = db.session.get(
        EvidenceExtraction,
        choice.structured_extraction_id,
    )
    if (
        evidence is None
        or evidence.car_id != context.car_id
        or extraction is None
        or extraction.evidence_id != evidence.id
        or extraction.extraction_type != "structured_fields"
        or extraction.status != "completed"
    ):
        raise ValueError("Historical Intelligence source provenance is incomplete.")

    existing = _existing_direct_draft(
        evidence_id=evidence.id,
        structured_extraction_id=extraction.id,
        episode_candidate_id=choice.episode_candidate_id,
    )
    if existing is not None:
        return existing

    suggested_date = (
        choice.date_start
        if choice.date_start
        and choice.date_end
        and choice.date_start == choice.date_end
        else None
    )
    rows: list[dict[str, Any]] = []
    for index, intervention in enumerate(choice.completed_interventions, start=1):
        rows.append(
            {
                "candidate_id": f"I{index:03d}",
                "title": intervention,
                "kind": "other_intervention",
                "component_name": None,
                "component_location": None,
                "suggested_occurred_at": suggested_date,
                "evidence_state": "completion_claim",
                "source_refs": list(choice.source_refs),
                "evidence_basis": (
                    choice.source_excerpt
                    or choice.summary
                    or "Historical Intelligence source-supported completion candidate."
                ),
                "confidence": choice.confidence,
                "reconciliation_reason": (
                    "Historical Intelligence classified this source-supported episode as "
                    f"{choice.comparison}. Advisor confirmation is required before any "
                    "durable history write."
                ),
                "advisor_decision": "unsure",
                "component_condition": "not_applicable",
                "advisor_note": "",
            }
        )

    payload = {
        "schema_version": 1,
        "summary": choice.summary,
        "advisor_notice": (
            "Historical Intelligence found this episode but did not prove completed "
            "work. The advisor must clarify what actually happened before Aura can "
            "prepare any durable history."
            if choice.intake_required
            else (
                "This draft was staged deterministically from Historical Intelligence. "
                "Every completed intervention still requires advisor review and a dated "
                "confirmation before it can become durable vehicle history."
            )
        ),
        "episode_title": choice.title,
        "episode_candidate_id": choice.episode_candidate_id,
        "episode_date_start": choice.date_start,
        "episode_date_end": choice.date_end,
        "source_excerpt": choice.source_excerpt,
        "canonical_comparison": choice.comparison,
        "advisor_intake_required": choice.intake_required,
        "advisor_intake_resolved": not choice.intake_required,
        "advisor_intake_outcome": None,
        "candidates": rows,
    }
    cipher, version, digest = _payload_cipher(payload)
    row = EvidenceExtraction(
        evidence_id=evidence.id,
        extraction_type="historical_reconciliation",
        provider="aura",
        provider_model="deterministic-historical-intelligence-bridge-v1",
        status="completed",
        review_status="unreviewed",
        result_ciphertext=cipher,
        result_key_version=version,
        result_sha256=digest,
        completed_at=datetime.utcnow(),
        provenance={
            "analysis_pipeline": DIRECT_HISTORICAL_INTELLIGENCE_PIPELINE,
            "background_stage": "completed",
            "source_structured_extraction_id": extraction.id,
            "episode_candidate_id": choice.episode_candidate_id,
            "episode_title": choice.title,
            "selected_car_id": context.car_id,
            "staged_by_user_id": actor_user_id,
            "semantic_authority": "candidate_only",
            "canonical_comparison": choice.comparison,
            "advisor_intake_required": choice.intake_required,
            "schema_version": 1,
        },
    )
    db.session.add(row)
    db.session.flush()
    return row


def direct_drafts_for_car(car_id: int) -> list[EvidenceExtraction]:
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
        == DIRECT_HISTORICAL_INTELLIGENCE_PIPELINE
    ]
