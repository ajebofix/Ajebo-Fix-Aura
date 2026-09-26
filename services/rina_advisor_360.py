"""Governed longitudinal advisor context for A.J. Rina.

This module intentionally builds a compact, authority-filtered care graph rather
than dumping Aura's database into the provider prompt. It is advisor/admin only,
vehicle-scoped, bounded, provenance-aware and read-only.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from evidence.models import EvidenceExtraction, VehicleEvidence
from extensions import db
from historical_ingestion.models import HistoricalServiceEpisode
from historical_ingestion.service import HistoricalIngestionError, decrypt_extraction_payload
from models import Car, CarDriver, TreatmentPlan, User, VehicleEvent
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
