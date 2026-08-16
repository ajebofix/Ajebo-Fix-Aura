"""Protected retrieval, governed deletion and storage reconciliation for evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
import secrets
from typing import Mapping

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import SQLAlchemyError

from evidence.models import EvidenceLink, VehicleEvidence
from evidence.storage import (
    EvidenceStorageConfigurationError,
    EvidenceStorageError,
    EvidenceStorageProvider,
    build_evidence_storage_provider,
)
from extensions import db
from security.access import resolve_vehicle_authority


logger = logging.getLogger(__name__)

_GRANT_SALT = "aura-evidence-retrieval-v1"
_MAX_GRANT_SECONDS = 300
_ALLOWED_DELETE_REASONS = frozenset(
    {
        "invalid_upload",
        "duplicate",
        "privacy_request_approved",
        "retention_expired",
        "superseded_cleanup",
        "operational_correction",
    }
)


class EvidenceRetrievalError(RuntimeError):
    """Base safe failure for private evidence retrieval workflows."""


class EvidenceRetrievalAccessError(EvidenceRetrievalError):
    """Raised when current vehicle authority cannot view the evidence."""


class EvidenceRetrievalConfigurationError(EvidenceRetrievalError):
    """Raised when retrieval/security configuration is incomplete."""


class EvidenceNotAvailableError(EvidenceRetrievalError):
    """Raised when evidence is not in a retrievable lifecycle state."""


class EvidenceDeletionConflict(EvidenceRetrievalError):
    """Raised when professional/audit relationships block immediate deletion."""


@dataclass(frozen=True)
class EvidenceRetrievalGrant:
    evidence_id: int
    token: str
    expires_in_seconds: int


@dataclass(frozen=True)
class EvidencePrivateContent:
    evidence_id: int
    payload: bytes
    content_type: str
    download_name: str
    byte_size: int
    review_status: str
    unreviewed: bool


@dataclass(frozen=True)
class EvidenceDeletionResult:
    evidence_id: int
    review_status: str
    storage_state: str
    storage_delete_pending: bool


@dataclass(frozen=True)
class EvidenceReconciliationSummary:
    examined: int
    repaired: int
    pending: int
    failed: int


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _positive_grant_seconds(value: object) -> int:
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise EvidenceRetrievalConfigurationError(
            "Evidence retrieval grant policy is not configured."
        ) from exc

    if seconds <= 0 or seconds > _MAX_GRANT_SECONDS:
        raise EvidenceRetrievalConfigurationError(
            "Evidence retrieval grant policy is not configured safely."
        )
    return seconds


def _serializer(secret_key: object) -> URLSafeTimedSerializer:
    secret = str(secret_key or "").strip()
    if not secret:
        raise EvidenceRetrievalConfigurationError(
            "Evidence retrieval signing key is not configured."
        )
    return URLSafeTimedSerializer(secret_key=secret, salt=_GRANT_SALT)


def _provider(
    *,
    storage_provider: EvidenceStorageProvider | None,
    storage_config: Mapping[str, object] | None,
) -> EvidenceStorageProvider:
    if storage_provider is not None:
        return storage_provider
    if storage_config is None:
        raise EvidenceRetrievalConfigurationError(
            "Private evidence storage is not configured."
        )
    try:
        return build_evidence_storage_provider(storage_config)
    except EvidenceStorageConfigurationError as exc:
        raise EvidenceRetrievalConfigurationError(
            "Private evidence storage is not configured."
        ) from exc


def _resolve_visible_evidence(*, user_id: int, evidence_id: int) -> tuple[VehicleEvidence, str]:
    evidence = db.session.get(VehicleEvidence, evidence_id)
    if evidence is None:
        raise EvidenceRetrievalAccessError("Evidence is not available for this account.")

    authority = resolve_vehicle_authority(user_id, evidence.car_id)
    if authority is None:
        raise EvidenceRetrievalAccessError("Evidence is not available for this account.")

    if authority in {"advisor", "administrator"}:
        return evidence, authority

    if authority == "owner":
        if evidence.visibility != "client":
            raise EvidenceRetrievalAccessError("Evidence is not available for this account.")
        return evidence, authority

    if authority == "driver":
        if evidence.visibility != "client" or evidence.uploaded_by_user_id != user_id:
            raise EvidenceRetrievalAccessError("Evidence is not available for this account.")
        return evidence, authority

    raise EvidenceRetrievalAccessError("Evidence is not available for this account.")


def _ensure_retrievable(evidence: VehicleEvidence) -> None:
    if evidence.review_status == "deleted" or evidence.deleted_at is not None:
        raise EvidenceNotAvailableError("Evidence has been deleted.")
    if evidence.storage_state != "available":
        raise EvidenceNotAvailableError("Evidence object is not currently available.")
    if evidence.byte_size <= 0 or not evidence.sha256:
        raise EvidenceNotAvailableError("Evidence integrity metadata is incomplete.")


def create_retrieval_grant(
    *,
    user_id: int,
    evidence_id: int,
    secret_key: object,
    grant_seconds: object,
) -> EvidenceRetrievalGrant:
    evidence, _authority = _resolve_visible_evidence(
        user_id=user_id,
        evidence_id=evidence_id,
    )
    _ensure_retrievable(evidence)

    expires_in = _positive_grant_seconds(grant_seconds)
    token = _serializer(secret_key).dumps(
        {
            "evidence_id": evidence.id,
            "user_id": user_id,
            "nonce": secrets.token_urlsafe(16),
        }
    )
    return EvidenceRetrievalGrant(
        evidence_id=evidence.id,
        token=token,
        expires_in_seconds=expires_in,
    )


def retrieve_private_content(
    *,
    user_id: int,
    evidence_id: int,
    token: str,
    secret_key: object,
    grant_seconds: object,
    storage_provider: EvidenceStorageProvider | None = None,
    storage_config: Mapping[str, object] | None = None,
) -> EvidencePrivateContent:
    expires_in = _positive_grant_seconds(grant_seconds)
    try:
        payload = _serializer(secret_key).loads(token, max_age=expires_in)
    except (BadSignature, SignatureExpired) as exc:
        raise EvidenceRetrievalAccessError("Evidence retrieval grant is invalid or expired.") from exc

    if (
        not isinstance(payload, dict)
        or payload.get("evidence_id") != evidence_id
        or payload.get("user_id") != user_id
    ):
        raise EvidenceRetrievalAccessError("Evidence retrieval grant is invalid or expired.")

    evidence, authority = _resolve_visible_evidence(
        user_id=user_id,
        evidence_id=evidence_id,
    )
    _ensure_retrievable(evidence)

    provider = _provider(
        storage_provider=storage_provider,
        storage_config=storage_config,
    )
    if provider.provider_name != evidence.storage_provider:
        raise EvidenceRetrievalConfigurationError(
            "Evidence storage provider does not match the recorded object."
        )

    try:
        retrieved = provider.get_bytes(
            object_key=evidence.object_key,
            max_bytes=evidence.byte_size,
        )
    except EvidenceStorageError as exc:
        logger.warning(
            "evidence_private_read_failed evidence_id=%s car_id=%s provider=%s",
            evidence.id,
            evidence.car_id,
            provider.provider_name,
        )
        raise EvidenceNotAvailableError(
            "Evidence object could not be retrieved privately."
        ) from exc

    digest = hashlib.sha256(retrieved.payload).hexdigest()
    if retrieved.byte_size != evidence.byte_size or digest != evidence.sha256:
        try:
            evidence.storage_state = "failed"
            evidence.storage_failure_reason_code = "retrieval_integrity_mismatch"
            evidence.updated_at = _utcnow_naive()
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception(
                "evidence_integrity_failure_state_persist_failed evidence_id=%s",
                evidence.id,
            )
        raise EvidenceNotAvailableError("Evidence integrity verification failed.")

    logger.info(
        "evidence_private_retrieved evidence_id=%s car_id=%s user_id=%s authority=%s review_status=%s byte_size=%s",
        evidence.id,
        evidence.car_id,
        user_id,
        authority,
        evidence.review_status,
        retrieved.byte_size,
    )

    return EvidencePrivateContent(
        evidence_id=evidence.id,
        payload=retrieved.payload,
        content_type=evidence.content_type,
        download_name=evidence.safe_display_name,
        byte_size=retrieved.byte_size,
        review_status=evidence.review_status,
        unreviewed=evidence.review_status == "pending_review",
    )


def delete_evidence(
    *,
    user_id: int,
    evidence_id: int,
    reason_code: str,
    storage_provider: EvidenceStorageProvider | None = None,
    storage_config: Mapping[str, object] | None = None,
) -> EvidenceDeletionResult:
    evidence, authority = _resolve_visible_evidence(
        user_id=user_id,
        evidence_id=evidence_id,
    )
    if authority not in {"advisor", "administrator"}:
        raise EvidenceRetrievalAccessError(
            "Only advisor operations may complete evidence deletion in this release."
        )

    normalized_reason = (reason_code or "").strip().lower()
    if normalized_reason not in _ALLOWED_DELETE_REASONS:
        raise EvidenceDeletionConflict("Select an approved evidence deletion reason.")

    if evidence.review_status == "accepted":
        raise EvidenceDeletionConflict(
            "Accepted professional evidence requires a governed review before deletion."
        )
    if EvidenceLink.query.filter_by(evidence_id=evidence.id).first() is not None:
        raise EvidenceDeletionConflict(
            "Linked evidence requires governed unlink/review before deletion."
        )

    provider = _provider(
        storage_provider=storage_provider,
        storage_config=storage_config,
    )
    if provider.provider_name != evidence.storage_provider:
        raise EvidenceRetrievalConfigurationError(
            "Evidence storage provider does not match the recorded object."
        )

    if evidence.review_status == "deleted" and evidence.storage_state == "deleted":
        return EvidenceDeletionResult(
            evidence_id=evidence.id,
            review_status="deleted",
            storage_state="deleted",
            storage_delete_pending=False,
        )

    now = _utcnow_naive()
    evidence.review_status = "deleted"
    evidence.deleted_at = evidence.deleted_at or now
    evidence.reviewed_by_user_id = user_id
    evidence.reviewed_at = now
    evidence.review_reason_code = normalized_reason
    evidence.storage_state = "delete_pending"
    evidence.storage_failure_reason_code = None
    evidence.updated_at = now
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise EvidenceRetrievalError("Aura could not record evidence deletion intent.") from exc

    try:
        provider.delete(object_key=evidence.object_key)
    except EvidenceStorageError:
        logger.warning(
            "evidence_delete_pending evidence_id=%s car_id=%s provider=%s reason_code=%s",
            evidence.id,
            evidence.car_id,
            provider.provider_name,
            normalized_reason,
        )
        return EvidenceDeletionResult(
            evidence_id=evidence.id,
            review_status="deleted",
            storage_state="delete_pending",
            storage_delete_pending=True,
        )

    evidence.storage_state = "deleted"
    evidence.storage_failure_reason_code = None
    evidence.updated_at = _utcnow_naive()
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "evidence_delete_finalization_failed evidence_id=%s car_id=%s",
            evidence.id,
            evidence.car_id,
        )
        return EvidenceDeletionResult(
            evidence_id=evidence.id,
            review_status="deleted",
            storage_state="delete_pending",
            storage_delete_pending=True,
        )

    logger.info(
        "evidence_deleted evidence_id=%s car_id=%s reviewer_id=%s reason_code=%s",
        evidence.id,
        evidence.car_id,
        user_id,
        normalized_reason,
    )
    return EvidenceDeletionResult(
        evidence_id=evidence.id,
        review_status="deleted",
        storage_state="deleted",
        storage_delete_pending=False,
    )


def reconcile_evidence_storage(
    *,
    storage_provider: EvidenceStorageProvider | None = None,
    storage_config: Mapping[str, object] | None = None,
    limit: int = 200,
) -> EvidenceReconciliationSummary:
    """Repair deletion/orphan/missing-object states without exposing media."""

    provider = _provider(
        storage_provider=storage_provider,
        storage_config=storage_config,
    )
    bounded_limit = max(1, min(int(limit), 1000))
    rows = (
        VehicleEvidence.query.filter(
            VehicleEvidence.storage_provider == provider.provider_name,
            VehicleEvidence.storage_state.in_(["available", "delete_pending", "failed"]),
        )
        .order_by(VehicleEvidence.updated_at.asc(), VehicleEvidence.id.asc())
        .limit(bounded_limit)
        .all()
    )

    repaired = 0
    pending = 0
    failed = 0

    for evidence in rows:
        try:
            if evidence.storage_state == "delete_pending":
                if provider.exists(object_key=evidence.object_key):
                    provider.delete(object_key=evidence.object_key)
                evidence.storage_state = "deleted"
                evidence.storage_failure_reason_code = None
                evidence.updated_at = _utcnow_naive()
                db.session.commit()
                repaired += 1
                continue

            if (
                evidence.storage_state == "failed"
                and evidence.storage_failure_reason_code
                == "finalization_failed_orphan_risk"
            ):
                if provider.exists(object_key=evidence.object_key):
                    provider.delete(object_key=evidence.object_key)
                evidence.storage_failure_reason_code = "orphan_cleanup_completed"
                evidence.updated_at = _utcnow_naive()
                db.session.commit()
                repaired += 1
                continue

            if evidence.storage_state == "available" and not provider.exists(
                object_key=evidence.object_key
            ):
                evidence.storage_state = "failed"
                evidence.storage_failure_reason_code = "missing_object"
                evidence.updated_at = _utcnow_naive()
                db.session.commit()
                repaired += 1
                continue

            pending += 1
        except (EvidenceStorageError, SQLAlchemyError):
            db.session.rollback()
            failed += 1
            logger.exception(
                "evidence_storage_reconciliation_failed evidence_id=%s state=%s",
                evidence.id,
                evidence.storage_state,
            )

    return EvidenceReconciliationSummary(
        examined=len(rows),
        repaired=repaired,
        pending=pending,
        failed=failed,
    )
