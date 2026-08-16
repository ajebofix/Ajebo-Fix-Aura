"""Authority-first, server-mediated image evidence intake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import uuid
from typing import Mapping

from sqlalchemy.exc import SQLAlchemyError

from evidence.image_sanitizer import (
    EvidenceImageValidationError,
    SanitizedEvidenceImage,
    sanitize_evidence_image,
)
from evidence.models import EVIDENCE_PURPOSES, EVIDENCE_VISIBILITY, VehicleEvidence
from evidence.storage import (
    EvidenceStorageConfigurationError,
    EvidenceStorageError,
    EvidenceStorageProvider,
    R2EvidenceStorageProvider,
)
from extensions import db
from models import Car
from security.access import resolve_vehicle_authority


logger = logging.getLogger(__name__)

_OWNER_PURPOSES = frozenset(EVIDENCE_PURPOSES)
_DRIVER_PURPOSES = frozenset({"concern_support", "driver_observation"})
_ADVISOR_PURPOSES = frozenset(EVIDENCE_PURPOSES)


class EvidenceIntakeError(RuntimeError):
    """Base user-safe failure for image evidence intake."""


class EvidenceIntakeAccessError(EvidenceIntakeError):
    """Raised when vehicle authority or requested visibility is not allowed."""


class EvidenceIntakeConfigurationError(EvidenceIntakeError):
    """Raised when private evidence storage is not ready."""


@dataclass(frozen=True)
class EvidenceIntakeResult:
    evidence_id: int
    car_id: int
    evidence_type: str
    purpose: str
    visibility: str
    review_status: str
    storage_state: str
    content_type: str
    byte_size: int
    sha256: str
    width: int
    height: int


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _allowed_purposes(authority: str) -> frozenset[str]:
    if authority == "owner":
        return _OWNER_PURPOSES
    if authority == "driver":
        return _DRIVER_PURPOSES
    if authority in {"advisor", "administrator"}:
        return _ADVISOR_PURPOSES
    return frozenset()


def _resolved_visibility(authority: str, requested: str | None) -> str:
    if authority in {"owner", "driver"}:
        if requested and requested != "client":
            raise EvidenceIntakeAccessError(
                "Client and driver evidence cannot be uploaded into a private advisor channel."
            )
        return "client"

    if authority in {"advisor", "administrator"}:
        visibility = (requested or "advisor").strip().lower()
        if visibility not in EVIDENCE_VISIBILITY:
            raise EvidenceIntakeAccessError("Evidence visibility is not supported.")
        return visibility

    raise EvidenceIntakeAccessError("No evidence-upload authority exists for this vehicle.")


def _storage_provider_from_config(config: Mapping[str, object]) -> EvidenceStorageProvider:
    provider_name = str(config.get("EVIDENCE_STORAGE_PROVIDER") or "r2").strip().lower()
    if provider_name != "r2":
        raise EvidenceIntakeConfigurationError(
            "Aura's configured private evidence storage provider is not supported."
        )

    try:
        return R2EvidenceStorageProvider.from_config(config)
    except EvidenceStorageConfigurationError as exc:
        raise EvidenceIntakeConfigurationError(
            "Private evidence storage is not configured."
        ) from exc


def _object_key(*, car_id: int, extension: str) -> tuple[str, str]:
    token = uuid.uuid4().hex
    return (
        f"evidence/vehicles/{car_id}/{token}{extension}",
        f"vehicle-evidence-{token[:12]}{extension}",
    )


def _mark_storage_failure(evidence_id: int, reason_code: str) -> None:
    try:
        record = db.session.get(VehicleEvidence, evidence_id)
        if record is None:
            return
        record.storage_state = "failed"
        record.storage_failure_reason_code = reason_code
        record.updated_at = _utcnow_naive()
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "evidence_storage_failure_state_persist_failed evidence_id=%s",
            evidence_id,
        )


def create_image_evidence(
    *,
    user_id: int,
    car_id: int,
    file_stream,
    declared_content_type: str,
    purpose: str,
    consent_confirmed: bool,
    requested_visibility: str | None = None,
    source_channel: str = "web",
    storage_provider: EvidenceStorageProvider | None = None,
    storage_config: Mapping[str, object] | None = None,
) -> EvidenceIntakeResult:
    """Persist one sanitized image while preserving DB/object compensation rules."""

    car = db.session.get(Car, car_id)
    if car is None:
        raise EvidenceIntakeAccessError("Vehicle was not found.")

    authority = resolve_vehicle_authority(user_id, car_id)
    if authority is None:
        raise EvidenceIntakeAccessError("You do not have access to this vehicle.")

    normalized_purpose = (purpose or "").strip().lower()
    if normalized_purpose not in _allowed_purposes(authority):
        raise EvidenceIntakeAccessError(
            "This evidence purpose is not available for your vehicle relationship."
        )

    if source_channel != "web":
        raise EvidenceIntakeAccessError(
            "Only authenticated web evidence intake is enabled in this release."
        )

    visibility = _resolved_visibility(authority, requested_visibility)

    if not consent_confirmed:
        raise EvidenceIntakeAccessError(
            "Confirm that this media may be stored for the vehicle-care purpose."
        )

    try:
        sanitized: SanitizedEvidenceImage = sanitize_evidence_image(
            file_stream,
            declared_content_type=declared_content_type,
        )
    except EvidenceImageValidationError:
        raise

    provider = storage_provider
    if provider is None:
        if storage_config is None:
            raise EvidenceIntakeConfigurationError(
                "Private evidence storage is not configured."
            )
        provider = _storage_provider_from_config(storage_config)

    object_key, safe_display_name = _object_key(
        car_id=car_id,
        extension=sanitized.extension,
    )

    evidence = VehicleEvidence(
        car_id=car_id,
        uploaded_by_user_id=user_id,
        evidence_type="image",
        purpose=normalized_purpose,
        source_channel="web",
        visibility=visibility,
        review_status="pending_review",
        storage_provider=provider.provider_name,
        storage_state="pending",
        object_key=object_key,
        safe_display_name=safe_display_name,
        content_type=sanitized.content_type,
        byte_size=sanitized.byte_size,
        sha256=sanitized.sha256,
        captured_at=None,
        capture_time_source=None,
        uploaded_at=_utcnow_naive(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
    )

    try:
        db.session.add(evidence)
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise EvidenceIntakeError("Aura could not create the evidence record.") from exc

    if evidence.id is None:
        raise EvidenceIntakeError("Aura could not create the evidence record.")

    try:
        stored = provider.put_bytes(
            object_key=object_key,
            payload=sanitized.payload,
            content_type=sanitized.content_type,
        )
        if stored.object_key != object_key or stored.byte_size != sanitized.byte_size:
            raise EvidenceStorageError("Private storage confirmation did not match intake.")
    except EvidenceStorageError as exc:
        _mark_storage_failure(evidence.id, "write_failed")
        logger.warning(
            "evidence_storage_write_failed evidence_id=%s car_id=%s provider=%s",
            evidence.id,
            car_id,
            provider.provider_name,
        )
        raise EvidenceIntakeError(
            "Aura could not securely store this evidence. Nothing was accepted for review."
        ) from exc

    try:
        record = db.session.get(VehicleEvidence, evidence.id)
        if record is None:
            raise EvidenceIntakeError("Evidence record disappeared during finalization.")
        record.storage_state = "available"
        record.storage_failure_reason_code = None
        record.updated_at = _utcnow_naive()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        compensation_ok = False
        try:
            provider.delete(object_key=object_key)
            compensation_ok = True
        except EvidenceStorageError:
            logger.exception(
                "evidence_storage_compensation_failed evidence_id=%s car_id=%s provider=%s",
                evidence.id,
                car_id,
                provider.provider_name,
            )

        _mark_storage_failure(
            evidence.id,
            "finalization_failed_compensated"
            if compensation_ok
            else "finalization_failed_orphan_risk",
        )
        raise EvidenceIntakeError(
            "Aura could not finalize this evidence record. Nothing was accepted for review."
        ) from exc

    logger.info(
        "evidence_image_accepted evidence_id=%s car_id=%s uploader_id=%s authority=%s visibility=%s content_type=%s byte_size=%s",
        evidence.id,
        car_id,
        user_id,
        authority,
        visibility,
        sanitized.content_type,
        sanitized.byte_size,
    )

    return EvidenceIntakeResult(
        evidence_id=evidence.id,
        car_id=car_id,
        evidence_type="image",
        purpose=normalized_purpose,
        visibility=visibility,
        review_status="pending_review",
        storage_state="available",
        content_type=sanitized.content_type,
        byte_size=sanitized.byte_size,
        sha256=sanitized.sha256,
        width=sanitized.width,
        height=sanitized.height,
    )
