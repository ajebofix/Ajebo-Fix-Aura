"""Governed longitudinal advisor context for A.J. Rina.

This module intentionally builds a compact, authority-filtered care graph rather
than dumping Aura's database into the provider prompt. It is advisor/admin only,
vehicle-scoped, bounded, provenance-aware and read-only.
"""

from __future__ import annotations

from collections import Counter
import re
from typing import Any

from evidence.models import EvidenceBundleItem, EvidenceExtraction, VehicleEvidence
from extensions import db
from historical_ingestion.models import HistoricalServiceEpisode
from historical_ingestion.reconciliation import applied_reconciliation_plan
from historical_ingestion.service import HistoricalIngestionError, decrypt_extraction_payload
from models import Car, CarDriver, CarOwnership, TreatmentPlan, User, VehicleEvent
from profiles.models import ClientProfile, ProfileAuditEvent
from rina.audit_models import RinaAIAuditEvent
from services.rina_context_resolver import RinaResolvedContext
from treatment.models import TreatmentAction, TreatmentOutcome


_PRIVILEGED = {"advisor", "administrator"}
_MAX_EPISODES = 6
_MAX_PLANS = 6
_MAX_ACTIONS_PER_PLAN = 16
_MAX_OUTCOMES_PER_PLAN = 8
_MAX_EVIDENCE_ROWS = 12
_MAX_EVENTS = 20
_MAX_RINA_AUDIT = 10
_MAX_PROFILE_AUDIT = 8


def _clip(value: object, *, limit: int) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    if not clean:
        return None
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _iso(value: object) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else _clip(value, limit=64)


def _safe_owner_context(car: Car) -> dict[str, Any] | None:
    ownership = car.active_ownership
    if ownership is None or ownership.user is None:
        return None

    owner = ownership.user
    profile = ClientProfile.query.filter_by(user_id=owner.id).first()

    safe_profile = None
    if profile is not None:
        safe_profile = {
            "occupation": _clip(profile.occupation, limit=120),
            "organisation": _clip(profile.organisation, limit=120),
            "city": _clip(profile.city, limit=120),
            "state_region": _clip(profile.state_region, limit=120),
            "country": _clip(profile.country, limit=120),
            "preferred_communication": _clip(
                profile.preferred_communication,
                limit=30,
            ),
            "preferred_communication_time": _clip(
                profile.preferred_communication_time,
                limit=120,
            ),
            "care_preference": _clip(profile.care_preference, limit=600),
            "preferred_language": _clip(profile.preferred_language, limit=80),
            "timezone": _clip(profile.timezone, limit=80),
        }

    return {
        "owner_user_id": owner.id,
        "owner_name": _clip(owner.name, limit=120),
        "ownership_id": ownership.id,
        "plate_number": _clip(ownership.plate_number, limit=20),
        "care_plan": _clip(ownership.care_plan, limit=50),
        "ownership_started_at": _iso(ownership.start_date),
        "safe_profile": safe_profile,
    }


def _driver_context(car_id: int) -> list[dict[str, Any]]:
    rows = (
        db.session.query(CarDriver, User)
        .join(User, User.id == CarDriver.user_id)
        .filter(
            CarDriver.car_id == car_id,
            CarDriver.is_active.is_(True),
        )
        .order_by(CarDriver.assigned_at.desc(), CarDriver.id.desc())
        .limit(6)
        .all()
    )
    return [
        {
            "driver_user_id": user.id,
            "driver_name": _clip(user.name, limit=120),
            "assigned_at": _iso(assignment.assigned_at),
        }
        for assignment, user in rows
    ]


def _episode_extractions(car_id: int) -> list[EvidenceExtraction]:
    return (
        EvidenceExtraction.query.join(
            VehicleEvidence,
            VehicleEvidence.id == EvidenceExtraction.evidence_id,
        )
        .filter(
            VehicleEvidence.car_id == car_id,
            EvidenceExtraction.extraction_type.in_(
                ("historical_case_attribution", "historical_reconciliation")
            ),
            EvidenceExtraction.status == "completed",
        )
        .order_by(EvidenceExtraction.id.desc())
        .limit(120)
        .all()
    )


def _latest_episode_extraction(
    rows: list[EvidenceExtraction],
    *,
    episode_id: int,
    extraction_type: str,
) -> EvidenceExtraction | None:
    for row in rows:
        if row.extraction_type != extraction_type:
            continue
        provenance = row.provenance or {}
        try:
            row_episode_id = int(provenance.get("episode_id") or 0)
        except (TypeError, ValueError):
            continue
        if row_episode_id == int(episode_id):
            return row
    return None


def _attribution_summary(extraction: EvidenceExtraction | None) -> dict[str, Any] | None:
    if extraction is None:
        return None
    try:
        payload = decrypt_extraction_payload(extraction)
    except HistoricalIngestionError:
        return {
            "extraction_id": extraction.id,
            "status": extraction.status,
            "payload_available": False,
        }

    groups = payload.get("evidence_groups")
    compact_groups: list[dict[str, Any]] = []
    if isinstance(groups, list):
        for row in groups[:12]:
            if not isinstance(row, dict):
                continue
            compact_groups.append(
                {
                    "classification": _clip(row.get("classification"), limit=32),
                    "title": _clip(row.get("title"), limit=220),
                    "summary": _clip(row.get("summary"), limit=700),
                    "match_reason": _clip(row.get("match_reason"), limit=500),
                    "evidence_role": _clip(row.get("evidence_role"), limit=40),
                    "occurred_at": _clip(row.get("occurred_at"), limit=64),
                    "confidence": row.get("confidence"),
                    "source_refs": [
                        _clip(ref, limit=120)
                        for ref in (row.get("source_refs") or [])[:8]
                        if _clip(ref, limit=120)
                    ],
                }
            )

    return {
        "extraction_id": extraction.id,
        "status": extraction.status,
        "episode_summary": _clip(payload.get("episode_summary"), limit=1200),
        "match_overview": _clip(payload.get("match_overview"), limit=900),
        "source_ref_counts": payload.get("source_ref_counts") or {},
        "evidence_groups": compact_groups,
        "advisor_attention": [
            _clip(item, limit=500)
            for item in (payload.get("advisor_attention") or [])[:8]
            if _clip(item, limit=500)
        ],
    }


def _reconciliation_summary(
    extraction: EvidenceExtraction | None,
) -> dict[str, Any] | None:
    if extraction is None:
        return None

    reviewed = extraction.review_status in {"accepted", "corrected"}
    try:
        payload = decrypt_extraction_payload(extraction, reviewed=reviewed)
    except HistoricalIngestionError:
        return {
            "extraction_id": extraction.id,
            "status": extraction.status,
            "review_status": extraction.review_status,
            "payload_available": False,
        }

    candidates: list[dict[str, Any]] = []
    for row in (payload.get("candidates") or [])[:24]:
        if not isinstance(row, dict):
            continue
        candidates.append(
            {
                "candidate_id": _clip(row.get("candidate_id"), limit=32),
                "title": _clip(row.get("title"), limit=220),
                "kind": _clip(row.get("kind"), limit=40),
                "evidence_state": _clip(row.get("evidence_state"), limit=40),
                "advisor_decision": _clip(
                    row.get("advisor_decision"),
                    limit=32,
                ),
                "component_name": _clip(row.get("component_name"), limit=220),
                "component_location": _clip(
                    row.get("component_location"),
                    limit=120,
                ),
                "component_condition": _clip(
                    row.get("component_condition"),
                    limit=40,
                ),
                "occurred_at": _clip(
                    row.get("occurred_at") or row.get("suggested_occurred_at"),
                    limit=64,
                ),
                "source_refs": [
                    _clip(ref, limit=120)
                    for ref in (row.get("source_refs") or [])[:8]
                    if _clip(ref, limit=120)
                ],
            }
        )

    counts = Counter(
        str(item.get("advisor_decision") or "unreviewed") for item in candidates
    )
    return {
        "extraction_id": extraction.id,
        "status": extraction.status,
        "review_status": extraction.review_status,
        "reviewed_by_user_id": extraction.reviewed_by_user_id,
        "reviewed_at": _iso(extraction.reviewed_at),
        "summary": _clip(payload.get("summary"), limit=900),
        "advisor_review_note": _clip(payload.get("advisor_review_note"), limit=600),
        "decision_counts": dict(counts),
        "candidates": candidates,
    }


def _historical_episodes(car_id: int) -> list[dict[str, Any]]:
    episodes = (
        HistoricalServiceEpisode.query.filter_by(car_id=car_id)
        .order_by(
            HistoricalServiceEpisode.episode_date.desc(),
            HistoricalServiceEpisode.id.desc(),
        )
        .limit(_MAX_EPISODES)
        .all()
    )
    extraction_rows = _episode_extractions(car_id)

    result: list[dict[str, Any]] = []
    for episode in episodes:
        attribution = _latest_episode_extraction(
            extraction_rows,
            episode_id=episode.id,
            extraction_type="historical_case_attribution",
        )
        reconciliation = _latest_episode_extraction(
            extraction_rows,
            episode_id=episode.id,
            extraction_type="historical_reconciliation",
        )
        result.append(
            {
                "episode_id": episode.id,
                "title": _clip(episode.title, limit=255),
                "job_reference": _clip(episode.job_reference, limit=120),
                "episode_date": _iso(episode.episode_date),
                "status": episode.status,
                "anchor_evidence_id": episode.anchor_evidence_id,
                "anchor_extraction_id": episode.anchor_extraction_id,
                "created_by_user_id": episode.created_by_user_id,
                "case_attribution": _attribution_summary(attribution),
                "reconciliation": _reconciliation_summary(reconciliation),
            }
        )
    return result


def _compact_understanding_payload(payload: dict[str, Any]) -> dict[str, Any]:
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}

    chronology: list[dict[str, Any]] = []
    for item in (payload.get("chronology") or [])[:24]:
        if not isinstance(item, dict):
            continue
        chronology.append(
            {
                "date": _clip(item.get("date"), limit=40),
                "event": _clip(item.get("event"), limit=420),
                "status": _clip(item.get("status"), limit=40),
                "source_pages": list(item.get("source_pages") or [])[:8],
            }
        )

    facts: list[dict[str, Any]] = []
    for item in (payload.get("facts") or [])[:40]:
        if not isinstance(item, dict):
            continue
        facts.append(
            {
                "kind": _clip(item.get("kind"), limit=40),
                "state": _clip(item.get("state"), limit=40),
                "title": _clip(item.get("title"), limit=220),
                "detail": _clip(item.get("detail"), limit=500),
                "date": _clip(item.get("date"), limit=40),
                "why_it_matters": _clip(item.get("why_it_matters"), limit=420),
                "source_pages": list(item.get("source_pages") or [])[:8],
            }
        )

    return {
        "document": {
            "document_type": _clip(document.get("document_type"), limit=80),
            "title": _clip(document.get("title"), limit=255),
            "reference": _clip(document.get("reference"), limit=120),
            "job_reference": _clip(document.get("job_reference"), limit=120),
            "sow_reference": _clip(document.get("sow_reference"), limit=120),
            "document_date": _clip(document.get("document_date"), limit=40),
            "client_name": _clip(document.get("client_name"), limit=180),
            "vehicle_description": _clip(
                document.get("vehicle_description"),
                limit=220,
            ),
            "vin": _clip(document.get("vin"), limit=80),
            "plate_number": _clip(document.get("plate_number"), limit=40),
        },
        "advisor_narrative": _clip(payload.get("advisor_narrative"), limit=1800),
        "chronology": chronology,
        "facts": facts,
        "ambiguities": [
            _clip(item, limit=500)
            for item in (payload.get("ambiguities") or [])[:12]
            if _clip(item, limit=500)
        ],
        "advisor_suggestions": [
            _clip(item, limit=500)
            for item in (payload.get("advisor_suggestions") or [])[:12]
            if _clip(item, limit=500)
        ],
    }


def _bundle_child_content_summary(source_id: int) -> dict[str, Any]:
    rows = (
        EvidenceBundleItem.query.filter_by(bundle_evidence_id=source_id)
        .order_by(EvidenceBundleItem.member_index.asc())
        .all()
    )
    counts = Counter(str(row.member_kind or "unknown") for row in rows)
    analysed_counts: Counter[str] = Counter()
    for row in rows:
        child = row.child
        if child is None:
            continue
        completed_types = {
            extraction.extraction_type
            for extraction in child.extractions
            if extraction.status == "completed"
        }
        for extraction_type in completed_types:
            analysed_counts[extraction_type] += 1

    return {
        "total_children": len(rows),
        "child_kind_counts": dict(counts),
        "completed_extraction_counts": dict(analysed_counts),
        "note": (
            "These are child items inside the parent source. Their extracted content "
            "was used by the whole-source analysis and must not be counted as separate "
            "historical sources."
        ),
    }


def _historical_source_candidate_backlog(car_id: int) -> list[dict[str, Any]]:
    """Expose bounded candidate-only source analysis before advisor publication."""

    sources = (
        VehicleEvidence.query.filter(
            VehicleEvidence.car_id == car_id,
            VehicleEvidence.historical_source_type.isnot(None),
            VehicleEvidence.storage_state == "available",
            VehicleEvidence.deleted_at.is_(None),
            ~VehicleEvidence.bundle_parent_items.any(),
        )
        .order_by(VehicleEvidence.uploaded_at.desc(), VehicleEvidence.id.desc())
        .limit(8)
        .all()
    )

    result: list[dict[str, Any]] = []
    for source in sources:
        extraction = (
            EvidenceExtraction.query.filter_by(
                evidence_id=source.id,
                extraction_type="structured_fields",
                status="completed",
            )
            .order_by(EvidenceExtraction.id.desc())
            .first()
        )
        understanding = (
            EvidenceExtraction.query.filter_by(
                evidence_id=source.id,
                extraction_type="document_understanding",
                status="completed",
            )
            .order_by(EvidenceExtraction.id.desc())
            .first()
        )
        if extraction is None and understanding is None:
            continue

        payload: dict[str, Any] = {}
        if extraction is not None:
            try:
                payload = decrypt_extraction_payload(
                    extraction,
                    reviewed=extraction.review_status in {"accepted", "corrected"},
                )
            except HistoricalIngestionError:
                payload = {}

        understanding_payload: dict[str, Any] = {}
        if understanding is not None:
            try:
                understanding_payload = decrypt_extraction_payload(understanding)
            except HistoricalIngestionError:
                understanding_payload = {}

        candidate_rows: list[dict[str, Any]] = []
        all_candidates = payload.get("candidates") or []
        for item in all_candidates[:60]:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or "").strip().lower()
            destination = str(item.get("suggested_destination") or "").strip().lower()
            if category == "financial" or destination == "financial_separate":
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            candidate_rows.append(
                {
                    "candidate_id": _clip(item.get("candidate_id"), limit=64),
                    "category": _clip(category, limit=40),
                    "state": _clip(item.get("state"), limit=40),
                    "title": _clip(item.get("title"), limit=220),
                    "detail": _clip(item.get("detail"), limit=350),
                    "occurred_at": _clip(item.get("occurred_at"), limit=64),
                    "suggested_destination": _clip(destination, limit=48),
                    "completion_confirmed": bool(item.get("completion_confirmed")),
                    "action": {
                        "kind": _clip(action.get("kind"), limit=40),
                        "component_name": _clip(action.get("component_name"), limit=220),
                        "component_location": _clip(
                            action.get("component_location"),
                            limit=120,
                        ),
                    }
                    if action
                    else None,
                    "source_fact_ids": [
                        _clip(ref, limit=120)
                        for ref in (item.get("source_fact_ids") or [])[:8]
                        if _clip(ref, limit=120)
                    ],
                    "review_decision": _clip(item.get("review_decision"), limit=24),
                    "candidate_only": item.get("review_decision") not in {
                        "accepted",
                    },
                }
            )

        threads: list[dict[str, Any]] = []
        all_threads = payload.get("priority_threads") or []
        for item in all_threads[:20]:
            if not isinstance(item, dict):
                continue
            threads.append(
                {
                    "title": _clip(item.get("title"), limit=220),
                    "priority": _clip(item.get("priority"), limit=40),
                    "reason": _clip(item.get("reason"), limit=500),
                    "status": _clip(item.get("status"), limit=40),
                    "source_refs": [
                        _clip(ref, limit=120)
                        for ref in (item.get("source_refs") or [])[:8]
                        if _clip(ref, limit=120)
                    ],
                }
            )

        vehicle_candidates = (
            payload.get("vehicle_candidates")
            if isinstance(payload.get("vehicle_candidates"), list)
            else []
        )[:24]
        service_episodes = (
            payload.get("service_episode_candidates")
            if isinstance(payload.get("service_episode_candidates"), list)
            else []
        )[:80]
        canonical_comparisons = (
            payload.get("canonical_comparisons")
            if isinstance(payload.get("canonical_comparisons"), list)
            else []
        )[:80]
        comparison_counts = Counter(
            str(item.get("comparison") or "uncertain")
            for item in canonical_comparisons
            if isinstance(item, dict)
        )
        source_coverage = (
            payload.get("source_coverage")
            if isinstance(payload.get("source_coverage"), dict)
            else (
                understanding_payload.get("source_coverage")
                if isinstance(understanding_payload.get("source_coverage"), dict)
                else {}
            )
        )

        result.append(
            {
                "evidence_id": source.id,
                "structured_extraction_id": extraction.id if extraction else None,
                "understanding_extraction_id": (
                    understanding.id if understanding else None
                ),
                "source_type": source.historical_source_type,
                "safe_display_name": _clip(source.safe_display_name, limit=160),
                "source_review_status": source.review_status,
                "extraction_review_status": (
                    extraction.review_status if extraction else None
                ),
                "document": (
                    payload.get("document")
                    if isinstance(payload.get("document"), dict)
                    else (
                        understanding_payload.get("document")
                        if isinstance(understanding_payload.get("document"), dict)
                        else {}
                    )
                ),
                "rina_summary": _clip(payload.get("rina_summary"), limit=1800),
                "case_focus": _clip(payload.get("case_focus"), limit=1800),
                "advisor_suggestions": [
                    _clip(item, limit=500)
                    for item in (payload.get("advisor_suggestions") or [])[:12]
                    if _clip(item, limit=500)
                ],
                "candidate_count": len(all_candidates),
                "priority_thread_count": len(all_threads),
                "priority_threads": threads,
                "candidates": candidate_rows,
                "historical_intelligence_version": int(
                    payload.get("historical_intelligence_version") or 0
                ),
                "source_coverage": source_coverage,
                "vehicle_candidates": vehicle_candidates,
                "service_episode_candidates": service_episodes,
                "canonical_comparisons": canonical_comparisons,
                "canonical_comparison_counts": dict(comparison_counts),
                "understanding": (
                    _compact_understanding_payload(understanding_payload)
                    if understanding_payload
                    else None
                ),
                "bundle_content": (
                    _bundle_child_content_summary(source.id)
                    if source.historical_source_type == "whatsapp_conversation"
                    else None
                ),
                "semantic_authority": (
                    "advisor_reviewed_source"
                    if (
                        source.review_status == "accepted"
                        or (
                            extraction is not None
                            and extraction.review_status in {"accepted", "corrected"}
                        )
                    )
                    else "candidate_only"
                ),
            }
        )

    return result


def build_rina_historical_copilot_context(
    context: RinaResolvedContext,
) -> dict[str, Any] | None:
    """Return advisor-facing historical backlog without promoting candidates to truth."""

    if context.authority not in _PRIVILEGED:
        return None

    car = db.session.get(Car, context.car_id)
    if car is None:
        return None

    episodes = _historical_episodes(context.car_id)
    source_candidates = _historical_source_candidate_backlog(context.car_id)

    open_groups: list[dict[str, Any]] = []
    seen: set[tuple[object, ...]] = set()
    for episode in episodes:
        attribution = episode.get("case_attribution") or {}
        for group in attribution.get("evidence_groups") or []:
            classification = str(group.get("classification") or "").strip().lower()
            if classification not in {"uncertain", "other_episode", "unassigned"}:
                continue
            key = (
                classification,
                group.get("title"),
                tuple(group.get("source_refs") or []),
            )
            if key in seen:
                continue
            seen.add(key)
            open_groups.append(
                {
                    "classification": classification,
                    "title": group.get("title"),
                    "summary": group.get("summary"),
                    "match_reason": group.get("match_reason"),
                    "evidence_role": group.get("evidence_role"),
                    "occurred_at": group.get("occurred_at"),
                    "confidence": group.get("confidence"),
                    "source_refs": list(group.get("source_refs") or [])[:8],
                    "source_episode_id": episode.get("episode_id"),
                    "candidate_only": True,
                }
            )

    source_rows = (
        VehicleEvidence.query.filter(
            VehicleEvidence.car_id == context.car_id,
            VehicleEvidence.historical_source_type.isnot(None),
            VehicleEvidence.deleted_at.is_(None),
            ~VehicleEvidence.bundle_parent_items.any(),
        )
        .order_by(VehicleEvidence.uploaded_at.desc(), VehicleEvidence.id.desc())
        .limit(40)
        .all()
    )
    source_review_counts = Counter(
        str(row.review_status or "unknown") for row in source_rows
    )
    pending_source_ids = [
        row.id
        for row in source_rows
        if row.review_status not in {"accepted", "superseded", "deleted"}
    ][:12]

    owner = car.active_ownership.user if car.active_ownership else None
    known_other_vehicles: list[dict[str, Any]] = []
    if owner is not None:
        ownerships = (
            CarOwnership.query.join(Car, Car.id == CarOwnership.car_id)
            .filter(
                CarOwnership.user_id == owner.id,
                CarOwnership.is_active.is_(True),
                CarOwnership.car_id != context.car_id,
            )
            .order_by(CarOwnership.start_date.desc(), CarOwnership.id.desc())
            .limit(12)
            .all()
        )
        for ownership in ownerships:
            other = ownership.car
            if other is None:
                continue
            known_other_vehicles.append(
                {
                    "car_id": other.id,
                    "display_name": _clip(other.rina_display_name, limit=220),
                    "plate_number": _clip(ownership.plate_number, limit=20),
                    "vin_tail": (
                        str(other.vin or "").strip().upper()[-6:]
                        if other.vin
                        else None
                    ),
                }
            )

    identity_candidates = [
        group
        for group in open_groups
        if str(group.get("evidence_role") or "").strip().lower() == "identity"
    ]
    v2_vehicle_candidates = [
        {
            **item,
            "source_evidence_id": source.get("evidence_id"),
        }
        for source in source_candidates
        for item in (source.get("vehicle_candidates") or [])
        if isinstance(item, dict)
        and str(item.get("identity_state") or "") in {
            "registered_other_vehicle_match",
            "possible_other_vehicle",
            "uncertain_vehicle",
        }
    ]
    possible_unregistered_vehicle = bool(
        (
            identity_candidates
            and any(
                group.get("classification") in {"other_episode", "unassigned"}
                for group in identity_candidates
            )
        )
        or any(
            item.get("identity_state") in {
                "possible_other_vehicle",
                "uncertain_vehicle",
            }
            for item in v2_vehicle_candidates
        )
    )

    reconciliation_backlog: list[dict[str, Any]] = []
    for episode in episodes:
        reconciliation = episode.get("reconciliation")
        if reconciliation is None:
            if episode.get("case_attribution"):
                reconciliation_backlog.append(
                    {
                        "episode_id": episode.get("episode_id"),
                        "title": episode.get("title"),
                        "state": "reconciliation_not_prepared",
                    }
                )
            continue

        review_status = str(reconciliation.get("review_status") or "").strip().lower()
        counts = reconciliation.get("decision_counts") or {}
        unreviewed = int(counts.get("unreviewed") or 0)
        extraction_id = reconciliation.get("extraction_id")

        if review_status in {"accepted", "corrected"} and not unreviewed:
            applied = (
                applied_reconciliation_plan(int(extraction_id))
                if extraction_id
                else None
            )
            if applied is None:
                reconciliation_backlog.append(
                    {
                        "episode_id": episode.get("episode_id"),
                        "title": episode.get("title"),
                        "state": "ready_to_apply",
                        "reconciliation_extraction_id": extraction_id,
                        "unreviewed_candidates": 0,
                    }
                )
            continue

        reconciliation_backlog.append(
            {
                "episode_id": episode.get("episode_id"),
                "title": episode.get("title"),
                "state": (
                    "advisor_review_required"
                    if review_status not in {"accepted", "corrected"}
                    else "candidate_decisions_incomplete"
                ),
                "reconciliation_extraction_id": extraction_id,
                "unreviewed_candidates": unreviewed,
            }
        )

    return {
        "scope": "advisor_supervised_historical_copilot",
        "candidate_only": True,
        "selected_car_id": context.car_id,
        "owner_user_id": owner.id if owner is not None else None,
        "source_review_counts": dict(source_review_counts),
        "top_level_source_count": len(source_rows),
        "pending_source_ids": pending_source_ids,
        "source_candidate_backlog": source_candidates,
        "historical_intelligence_version": max(
            [
                int(item.get("historical_intelligence_version") or 0)
                for item in source_candidates
            ]
            or [0]
        ),
        "source_coverage": [
            {
                "evidence_id": item.get("evidence_id"),
                **(item.get("source_coverage") or {}),
            }
            for item in source_candidates
            if item.get("source_coverage")
        ],
        "vehicle_candidates": [
            {
                **vehicle,
                "source_evidence_id": item.get("evidence_id"),
            }
            for item in source_candidates
            for vehicle in (item.get("vehicle_candidates") or [])
            if isinstance(vehicle, dict)
        ][:40],
        "service_episode_candidates": [
            {
                **episode,
                "source_evidence_id": item.get("evidence_id"),
            }
            for item in source_candidates
            for episode in (item.get("service_episode_candidates") or [])
            if isinstance(episode, dict)
        ][:120],
        "canonical_comparisons": [
            {
                **comparison,
                "source_evidence_id": item.get("evidence_id"),
            }
            for item in source_candidates
            for comparison in (item.get("canonical_comparisons") or [])
            if isinstance(comparison, dict)
        ][:120],
        "canonical_comparison_counts": dict(
            Counter(
                str(comparison.get("comparison") or "uncertain")
                for item in source_candidates
                for comparison in (item.get("canonical_comparisons") or [])
                if isinstance(comparison, dict)
            )
        ),
        "unrecorded_candidate_count": sum(
            1
            for item in source_candidates
            for comparison in (item.get("canonical_comparisons") or [])
            if isinstance(comparison, dict)
            and comparison.get("comparison") == "missing_from_durable_history"
        ),
        "priority_thread_count": sum(
            len(item.get("priority_threads") or []) for item in source_candidates
        ),
        "reconciliation_backlog": reconciliation_backlog[:12],
        "unresolved_attribution_groups": open_groups[:20],
        "known_other_client_vehicles": known_other_vehicles,
        "possible_unregistered_vehicle": possible_unregistered_vehicle,
        "possible_unregistered_vehicle_evidence": identity_candidates[:8],
        "vehicle_identity_proposals": (
            [
                {
                    "title": item.get("title"),
                    "summary": item.get("summary"),
                    "confidence": item.get("confidence"),
                    "source_refs": item.get("source_refs") or [],
                    "proposal_state": "human_confirmation_required",
                }
                for item in identity_candidates[:8]
            ]
            + [
                {
                    "title": (
                        item.get("make_model_year")
                        or item.get("identity_summary")
                        or "Possible additional vehicle"
                    ),
                    "summary": item.get("identity_summary"),
                    "confidence": item.get("confidence"),
                    "source_refs": item.get("source_refs") or [],
                    "vin": item.get("vin"),
                    "plate_number": item.get("plate_number"),
                    "identity_state": item.get("identity_state"),
                    "source_evidence_id": item.get("source_evidence_id"),
                    "proposal_state": "human_confirmation_required",
                }
                for item in v2_vehicle_candidates[:12]
            ]
        )[:16],
        "supervision_policy": {
            "rina_may_prepare": True,
            "advisor_must_review": True,
            "advisor_must_authorize_durable_write": True,
            "rina_may_self_approve": False,
            "new_vehicle_identity_requires_human_confirmation": True,
        },
    }


def _completion_detail(action: TreatmentAction) -> dict[str, Any] | None:
    detail = getattr(action, "completion_detail", None)
    if detail is None:
        return None
    return {
        "kind": detail.action_kind,
        "component_name": _clip(detail.component_name, limit=220),
        "component_location": _clip(detail.component_location, limit=120),
        "component_condition": detail.component_condition,
        "quantity": detail.quantity,
        "odometer_km": detail.odometer_km,
        "source_evidence_id": detail.source_evidence_id,
        "verification_status": detail.verification_status,
        "verified_by_user_id": detail.verified_by_user_id,
        "verified_at": _iso(detail.verified_at),
    }


def _action_addenda(action: TreatmentAction) -> list[dict[str, Any]]:
    return [
        {
            "addendum_id": row.id,
            "category": row.category,
            "reason": _clip(row.reason, limit=240),
            "visibility": row.visibility,
            "detail": _clip(row.detail_text, limit=700),
            "created_by_user_id": row.created_by_user_id,
            "created_at": _iso(row.created_at),
        }
        for row in (getattr(action, "addenda", []) or [])[-6:]
    ]


def _treatment_context(car_id: int) -> list[dict[str, Any]]:
    plans = (
        TreatmentPlan.query.filter_by(car_id=car_id)
        .order_by(TreatmentPlan.created_at.desc(), TreatmentPlan.id.desc())
        .limit(_MAX_PLANS)
        .all()
    )
    result: list[dict[str, Any]] = []
    for plan in plans:
        actions = (
            TreatmentAction.query.filter_by(
                treatment_plan_id=plan.id,
                car_id=car_id,
            )
            .order_by(TreatmentAction.id.asc())
            .limit(_MAX_ACTIONS_PER_PLAN)
            .all()
        )
        outcomes = (
            TreatmentOutcome.query.filter_by(
                treatment_plan_id=plan.id,
                car_id=car_id,
            )
            .order_by(TreatmentOutcome.observed_at.desc(), TreatmentOutcome.id.desc())
            .limit(_MAX_OUTCOMES_PER_PLAN)
            .all()
        )

        result.append(
            {
                "treatment_plan_id": plan.id,
                "title": _clip(plan.title, limit=255),
                "status": plan.status,
                "record_origin": plan.record_origin,
                "advisor_id": plan.advisor_id,
                "source_evidence_id": plan.source_evidence_id,
                "source_extraction_id": plan.source_extraction_id,
                "created_at": _iso(plan.created_at),
                "updated_at": _iso(plan.updated_at),
                "actions": [
                    {
                        "treatment_action_id": action.id,
                        "title": _clip(action.title, limit=255),
                        "status": action.status,
                        "visibility": action.visibility,
                        "scheduled_for": _iso(action.scheduled_for),
                        "started_at": _iso(action.started_at),
                        "completed_at": _iso(action.completed_at),
                        "completion_detail": _completion_detail(action),
                        "addenda": _action_addenda(action),
                    }
                    for action in actions
                ],
                "outcomes": [
                    {
                        "treatment_outcome_id": outcome.id,
                        "treatment_action_id": outcome.treatment_action_id,
                        "progression_direction": outcome.progression_direction,
                        "summary": _clip(outcome.summary, limit=600),
                        "visibility": outcome.visibility,
                        "provenance_kind": outcome.provenance_kind,
                        "observed_at": _iso(outcome.observed_at),
                        "recorded_by_user_id": outcome.recorded_by_user_id,
                    }
                    for outcome in outcomes
                ],
            }
        )
    return result


def _display_treatment_action_title(title: object, status: object) -> str | None:
    """Clean stale state words for presentation without mutating stored history."""

    clean = _clip(title, limit=255)
    if not clean:
        return None

    # Historical reconciliation titles sometimes preserve words that describe
    # the action's old workflow state. The canonical status now carries that
    # state explicitly, so repeating it in the display label is confusing.
    clean = re.sub(
        r"(?:\s*[-–—:]?\s*\b(?:authori[sz]ed|recommended|completed)\b[.!]?\s*)+$",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip(" -–—:")

    if str(status or "").strip().lower() == "completed":
        clean = re.sub(
            r"(?:\s*[-–—:]?\s*\bplan\b[.!]?\s*)+$",
            "",
            clean,
            flags=re.IGNORECASE,
        ).strip(" -–—:")

    return clean or _clip(title, limit=255)


def _canonical_treatment_action_index(
    treatment_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the compact action-state authority for longitudinal summaries.

    Historical extraction and reconciliation records remain useful provenance,
    but only durable Treatment Actions are authoritative for whether an action
    is currently recorded as recommended, authorised, in progress or completed.
    """

    index: list[dict[str, Any]] = []
    for plan in treatment_history:
        for action in plan.get("actions") or []:
            completion = action.get("completion_detail") or {}
            index.append(
                {
                    "treatment_action_id": action.get("treatment_action_id"),
                    "treatment_plan_id": plan.get("treatment_plan_id"),
                    "title": action.get("title"),
                    "display_title": _display_treatment_action_title(
                        action.get("title"),
                        action.get("status"),
                    ),
                    "status": action.get("status"),
                    "completed_at": action.get("completed_at"),
                    "verification_status": completion.get("verification_status"),
                    "record_origin": plan.get("record_origin"),
                    "precedence": "canonical_treatment_action",
                }
            )
    return index


def _evidence_context(car_id: int) -> dict[str, Any]:
    active = VehicleEvidence.query.filter(
        VehicleEvidence.car_id == car_id,
        VehicleEvidence.storage_state == "available",
        VehicleEvidence.deleted_at.is_(None),
    )
    rows = (
        active.order_by(VehicleEvidence.uploaded_at.desc(), VehicleEvidence.id.desc())
        .limit(_MAX_EVIDENCE_ROWS)
        .all()
    )
    all_rows = active.all()

    source_counts = Counter(
        str(row.historical_source_type or row.source_channel or "other")
        for row in all_rows
    )
    review_counts = Counter(str(row.review_status or "unknown") for row in all_rows)

    return {
        "active_count": len(all_rows),
        "source_counts": dict(source_counts),
        "review_counts": dict(review_counts),
        "recent_sources": [
            {
                "evidence_id": row.id,
                "evidence_type": row.evidence_type,
                "purpose": row.purpose,
                "source_channel": row.source_channel,
                "historical_source_type": row.historical_source_type,
                "review_status": row.review_status,
                "uploaded_at": _iso(row.uploaded_at),
                "safe_display_name": _clip(row.safe_display_name, limit=160),
            }
            for row in rows
        ],
    }


def _event_context(context: RinaResolvedContext) -> list[dict[str, Any]]:
    rows = (
        VehicleEvent.query.filter(
            VehicleEvent.car_id == context.car_id,
            VehicleEvent.is_deleted.is_(False),
            VehicleEvent.visibility.in_(context.visibility_scope),
        )
        .order_by(
            VehicleEvent.occurred_at.desc(),
            VehicleEvent.recorded_at.desc(),
            VehicleEvent.id.desc(),
        )
        .limit(_MAX_EVENTS)
        .all()
    )
    return [
        {
            "event_id": row.id,
            "event_type": row.event_type,
            "title": _clip(row.title, limit=160),
            "subject_type": row.subject_type,
            "subject_id": row.subject_id,
            "actor_user_id": row.actor_user_id,
            "actor_authority": row.actor_authority,
            "visibility": row.visibility,
            "previous_state": row.previous_state,
            "new_state": row.new_state,
            "progression_direction": row.progression_direction,
            "correlation_id": row.correlation_id,
            "correction_of_event_id": row.correction_of_event_id,
            "evidence_refs": (row.evidence_refs or [])[:10],
            "occurred_at": _iso(row.occurred_at),
            "recorded_at": _iso(row.recorded_at),
        }
        for row in rows
    ]


def _rina_audit_context(car_id: int) -> list[dict[str, Any]]:
    rows = (
        RinaAIAuditEvent.query.filter_by(car_id=car_id)
        .order_by(RinaAIAuditEvent.created_at.desc(), RinaAIAuditEvent.id.desc())
        .limit(_MAX_RINA_AUDIT)
        .all()
    )
    return [
        {
            "audit_event_id": row.id,
            "request_id": row.request_id,
            "user_id": row.user_id,
            "authority": row.authority,
            "state": row.state,
            "outcome": row.outcome,
            "action_family": row.action_family,
            "provider": row.provider,
            "provider_model": row.provider_model,
            "provider_status": row.provider_status,
            "evidence_refs": row.evidence_refs or [],
            "audit_metadata": row.audit_metadata or {},
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


def _profile_audit_context(owner_user_id: int | None) -> list[dict[str, Any]]:
    if owner_user_id is None:
        return []
    rows = (
        ProfileAuditEvent.query.filter_by(user_id=owner_user_id)
        .order_by(ProfileAuditEvent.created_at.desc(), ProfileAuditEvent.id.desc())
        .limit(_MAX_PROFILE_AUDIT)
        .all()
    )
    return [
        {
            "profile_audit_event_id": row.id,
            "action": row.action,
            "changed_fields": row.changed_fields or [],
            "success": row.success,
            "reason_code": row.reason_code,
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


def build_rina_advisor_360_context(
    context: RinaResolvedContext,
) -> dict[str, Any] | None:
    """Return compact longitudinal context for a privileged, scoped Rina request."""

    if context.authority not in _PRIVILEGED:
        return None

    car = db.session.get(Car, context.car_id)
    if car is None:
        return None

    owner = _safe_owner_context(car)
    owner_user_id = owner.get("owner_user_id") if owner else None
    treatment_history = _treatment_context(context.car_id)
    historical_copilot = build_rina_historical_copilot_context(context)

    return {
        "context_version": 1,
        "scope": {
            "car_id": context.car_id,
            "authority": context.authority,
            "generated_from": "Aura canonical and reviewed records",
            "read_only": True,
        },
        "client_relationship": {
            "owner": owner,
            "active_drivers": _driver_context(context.car_id),
        },
        "historical_episodes": _historical_episodes(context.car_id),
        "historical_copilot": historical_copilot,
        "treatment_history": treatment_history,
        "canonical_treatment_action_index": _canonical_treatment_action_index(
            treatment_history
        ),
        "record_precedence": {
            "action_state_source": "canonical_treatment_action_index",
            "historical_role": "supporting_provenance",
            "rule": (
                "For the same or semantically equivalent intervention, the durable "
                "Treatment Action status is authoritative. Historical extraction "
                "or reconciliation records may explain provenance or uncertainty "
                "but must not be presented as a second action with a competing status."
            ),
        },
        "evidence_index": _evidence_context(context.car_id),
        "canonical_events": _event_context(context),
        "audit": {
            "rina_ai_events": _rina_audit_context(context.car_id),
            "client_profile_events": _profile_audit_context(owner_user_id),
        },
    }
