"""Private PDF intake and governed extraction for historical vehicle records.

Historical ingestion is deliberately stronger than ordinary chat:
1) store the original source privately;
2) read the whole PDF with an advisor-grade analyzer;
3) reconstruct the job/document before structuring any records;
4) present candidate facts for human review;
5) only explicit advisor approval can create durable Aura history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
import hashlib
import json
import re
import uuid
from typing import Any, Mapping

from flask import current_app
from pypdf import PdfReader
from sqlalchemy.exc import SQLAlchemyError

from evidence.models import EvidenceExtraction, VehicleEvidence
from evidence.review import EvidenceReviewError, review_evidence
from evidence.storage import (
    EvidenceStorageConfigurationError,
    EvidenceStorageError,
    EvidenceStorageProvider,
    build_evidence_storage_provider,
)
from extensions import db
from historical_ingestion.advisor_analyzer import HistoricalAdvisorAnalyzer
from models import Car
from rina.providers.base import RinaProviderError
from security.access import resolve_vehicle_authority
from security.field_encryption import (
    ProfileEncryptionError,
    decrypt_profile_value,
    encrypt_profile_value,
)

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_PAGES = 80
MAX_EXTRACTED_TEXT_CHARS = 180_000

_ALLOWED_PURPOSES = {"service_document", "diagnostic_document", "treatment_evidence"}
_ALLOWED_VISIBILITY = {"client", "advisor"}
_ALLOWED_CATEGORIES = {
    "document_reference",
    "reported_concern",
    "observation",
    "diagnosis_context",
    "work_item",
    "outcome",
    "mileage",
    "financial",
}
_ALLOWED_STATES = {
    "reported",
    "observed",
    "recommended",
    "authorized",
    "completed",
    "outcome_observed",
    "unknown",
}
_ALLOWED_DESTINATIONS = {
    "context_only",
    "reported_concern",
    "assessment",
    "treatment_plan",
    "treatment_action",
    "treatment_outcome",
    "mileage_observation",
    "vehicle_identity",
    "financial_separate",
}
_ALLOWED_ACTION_KINDS = {"service", "component_replacement", "other_intervention"}
_ALLOWED_COMPONENT_CONDITIONS = {
    "new",
    "preowned_tokunbo",
    "refurbished",
    "client_supplied",
    "unknown",
    "not_applicable",
}
_ALLOWED_OUTCOME_DIRECTIONS = {
    "improving",
    "stable",
    "deteriorating",
    "resolved",
    "insufficient_evidence",
}


class HistoricalIngestionError(RuntimeError):
    """Safe failure for advisor historical-record ingestion."""


class HistoricalIngestionAccessError(HistoricalIngestionError):
    pass


class HistoricalIngestionConfigurationError(HistoricalIngestionError):
    pass


class HistoricalDocumentValidationError(HistoricalIngestionError):
    pass


@dataclass(frozen=True)
class HistoricalIngestionResult:
    evidence_id: int
    text_extraction_id: int
    understanding_extraction_id: int | None
    structured_extraction_id: int | None
    structured_status: str
    page_count: int
    extracted_characters: int
    reused_existing: bool = False


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _authority(user_id: int, car_id: int) -> str:
    authority = resolve_vehicle_authority(user_id, car_id)
    if authority not in {"advisor", "administrator"}:
        raise HistoricalIngestionAccessError(
            "Historical record import requires advisor authority for this vehicle."
        )
    return authority


def _retention_deadline(retention_days: object) -> datetime:
    try:
        days = int(str(retention_days).strip())
    except (TypeError, ValueError) as exc:
        raise HistoricalIngestionConfigurationError(
            "Evidence retention policy is not configured."
        ) from exc
    if days <= 0:
        raise HistoricalIngestionConfigurationError(
            "Evidence retention policy is not configured."
        )
    return _utcnow_naive() + timedelta(days=days)


def _read_pdf(file_stream) -> bytes:
    payload = file_stream.read(MAX_DOCUMENT_BYTES + 1)
    if not payload:
        raise HistoricalDocumentValidationError("Select a non-empty PDF document.")
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise HistoricalDocumentValidationError("PDF documents are limited to 10 MB.")
    if not payload.startswith(b"%PDF-"):
        raise HistoricalDocumentValidationError("The uploaded file is not a valid PDF.")
    return payload


def _normalise_page_text(value: str) -> str:
    lines: list[str] = []
    for raw_line in value.splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _extract_pdf_text(payload: bytes) -> tuple[str, int]:
    """Extract searchable text while preserving page and line boundaries."""

    try:
        reader = PdfReader(BytesIO(payload), strict=False)
    except Exception as exc:
        raise HistoricalDocumentValidationError(
            "Aura could not safely read this PDF."
        ) from exc

    if len(reader.pages) > MAX_DOCUMENT_PAGES:
        raise HistoricalDocumentValidationError(
            f"PDF documents are limited to {MAX_DOCUMENT_PAGES} pages."
        )

    chunks: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            value = page.extract_text() or ""
        except Exception:
            value = ""
        value = _normalise_page_text(value)
        if value:
            chunks.append(f"--- PAGE {page_number} ---\n{value}")

    text = "\n\n".join(chunks).strip()
    if not text:
        raise HistoricalDocumentValidationError(
            "This PDF does not contain extractable text yet. Scanned-only/OCR documents "
            "will use the same review workflow when OCR support is enabled."
        )
    if len(text) > MAX_EXTRACTED_TEXT_CHARS:
        text = text[:MAX_EXTRACTED_TEXT_CHARS]
    return text, len(reader.pages)


def _payload_cipher(payload: dict[str, Any]) -> tuple[str, str, str]:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        ciphertext = encrypt_profile_value(raw)
    except ProfileEncryptionError as exc:
        raise HistoricalIngestionConfigurationError(
            "Encrypted extraction storage is not configured."
        ) from exc
    if not ciphertext:
        raise HistoricalIngestionConfigurationError(
            "Encrypted extraction storage is not configured."
        )
    version = ciphertext.split(":", 1)[0]
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return ciphertext, version, digest


def decrypt_extraction_payload(
    extraction: EvidenceExtraction,
    *,
    reviewed: bool = False,
) -> dict[str, Any]:
    ciphertext = (
        extraction.reviewed_result_ciphertext
        if reviewed
        else extraction.result_ciphertext
    )
    expected_sha = (
        extraction.reviewed_result_sha256 if reviewed else extraction.result_sha256
    )
    if not ciphertext:
        return {}
    try:
        raw = decrypt_profile_value(ciphertext)
    except ProfileEncryptionError as exc:
        raise HistoricalIngestionConfigurationError(
            "Aura could not decrypt the extraction payload."
        ) from exc
    if raw is None:
        return {}
    actual_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if expected_sha and actual_sha != expected_sha:
        raise HistoricalIngestionError("Extraction integrity check failed.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HistoricalIngestionError("Stored extraction payload is invalid.") from exc
    return payload if isinstance(payload, dict) else {}


def _safe_text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _normalise_for_match(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _excerpt_supported(excerpt: str, source_text: str) -> bool:
    candidate = _normalise_for_match(excerpt)
    source = _normalise_for_match(source_text)
    if not candidate:
        return False
    if candidate in source:
        return True
    words = [word for word in re.findall(r"[a-z0-9₦]+", candidate) if len(word) > 2]
    if not words:
        return False
    source_words = set(re.findall(r"[a-z0-9₦]+", source))
    overlap = sum(1 for word in words if word in source_words)
    return overlap / len(words) >= 0.72


def _first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(0).strip() if match else None


def _apply_deterministic_document_fallbacks(
    document: dict[str, Any],
    source_text: str,
) -> dict[str, Any]:
    """Recover obvious references without asking the model to invent them."""

    normalized = dict(document)
    if not normalized.get("job_reference"):
        normalized["job_reference"] = _first_match(
            r"\b(?:AJF[-\s]?)?JOB[-\s]?\d{4}[-\s]?\d+\b",
            source_text,
        )
    if not normalized.get("sow_reference"):
        normalized["sow_reference"] = _first_match(
            r"\bSOW[-\s]?\d{4}[-\s]?\d+\b",
            source_text,
        )
    if not normalized.get("reference") and normalized.get("job_reference"):
        normalized["reference"] = normalized["job_reference"]
    return normalized


def _normalise_suggestions(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    suggestions: list[str] = []
    for item in value[:12]:
        if isinstance(item, dict):
            item = item.get("suggestion") or item.get("text") or ""
        text = _safe_text(item, 600)
        if text:
            suggestions.append(text)
    return suggestions


def _normalise_provider_payload(
    payload: dict[str, Any],
    *,
    source_text: str,
    page_count: int,
) -> dict[str, Any]:
    raw_document = payload.get("document")
    document = raw_document if isinstance(raw_document, dict) else {}
    document = _apply_deterministic_document_fallbacks(document, source_text)

    normalized_document = {
        "document_type": _safe_text(document.get("document_type"), 80) or "unknown",
        "title": _safe_text(document.get("title"), 255) or None,
        "reference": _safe_text(document.get("reference"), 120) or None,
        "document_date": _safe_text(document.get("document_date"), 40) or None,
        "job_reference": _safe_text(document.get("job_reference"), 120) or None,
        "sow_reference": _safe_text(document.get("sow_reference"), 120) or None,
        "client_name": _safe_text(document.get("client_name"), 255) or None,
        "vehicle_description": (
            _safe_text(document.get("vehicle_description"), 255) or None
        ),
        "vin": _safe_text(document.get("vin"), 80) or None,
        "plate_number": _safe_text(document.get("plate_number"), 80) or None,
    }

    candidates: list[dict[str, Any]] = []
    raw_candidates = payload.get("candidates")
    if isinstance(raw_candidates, list):
        for index, item in enumerate(raw_candidates[:100], start=1):
            if not isinstance(item, dict):
                continue

            category = _safe_text(item.get("category"), 40).lower()
            if category not in _ALLOWED_CATEGORIES:
                category = "observation"

            state = _safe_text(item.get("state"), 40).lower()
            if state not in _ALLOWED_STATES:
                state = "unknown"

            destination = _safe_text(
                item.get("suggested_destination"),
                48,
            ).lower()
            if destination not in _ALLOWED_DESTINATIONS:
                destination = "context_only"

            # Financial completion is not a mechanical/treatment state. Preserve
            # the commercial fact without letting "paid/finalised" imply repair.
            if category == "financial":
                destination = "financial_separate"
                state = "observed"

            try:
                confidence = float(item.get("confidence"))
                confidence = max(0.0, min(1.0, confidence))
            except (TypeError, ValueError):
                confidence = None

            source_excerpt = _safe_text(item.get("source_excerpt"), 700)
            source_verified = _excerpt_supported(source_excerpt, source_text)

            pages: list[int] = []
            raw_pages = item.get("source_pages")
            if isinstance(raw_pages, list):
                for value in raw_pages[:12]:
                    try:
                        page = int(value)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= page <= page_count and page not in pages:
                        pages.append(page)

            source_fact_ids: list[str] = []
            raw_fact_ids = item.get("source_fact_ids")
            if isinstance(raw_fact_ids, list):
                for value in raw_fact_ids[:12]:
                    fact_id = _safe_text(value, 80)
                    if fact_id and fact_id not in source_fact_ids:
                        source_fact_ids.append(fact_id)

            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            action_kind = _safe_text(action.get("kind"), 40).lower()
            if action_kind not in _ALLOWED_ACTION_KINDS:
                action_kind = "other_intervention"

            condition = _safe_text(
                action.get("component_condition"),
                40,
            ).lower()
            if condition not in _ALLOWED_COMPONENT_CONDITIONS:
                condition = "unknown"

            outcome_direction = _safe_text(
                item.get("outcome_direction"),
                40,
            ).lower()
            if outcome_direction not in _ALLOWED_OUTCOME_DIRECTIONS:
                outcome_direction = "insufficient_evidence"

            advisor_attention = _safe_text(item.get("advisor_attention"), 800)
            if not source_verified:
                warning = (
                    "Source excerpt could not be matched reliably against the "
                    "text extraction; verify this candidate against the PDF."
                )
                advisor_attention = (
                    f"{advisor_attention} {warning}".strip()
                    if advisor_attention
                    else warning
                )
                if confidence is not None:
                    confidence = min(confidence, 0.45)

            candidates.append(
                {
                    "candidate_id": f"c{index}",
                    "category": category,
                    "state": state,
                    "title": _safe_text(item.get("title"), 255) or "Extracted record",
                    "detail": _safe_text(item.get("detail"), 3000),
                    "occurred_at": _safe_text(item.get("occurred_at"), 40) or None,
                    "source_pages": pages,
                    "source_fact_ids": source_fact_ids,
                    "source_excerpt": source_excerpt,
                    "source_verified": source_verified,
                    "confidence": confidence,
                    "confidence_reason": _safe_text(
                        item.get("confidence_reason"),
                        800,
                    ),
                    "suggested_destination": destination,
                    "outcome_direction": outcome_direction,
                    "advisor_attention": advisor_attention,
                    "action": {
                        "kind": action_kind,
                        "component_name": (
                            _safe_text(action.get("component_name"), 255) or None
                        ),
                        "component_location": (
                            _safe_text(action.get("component_location"), 120) or None
                        ),
                        "component_condition": condition,
                        "quantity": _optional_int(action.get("quantity")),
                        "odometer_km": _optional_int(action.get("odometer_km")),
                    },
                    "requires_advisor_confirmation": True,
                }
            )

    return {
        "schema_version": 2,
        "document": normalized_document,
        "rina_summary": _safe_text(payload.get("rina_summary"), 5000),
        "advisor_suggestions": _normalise_suggestions(
            payload.get("advisor_suggestions")
        ),
        "candidates": candidates,
    }


def _find_reusable_document(
    *,
    car_id: int,
    sha256: str,
) -> tuple[
    VehicleEvidence,
    EvidenceExtraction,
    EvidenceExtraction | None,
    EvidenceExtraction,
] | None:
    evidence = (
        VehicleEvidence.query.filter(
            VehicleEvidence.car_id == car_id,
            VehicleEvidence.evidence_type == "document",
            VehicleEvidence.sha256 == sha256,
            VehicleEvidence.storage_state == "available",
            VehicleEvidence.deleted_at.is_(None),
        )
        .order_by(VehicleEvidence.created_at.desc(), VehicleEvidence.id.desc())
        .first()
    )
    if evidence is None:
        return None

    text_extraction = (
        EvidenceExtraction.query.filter_by(
            evidence_id=evidence.id,
            extraction_type="document_text",
            status="completed",
        )
        .order_by(EvidenceExtraction.created_at.desc(), EvidenceExtraction.id.desc())
        .first()
    )
    structured = latest_structured_extraction(evidence.id)
    if text_extraction is None or structured is None or structured.status != "completed":
        return None

    understanding = (
        EvidenceExtraction.query.filter_by(
            evidence_id=evidence.id,
            extraction_type="document_understanding",
            status="completed",
        )
        .order_by(EvidenceExtraction.created_at.desc(), EvidenceExtraction.id.desc())
        .first()
    )
    return evidence, text_extraction, understanding, structured


def _store_document(
    *,
    user_id: int,
    car_id: int,
    payload: bytes,
    purpose: str,
    visibility: str,
    retention_days: object,
    storage_provider: EvidenceStorageProvider | None,
    storage_config: Mapping[str, object],
) -> VehicleEvidence:
    car = db.session.get(Car, car_id)
    if car is None:
        raise HistoricalIngestionAccessError("Vehicle was not found.")
    _authority(user_id, car_id)

    purpose = (purpose or "service_document").strip().lower()
    if purpose not in _ALLOWED_PURPOSES:
        raise HistoricalDocumentValidationError("Select a supported document purpose.")
    visibility = (visibility or "advisor").strip().lower()
    if visibility not in _ALLOWED_VISIBILITY:
        raise HistoricalDocumentValidationError("Select a supported document visibility.")

    provider = storage_provider
    if provider is None:
        try:
            provider = build_evidence_storage_provider(storage_config)
        except EvidenceStorageConfigurationError as exc:
            raise HistoricalIngestionConfigurationError(
                "Private evidence storage is not configured."
            ) from exc

    token = uuid.uuid4().hex
    object_key = f"evidence/{token[:2]}/{token}.pdf"
    now = _utcnow_naive()
    evidence = VehicleEvidence(
        car_id=car_id,
        uploaded_by_user_id=user_id,
        evidence_type="document",
        purpose=purpose,
        source_channel="web",
        visibility=visibility,
        review_status="pending_review",
        storage_provider=provider.provider_name,
        storage_state="pending",
        object_key=object_key,
        safe_display_name=f"vehicle-document-{token[:12]}.pdf",
        content_type="application/pdf",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        uploaded_at=now,
        consent_basis="advisor_vehicle_care_record",
        lawful_purpose="vehicle_care_recordkeeping",
        retention_until=_retention_deadline(retention_days),
    )
    try:
        db.session.add(evidence)
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise HistoricalIngestionError(
            "Aura could not create the document record."
        ) from exc

    try:
        stored = provider.put_bytes(
            object_key=object_key,
            payload=payload,
            content_type="application/pdf",
        )
        if stored.byte_size != len(payload) or stored.object_key != object_key:
            raise EvidenceStorageError(
                "Private storage confirmation did not match intake."
            )
    except EvidenceStorageError as exc:
        evidence.storage_state = "failed"
        evidence.storage_failure_reason_code = "document_write_failed"
        db.session.commit()
        raise HistoricalIngestionError(
            "Aura could not securely store this document."
        ) from exc

    evidence.storage_state = "available"
    evidence.storage_failure_reason_code = None
    db.session.commit()
    return evidence


def _trusted_vehicle_context(car: Car) -> dict[str, Any]:
    return {
        "car_id": car.id,
        "display_name": car.decoded_display_name,
        "brand": car.brand,
        "model": car.model,
        "year": car.year,
        "vin": car.vin,
        "recorded_mileage_km": car.current_mileage,
        "audience": "Ajebo Fix professional advisor",
        "purpose": "historical vehicle-care reconstruction",
    }


def ingest_pdf_document(
    *,
    user_id: int,
    car_id: int,
    file_stream,
    declared_content_type: str,
    purpose: str,
    visibility: str,
    retention_days: object,
    storage_provider: EvidenceStorageProvider | None = None,
    storage_config: Mapping[str, object] | None = None,
    language_provider=None,
) -> HistoricalIngestionResult:
    _authority(user_id, car_id)
    if declared_content_type and declared_content_type not in {
        "application/pdf",
        "application/octet-stream",
    }:
        raise HistoricalDocumentValidationError("Upload a PDF document.")

    pdf_payload = _read_pdf(file_stream)
    source_sha = hashlib.sha256(pdf_payload).hexdigest()

    reusable = _find_reusable_document(car_id=car_id, sha256=source_sha)
    if reusable is not None:
        evidence, text_extraction, understanding, structured = reusable
        provenance = text_extraction.provenance or {}
        return HistoricalIngestionResult(
            evidence_id=evidence.id,
            text_extraction_id=text_extraction.id,
            understanding_extraction_id=(
                understanding.id if understanding is not None else None
            ),
            structured_extraction_id=structured.id,
            structured_status=structured.status,
            page_count=int(provenance.get("page_count") or 0),
            extracted_characters=int(provenance.get("characters") or 0),
            reused_existing=True,
        )

    text, page_count = _extract_pdf_text(pdf_payload)
    evidence = _store_document(
        user_id=user_id,
        car_id=car_id,
        payload=pdf_payload,
        purpose=purpose,
        visibility=visibility,
        retention_days=retention_days,
        storage_provider=storage_provider,
        storage_config=storage_config or current_app.config,
    )

    text_payload = {
        "schema_version": 2,
        "text": text,
        "page_count": page_count,
        "characters": len(text),
        "layout_preserved": True,
    }
    cipher, version, digest = _payload_cipher(text_payload)
    text_extraction = EvidenceExtraction(
        evidence_id=evidence.id,
        extraction_type="document_text",
        provider="pypdf",
        provider_model=None,
        status="completed",
        confidence=1.0,
        result_ciphertext=cipher,
        result_key_version=version,
        result_sha256=digest,
        provenance={
            "parser": "pypdf",
            "page_count": page_count,
            "characters": len(text),
            "layout_preserved": True,
            "semantic_authority": "none",
        },
        completed_at=_utcnow_naive(),
    )
    db.session.add(text_extraction)
    db.session.commit()

    structured = EvidenceExtraction(
        evidence_id=evidence.id,
        extraction_type="structured_fields",
        provider="openai",
        status="processing",
        review_status="unreviewed",
        provenance={
            "source_extraction_id": text_extraction.id,
            "analysis_pipeline": "advisor_grade_v2",
            "semantic_authority": "candidate_only",
        },
    )
    db.session.add(structured)
    db.session.commit()
    structured_id = structured.id
    understanding_id: int | None = None

    analyzer = language_provider or HistoricalAdvisorAnalyzer()
    try:
        if not hasattr(analyzer, "analyze_pdf"):
            raise HistoricalIngestionConfigurationError(
                "Historical document analyzer does not support whole-PDF analysis."
            )

        car = db.session.get(Car, car_id)
        if car is None:
            raise HistoricalIngestionAccessError("Vehicle was not found.")

        analysis = analyzer.analyze_pdf(
            pdf_payload=pdf_payload,
            extracted_text=text,
            trusted_vehicle_context=_trusted_vehicle_context(car),
        )

        understanding_payload = (
            analysis.understanding
            if isinstance(analysis.understanding, dict)
            else {}
        )
        understanding_cipher, understanding_version, understanding_digest = (
            _payload_cipher(understanding_payload)
        )
        understanding = EvidenceExtraction(
            evidence_id=evidence.id,
            extraction_type="document_understanding",
            provider=analysis.provider,
            provider_model=analysis.model,
            provider_request_id=analysis.understanding_request_id,
            status="completed",
            review_status="unreviewed",
            result_ciphertext=understanding_cipher,
            result_key_version=understanding_version,
            result_sha256=understanding_digest,
            provenance={
                "source_extraction_id": text_extraction.id,
                "analysis_pipeline": "advisor_grade_v2",
                "reasoning_stage": "whole_document_understanding",
                "semantic_authority": "candidate_only",
            },
            completed_at=_utcnow_naive(),
        )
        db.session.add(understanding)
        db.session.flush()
        understanding_id = understanding.id

        normalized = _normalise_provider_payload(
            analysis.structured,
            source_text=text,
            page_count=page_count,
        )
        cipher, version, digest = _payload_cipher(normalized)

        structured.provider = analysis.provider
        structured.provider_model = analysis.model
        structured.provider_request_id = analysis.structured_request_id
        structured.status = "completed"
        structured.result_ciphertext = cipher
        structured.result_key_version = version
        structured.result_sha256 = digest
        structured.completed_at = _utcnow_naive()
        structured.provenance = {
            **(structured.provenance or {}),
            "schema_version": 2,
            "understanding_extraction_id": understanding.id,
            "provider_output_normalized": True,
            "direct_pdf_input": True,
            "reasoning_stage": "advisor_record_structuring",
        }
        db.session.commit()

    except (
        RinaProviderError,
        HistoricalIngestionError,
        ValueError,
        TypeError,
    ) as exc:
        db.session.rollback()
        structured = db.session.get(EvidenceExtraction, structured_id)
        if structured is not None:
            structured.status = "failed"
            structured.completed_at = _utcnow_naive()
            structured.provenance = {
                **(structured.provenance or {}),
                "failure_class": type(exc).__name__,
            }
            db.session.commit()

    structured = db.session.get(EvidenceExtraction, structured_id)
    return HistoricalIngestionResult(
        evidence_id=evidence.id,
        text_extraction_id=text_extraction.id,
        understanding_extraction_id=understanding_id,
        structured_extraction_id=structured_id,
        structured_status=structured.status if structured else "failed",
        page_count=page_count,
        extracted_characters=len(text),
        reused_existing=False,
    )


def latest_structured_extraction(evidence_id: int) -> EvidenceExtraction | None:
    return (
        EvidenceExtraction.query.filter_by(
            evidence_id=evidence_id,
            extraction_type="structured_fields",
        )
        .order_by(EvidenceExtraction.created_at.desc(), EvidenceExtraction.id.desc())
        .first()
    )


def save_advisor_review(
    *,
    extraction: EvidenceExtraction,
    actor_user_id: int,
    reviewed_payload: dict[str, Any],
) -> None:
    evidence = extraction.evidence
    if evidence is None:
        raise HistoricalIngestionError("Source evidence is unavailable.")
    _authority(actor_user_id, evidence.car_id)

    if extraction.status != "completed":
        raise HistoricalIngestionError(
            "Only completed extraction results can be reviewed."
        )

    if evidence.review_status == "rejected":
        raise HistoricalIngestionError(
            "Rejected source evidence cannot be converted into historical truth."
        )

    try:
        review_evidence(
            reviewer_user_id=actor_user_id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="sufficient_for_record",
            commit=False,
        )
    except EvidenceReviewError as exc:
        raise HistoricalIngestionError(str(exc)) from exc

    cipher, version, digest = _payload_cipher(reviewed_payload)
    extraction.reviewed_result_ciphertext = cipher
    extraction.reviewed_result_key_version = version
    extraction.reviewed_result_sha256 = digest
    extraction.review_status = "corrected"
    extraction.reviewed_by_user_id = actor_user_id
    extraction.reviewed_at = _utcnow_naive()
    extraction.review_reason_code = "historical_record_advisor_review"
    db.session.flush()
