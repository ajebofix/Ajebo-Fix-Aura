"""Episode-aware attribution over an already-extracted WhatsApp evidence corpus."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any

from flask import current_app

from evidence.models import EvidenceExtraction, VehicleEvidence
from extensions import db
from historical_ingestion.models import HistoricalServiceEpisode
from historical_ingestion.service import (
    HistoricalIngestionError,
    _authority,
    _payload_cipher,
    _trusted_vehicle_context,
    _utcnow_naive,
    decrypt_extraction_payload,
    latest_structured_extraction,
)
from historical_ingestion.whatsapp_bundle import (
    _corpus,
    latest_whatsapp_bundle_extraction,
)
from historical_ingestion.whatsapp_bundle_analyzer import (
    WhatsAppBundleAdvisorAnalyzer,
)
from rina.providers.base import (
    RinaProviderError,
    RinaProviderTransientError,
)


PIPELINE = "historical_case_attribution_v1"
_ALLOWED_CLASSIFICATIONS = {
    "matched",
    "uncertain",
    "other_episode",
    "unassigned",
}
_ALLOWED_ROLES = {
    "identity",
    "reported_concern",
    "observation",
    "recommendation",
    "authorization",
    "completed_work",
    "outcome",
    "financial",
    "context",
}
_SOURCE_REF_RE = re.compile(
    r"\b(?:CHAT m\d{6}|(?:IMAGE|DOCUMENT|AUDIO|VIDEO|TRANSCRIPT) evidence:\d+)\b"
)


class HistoricalCaseAttributionError(HistoricalIngestionError):
    """Safe failure for historical episode attribution."""


@dataclass(frozen=True)
class HistoricalEpisodeCreationResult:
    episode_id: int
    created: bool


@dataclass(frozen=True)
class HistoricalCaseAttributionStart:
    episode_id: int
    corpus_evidence_id: int
    extraction_id: int
    status: str
    phase: str
    reused_existing: bool = False


@dataclass(frozen=True)
class HistoricalCaseAttributionStatus:
    episode_id: int
    corpus_evidence_id: int
    extraction_id: int
    status: str
    phase: str
    message: str
    review_ready: bool = False


def _clip(value: object, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _parse_datetime(value: object) -> datetime | None:
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
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _reviewed_anchor_payload(extraction: EvidenceExtraction) -> dict[str, Any]:
    if extraction.status != "completed" or extraction.review_status not in {
        "accepted",
        "corrected",
    }:
        raise HistoricalCaseAttributionError(
            "The standalone source must be reviewed/finalised before it can anchor an episode."
        )

    payload = decrypt_extraction_payload(extraction, reviewed=True)
    if not payload:
        raise HistoricalCaseAttributionError(
            "The finalised source does not contain a saved advisor review."
        )
    return payload


def _episode_date(payload: dict[str, Any]) -> datetime | None:
    document = payload.get("document")
    if isinstance(document, dict):
        parsed = _parse_datetime(document.get("document_date"))
        if parsed is not None:
            return parsed

    dates: list[datetime] = []
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        for row in candidates:
            if not isinstance(row, dict) or row.get("review_decision") != "accepted":
                continue
            parsed = _parse_datetime(row.get("occurred_at"))
            if parsed is not None:
                dates.append(parsed)
    return min(dates) if dates else None


def create_episode_from_finalized_source(
    *,
    car_id: int,
    evidence_id: int,
    actor_user_id: int,
) -> HistoricalEpisodeCreationResult:
    evidence = db.session.get(VehicleEvidence, evidence_id)
    if evidence is None or evidence.car_id != car_id:
        raise HistoricalCaseAttributionError("Historical source was not found.")
    _authority(actor_user_id, car_id)

    if evidence.historical_source_type != "standalone_document":
        raise HistoricalCaseAttributionError(
            "Only a standalone historical document can anchor a service episode."
        )
    if (
        evidence.review_status != "accepted"
        or evidence.deleted_at is not None
        or evidence.storage_state != "available"
    ):
        raise HistoricalCaseAttributionError(
            "Finalise this standalone source before creating a service episode."
        )

    existing = HistoricalServiceEpisode.query.filter_by(
        anchor_evidence_id=evidence.id
    ).first()
    if existing is not None:
        return HistoricalEpisodeCreationResult(
            episode_id=existing.id,
            created=False,
        )

    extraction = latest_structured_extraction(evidence.id)
    if extraction is None:
        raise HistoricalCaseAttributionError(
            "The finalised source has no completed advisor extraction."
        )
    payload = _reviewed_anchor_payload(extraction)
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}

    job_reference = _clip(
        document.get("job_reference") or document.get("reference"),
        limit=120,
    ) or None
    source_title = _clip(document.get("title"), limit=220)
    if source_title and job_reference and job_reference.lower() not in source_title.lower():
        title = f"{source_title} — {job_reference}"[:255]
    else:
        title = (
            source_title
            or (f"Historical service episode — {job_reference}" if job_reference else "")
            or f"Historical service episode — Evidence #{evidence.id}"
        )[:255]

    episode = HistoricalServiceEpisode(
        car_id=car_id,
        anchor_evidence_id=evidence.id,
        anchor_extraction_id=extraction.id,
        created_by_user_id=actor_user_id,
        title=title,
        job_reference=job_reference,
        episode_date=_episode_date(payload),
        status="active",
    )
    db.session.add(episode)
    db.session.flush()
    return HistoricalEpisodeCreationResult(
        episode_id=episode.id,
        created=True,
    )


def episode_anchor_context(episode: HistoricalServiceEpisode) -> dict[str, Any]:
    extraction = db.session.get(EvidenceExtraction, episode.anchor_extraction_id)
    if extraction is None or extraction.evidence_id != episode.anchor_evidence_id:
        raise HistoricalCaseAttributionError(
            "Historical episode anchor provenance is incomplete."
        )

    payload = _reviewed_anchor_payload(extraction)
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}

    accepted_facts: list[dict[str, Any]] = []
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        for row in candidates[:80]:
            if not isinstance(row, dict) or row.get("review_decision") != "accepted":
                continue
            accepted_facts.append(
                {
                    "category": _clip(row.get("category"), limit=40),
                    "state": _clip(row.get("state"), limit=40),
                    "title": _clip(row.get("title"), limit=220),
                    "detail": _clip(row.get("detail"), limit=1200),
                    "occurred_at": _clip(row.get("occurred_at"), limit=40) or None,
                    "source_fact_ids": [
                        _clip(ref, limit=120)
                        for ref in (row.get("source_fact_ids") or [])[:12]
                        if str(ref or "").strip()
                    ],
                }
            )

    return {
        "episode_id": episode.id,
        "title": episode.title,
        "job_reference": episode.job_reference,
        "episode_date": (
            episode.episode_date.isoformat()
            if episode.episode_date is not None
            else None
        ),
        "anchor_evidence_id": episode.anchor_evidence_id,
        "anchor_extraction_id": episode.anchor_extraction_id,
        "document": {
            "document_type": _clip(document.get("document_type"), limit=80),
            "title": _clip(document.get("title"), limit=255),
            "reference": _clip(document.get("reference"), limit=120),
            "job_reference": _clip(document.get("job_reference"), limit=120),
            "sow_reference": _clip(document.get("sow_reference"), limit=120),
            "document_date": _clip(document.get("document_date"), limit=40),
        },
        "reviewed_summary": _clip(payload.get("rina_summary"), limit=2200),
        "advisor_review_note": _clip(payload.get("advisor_review_note"), limit=1200),
        "accepted_facts": accepted_facts[:40],
    }


def episodes_for_car(car_id: int) -> list[HistoricalServiceEpisode]:
    return (
        HistoricalServiceEpisode.query.filter_by(
            car_id=car_id,
            status="active",
        )
        .order_by(
            HistoricalServiceEpisode.episode_date.desc(),
            HistoricalServiceEpisode.id.desc(),
        )
        .all()
    )


def latest_case_attribution(
    *,
    episode_id: int,
    corpus_evidence_id: int,
) -> EvidenceExtraction | None:
    rows = (
        EvidenceExtraction.query.filter_by(
            evidence_id=corpus_evidence_id,
            extraction_type="historical_case_attribution",
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
        ):
            return row
    return None


def _known_source_refs(corpus: str) -> set[str]:
    return set(_SOURCE_REF_RE.findall(corpus))


def _normalise_attribution_payload(
    payload: dict[str, Any],
    *,
    corpus: str,
) -> dict[str, Any]:
    known_refs = _known_source_refs(corpus)
    groups: list[dict[str, Any]] = []
    used_refs: set[str] = set()

    raw_groups = payload.get("evidence_groups")
    if isinstance(raw_groups, list):
        for row in raw_groups[:120]:
            if not isinstance(row, dict):
                continue
            classification = _clip(row.get("classification"), limit=32).lower()
            if classification not in _ALLOWED_CLASSIFICATIONS:
                continue

            role = _clip(row.get("evidence_role"), limit=32).lower()
            if role not in _ALLOWED_ROLES:
                role = "context"

            refs: list[str] = []
            raw_refs = row.get("source_refs")
            if isinstance(raw_refs, list):
                for value in raw_refs[:30]:
                    ref = _clip(value, limit=120)
                    if ref in known_refs and ref not in refs:
                        refs.append(ref)
            if not refs:
                continue

            try:
                confidence = float(row.get("confidence"))
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = min(max(confidence, 0.0), 1.0)

            groups.append(
                {
                    "classification": classification,
                    "title": _clip(row.get("title"), limit=255),
                    "summary": _clip(row.get("summary"), limit=1800),
                    "occurred_at": _clip(row.get("occurred_at"), limit=64) or None,
                    "evidence_role": role,
                    "source_refs": refs,
                    "source_excerpt": _clip(row.get("source_excerpt"), limit=1200),
                    "confidence": confidence,
                    "match_reason": _clip(row.get("match_reason"), limit=1600),
                }
            )
            used_refs.update(refs)

    attention = payload.get("advisor_attention")
    advisor_attention = (
        [_clip(item, limit=1200) for item in attention[:30] if str(item or "").strip()]
        if isinstance(attention, list)
        else []
    )

    counts = {
        key: sum(
            len(group["source_refs"])
            for group in groups
            if group["classification"] == key
        )
        for key in _ALLOWED_CLASSIFICATIONS
    }

    return {
        "schema_version": 1,
        "episode_summary": _clip(payload.get("episode_summary"), limit=3000),
        "match_overview": _clip(payload.get("match_overview"), limit=3000),
        "evidence_groups": groups,
        "advisor_attention": advisor_attention,
        "source_ref_counts": counts,
        "known_corpus_source_refs": len(known_refs),
        "attributed_source_refs": len(used_refs),
    }


def start_case_attribution(
    *,
    episode_id: int,
    corpus_evidence_id: int,
    actor_user_id: int,
    analyzer: WhatsAppBundleAdvisorAnalyzer | None = None,
) -> HistoricalCaseAttributionStart:
    episode = db.session.get(HistoricalServiceEpisode, episode_id)
    corpus_evidence = db.session.get(VehicleEvidence, corpus_evidence_id)
    if episode is None or episode.status != "active":
        raise HistoricalCaseAttributionError("Historical service episode was not found.")
    _authority(actor_user_id, episode.car_id)

    if corpus_evidence is None or corpus_evidence.car_id != episode.car_id:
        raise HistoricalCaseAttributionError(
            "WhatsApp corpus must belong to the same vehicle as the episode."
        )
    if (
        corpus_evidence.evidence_type != "archive"
        or corpus_evidence.historical_source_type != "whatsapp_conversation"
        or corpus_evidence.storage_state != "available"
        or corpus_evidence.deleted_at is not None
        or corpus_evidence.review_status == "superseded"
    ):
        raise HistoricalCaseAttributionError(
            "Select an active WhatsApp case bundle for this vehicle."
        )

    bundle_analysis = latest_whatsapp_bundle_extraction(corpus_evidence.id)
    if bundle_analysis is None or bundle_analysis.status != "completed":
        raise HistoricalCaseAttributionError(
            "The WhatsApp corpus must finish its source extraction before case attribution."
        )

    existing = latest_case_attribution(
        episode_id=episode.id,
        corpus_evidence_id=corpus_evidence.id,
    )
    if existing is not None and existing.status in {"processing", "completed"}:
        return HistoricalCaseAttributionStart(
            episode_id=episode.id,
            corpus_evidence_id=corpus_evidence.id,
            extraction_id=existing.id,
            status=existing.status,
            phase=str(
                (existing.provenance or {}).get("background_stage")
                or ("completed" if existing.status == "completed" else "attributing")
            ),
            reused_existing=True,
        )

    analyzer = analyzer or WhatsAppBundleAdvisorAnalyzer()
    corpus = _corpus(corpus_evidence)
    anchor = episode_anchor_context(episode)

    analysis = EvidenceExtraction(
        evidence_id=corpus_evidence.id,
        extraction_type="historical_case_attribution",
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
            "semantic_authority": "candidate_only",
            "schema_version": 1,
            "corpus_sha256": hashlib.sha256(corpus.encode("utf-8")).hexdigest(),
            "corpus_characters": len(corpus),
        },
    )
    db.session.add(analysis)
    db.session.commit()

    try:
        response = analyzer.start_case_attribution_background(
            corpus=corpus,
            episode_anchor=anchor,
            trusted_vehicle_context=_trusted_vehicle_context(corpus_evidence.car),
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
        raise HistoricalCaseAttributionError(
            "Rina could not start historical case attribution."
        ) from exc

    analysis.provider_model = response.model
    analysis.provider_request_id = response.response_id
    analysis.provenance = {
        **(analysis.provenance or {}),
        "background_stage": "attributing",
        "background_response_id": response.response_id,
    }
    db.session.commit()

    return HistoricalCaseAttributionStart(
        episode_id=episode.id,
        corpus_evidence_id=corpus_evidence.id,
        extraction_id=analysis.id,
        status="processing",
        phase="attributing",
    )


def _status(
    analysis: EvidenceExtraction,
    *,
    message: str,
    review_ready: bool = False,
) -> HistoricalCaseAttributionStatus:
    provenance = analysis.provenance or {}
    return HistoricalCaseAttributionStatus(
        episode_id=int(provenance.get("episode_id") or 0),
        corpus_evidence_id=analysis.evidence_id,
        extraction_id=analysis.id,
        status=analysis.status,
        phase=str(provenance.get("background_stage") or analysis.status),
        message=message,
        review_ready=review_ready,
    )


def advance_case_attribution(
    *,
    extraction_id: int,
    actor_user_id: int,
    analyzer: WhatsAppBundleAdvisorAnalyzer | None = None,
) -> HistoricalCaseAttributionStatus:
    analysis = db.session.get(EvidenceExtraction, extraction_id)
    if analysis is None or analysis.evidence is None:
        raise HistoricalCaseAttributionError("Historical case attribution was not found.")

    provenance = dict(analysis.provenance or {})
    if (
        analysis.extraction_type != "historical_case_attribution"
        or provenance.get("analysis_pipeline") != PIPELINE
    ):
        raise HistoricalCaseAttributionError("This analysis is not a case-attribution run.")

    episode = db.session.get(
        HistoricalServiceEpisode,
        int(provenance.get("episode_id") or 0),
    )
    if episode is None or episode.car_id != analysis.evidence.car_id:
        raise HistoricalCaseAttributionError(
            "Historical episode attribution provenance is incomplete."
        )
    _authority(actor_user_id, episode.car_id)

    if analysis.status == "completed":
        return _status(
            analysis,
            message="Historical case attribution is ready for advisor review.",
            review_ready=True,
        )
    if analysis.status == "failed":
        return _status(
            analysis,
            message="Historical case attribution failed safely. No vehicle history changed.",
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
            message="Historical case attribution failed safely. No vehicle history changed.",
        )

    try:
        response = analyzer.retrieve_background(response_id)
    except RinaProviderTransientError:
        return _status(
            analysis,
            message="Rina is still separating this episode from the WhatsApp history.",
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
            message="Historical case attribution failed safely. No vehicle history changed.",
        )

    if response.status in {"queued", "in_progress"}:
        return _status(
            analysis,
            message="Rina is still separating this episode from the WhatsApp history.",
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
            message="Historical case attribution failed safely. No vehicle history changed.",
        )

    corpus = _corpus(analysis.evidence)
    normalized = _normalise_attribution_payload(
        response.payload,
        corpus=corpus,
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
        "reasoning_stage": "episode_specific_whatsapp_attribution",
    }
    db.session.commit()

    current_app.logger.info(
        "historical_case_attribution_completed episode_id=%s corpus_evidence_id=%s extraction_id=%s groups=%s",
        episode.id,
        analysis.evidence_id,
        analysis.id,
        len(normalized.get("evidence_groups") or []),
    )
    return _status(
        analysis,
        message="Historical case attribution is ready for advisor review.",
        review_ready=True,
    )


def attribution_payload(extraction: EvidenceExtraction) -> dict[str, Any]:
    if (
        extraction.extraction_type != "historical_case_attribution"
        or extraction.status != "completed"
    ):
        return {}
    return decrypt_extraction_payload(extraction)
