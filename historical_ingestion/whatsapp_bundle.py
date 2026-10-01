"""Safe WhatsApp ZIP intake and resumable multimodal historical analysis."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import hashlib
from pathlib import PurePosixPath
import re
import stat
import uuid
import wave
import zipfile
from typing import Any, Mapping

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

from evidence.image_sanitizer import EvidenceImageValidationError
from evidence.models import EvidenceBundleItem, EvidenceExtraction, VehicleEvidence
from evidence.storage import (
    EvidenceStorageConfigurationError,
    EvidenceStorageError,
    EvidenceStorageProvider,
    build_evidence_storage_provider,
)
from extensions import db
from historical_ingestion.models import HistoricalServiceEpisode
from historical_ingestion.service import (
    HistoricalDocumentValidationError,
    HistoricalIngestionAccessError,
    HistoricalIngestionConfigurationError,
    HistoricalIngestionError,
    _authority,
    _extract_pdf_text,
    _normalise_provider_payload,
    _payload_cipher,
    _retention_deadline,
    _trusted_vehicle_context,
    _utcnow_naive,
    decrypt_extraction_payload,
)
from historical_ingestion.whatsapp_bundle_analyzer import WhatsAppBundleAdvisorAnalyzer
from models import Car, CarOwnership, TreatmentPlan, VehicleEvent
from treatment.models import TreatmentAction
from rina.providers.base import RinaProviderError, RinaProviderTransientError


MAX_ARCHIVE_BYTES = 150 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 800
MAX_TOTAL_UNCOMPRESSED_BYTES = 750 * 1024 * 1024
MAX_MEMBER_BYTES = 100 * 1024 * 1024
MAX_TEXT_BYTES = 12 * 1024 * 1024
MAX_CORPUS_CHARS = 3_000_000
MAX_BUNDLE_IMAGE_BYTES = 15 * 1024 * 1024
MAX_BUNDLE_IMAGE_OUTPUT_BYTES = 8 * 1024 * 1024
PIPELINE = "whatsapp_bundle_v1"

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_PDF_EXTENSIONS = {".pdf"}
_AUDIO_EXTENSIONS = {".opus", ".ogg", ".mp3", ".m4a", ".wav", ".aac", ".webm"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".3gp"}
_TEXT_EXTENSIONS = {".txt"}
_ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"}
_EXECUTABLE_EXTENSIONS = {
    ".exe", ".dll", ".bat", ".cmd", ".com", ".msi", ".sh", ".ps1", ".js", ".jar",
}

_AUDIO_MIME = {
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".webm": "audio/webm",
}
_VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".3gp": "video/3gpp",
}


@dataclass(frozen=True)
class WhatsAppBundleStartResult:
    evidence_id: int
    extraction_id: int
    status: str
    phase: str
    reused_existing: bool = False


@dataclass(frozen=True)
class WhatsAppBundleStatus:
    evidence_id: int
    extraction_id: int
    status: str
    phase: str
    message: str
    completed_items: int = 0
    total_items: int = 0
    review_ready: bool = False


class WhatsAppBundleValidationError(HistoricalIngestionError):
    pass


def _provider(
    storage_provider: EvidenceStorageProvider | None,
    storage_config: Mapping[str, object],
) -> EvidenceStorageProvider:
    if storage_provider is not None:
        return storage_provider
    try:
        return build_evidence_storage_provider(storage_config)
    except EvidenceStorageConfigurationError as exc:
        raise HistoricalIngestionConfigurationError(
            "Private evidence storage is not configured."
        ) from exc


def _read_archive(file_stream) -> bytes:
    payload = file_stream.read(MAX_ARCHIVE_BYTES + 1)
    if not payload:
        raise WhatsAppBundleValidationError("Select a non-empty WhatsApp ZIP export.")
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise WhatsAppBundleValidationError(
            "WhatsApp ZIP exports are currently limited to 150 MB."
        )
    if not payload.startswith(b"PK"):
        raise WhatsAppBundleValidationError("The uploaded file is not a ZIP archive.")
    return payload


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
    ):
        raise WhatsAppBundleValidationError("The ZIP contains an unsafe member path.")
    return normalized


def _member_kind(name: str) -> str | None:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in _TEXT_EXTENSIONS:
        return "transcript"
    if suffix in _IMAGE_EXTENSIONS:
        return "image"
    if suffix in _PDF_EXTENSIONS:
        return "document"
    if suffix in _AUDIO_EXTENSIONS:
        return "audio"
    if suffix in _VIDEO_EXTENSIONS:
        return "video"
    if suffix in _ARCHIVE_EXTENSIONS or suffix in _EXECUTABLE_EXTENSIONS:
        return "blocked"
    return None


def _validate_zip(payload: bytes) -> list[dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(BytesIO(payload))
    except (zipfile.BadZipFile, OSError) as exc:
        raise WhatsAppBundleValidationError("Aura could not safely read this ZIP.") from exc

    members = [info for info in archive.infolist() if not info.is_dir()]
    if not members:
        raise WhatsAppBundleValidationError("The ZIP contains no files.")
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise WhatsAppBundleValidationError(
            f"The ZIP contains more than {MAX_ARCHIVE_MEMBERS} files."
        )

    total = 0
    manifest: list[dict[str, Any]] = []
    transcript_count = 0
    for index, info in enumerate(members):
        safe_name = _safe_member_name(info.filename)
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if unix_mode and stat.S_ISLNK(unix_mode):
            raise WhatsAppBundleValidationError("Symbolic links inside ZIPs are not supported.")
        if info.flag_bits & 0x1:
            raise WhatsAppBundleValidationError("Encrypted ZIP members are not supported.")
        if info.file_size <= 0:
            continue
        if info.file_size > MAX_MEMBER_BYTES:
            raise WhatsAppBundleValidationError(
                "One file inside the ZIP exceeds Aura's 100 MB member limit."
            )
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise WhatsAppBundleValidationError(
                "The ZIP expands beyond Aura's safe 750 MB bundle limit."
            )
        if info.compress_size > 0 and info.file_size > 5 * 1024 * 1024:
            ratio = info.file_size / info.compress_size
            if ratio > 80:
                raise WhatsAppBundleValidationError(
                    "The ZIP contains an unsafe compression ratio."
                )

        kind = _member_kind(safe_name)
        if kind == "blocked":
            raise WhatsAppBundleValidationError(
                "Nested archives or executable/script files are not accepted."
            )
        if kind == "transcript":
            transcript_count += 1

        manifest.append(
            {
                "member_index": index,
                "original_name": safe_name,
                "kind": kind or "unsupported",
                "byte_size": info.file_size,
                "compressed_size": info.compress_size,
                "crc": info.CRC,
            }
        )

    if transcript_count == 0:
        raise WhatsAppBundleValidationError(
            "No WhatsApp text transcript (.txt) was found in the ZIP."
        )
    return manifest



def _sanitize_bundle_image(payload: bytes) -> tuple[bytes, str, str]:
    if not payload or len(payload) > MAX_BUNDLE_IMAGE_BYTES:
        raise EvidenceImageValidationError(
            "Image inside WhatsApp bundle exceeds Aura's safe image limit."
        )
    try:
        with Image.open(BytesIO(payload)) as probe:
            detected = str(probe.format or "").upper()
            probe.verify()
        with Image.open(BytesIO(payload)) as image:
            image.load()
            if detected not in {"JPEG", "PNG", "WEBP"}:
                raise EvidenceImageValidationError(
                    "Only JPEG, PNG and WebP images are supported in WhatsApp bundles."
                )
            if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
                raise EvidenceImageValidationError(
                    "Animated images are not supported in WhatsApp bundles."
                )
            width, height = image.size
            if (
                width <= 0
                or height <= 0
                or width > 10000
                or height > 10000
                or width * height > 25_000_000
            ):
                raise EvidenceImageValidationError(
                    "Image dimensions exceed Aura's safety limit."
                )
            transposed = ImageOps.exif_transpose(image)
            if detected == "JPEG":
                normalized = transposed.convert("RGB")
                content_type, extension, format_name = "image/jpeg", ".jpg", "JPEG"
                save_kwargs = {"quality": 88, "optimize": True}
            elif detected == "PNG":
                has_alpha = transposed.mode in {"RGBA", "LA"} or (
                    transposed.mode == "P" and "transparency" in transposed.info
                )
                normalized = transposed.convert("RGBA" if has_alpha else "RGB")
                content_type, extension, format_name = "image/png", ".png", "PNG"
                save_kwargs = {"optimize": True}
            else:
                has_alpha = transposed.mode in {"RGBA", "LA"} or (
                    transposed.mode == "P" and "transparency" in transposed.info
                )
                normalized = transposed.convert("RGBA" if has_alpha else "RGB")
                content_type, extension, format_name = "image/webp", ".webp", "WEBP"
                save_kwargs = {"quality": 88, "method": 4}
            try:
                output = BytesIO()
                normalized.save(output, format=format_name, **save_kwargs)
                sanitized = output.getvalue()
            finally:
                normalized.close()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise EvidenceImageValidationError(
            "Aura could not safely decode an image in this WhatsApp bundle."
        ) from exc

    if not sanitized or len(sanitized) > MAX_BUNDLE_IMAGE_OUTPUT_BYTES:
        raise EvidenceImageValidationError(
            "Sanitized WhatsApp image exceeds Aura's safe storage limit."
        )
    return sanitized, content_type, extension


def _validate_media_signature(payload: bytes, suffix: str, kind: str) -> None:
    if not payload:
        raise WhatsAppBundleValidationError("A media file inside the ZIP is empty.")

    if kind == "audio":
        valid = (
            (suffix in {".ogg", ".opus"} and payload.startswith(b"OggS"))
            or (suffix == ".wav" and payload.startswith(b"RIFF") and payload[8:12] == b"WAVE")
            or (suffix == ".mp3" and (payload.startswith(b"ID3") or payload[:1] == b"\xff"))
            or (suffix in {".m4a", ".aac"} and len(payload) > 12 and b"ftyp" in payload[:16])
            or (suffix == ".webm" and payload.startswith(b"\x1aE\xdf\xa3"))
        )
        if not valid:
            raise WhatsAppBundleValidationError(
                "An audio member does not match its expected media format."
            )
    elif kind == "video":
        valid = (
            (suffix in {".mp4", ".mov", ".m4v", ".3gp"} and len(payload) > 12 and b"ftyp" in payload[:16])
        )
        if not valid:
            raise WhatsAppBundleValidationError(
                "A video member does not match its expected media format."
            )


def _object_key(extension: str) -> tuple[str, str]:
    token = uuid.uuid4().hex
    return (
        f"evidence/{token[:2]}/{token}{extension}",
        f"whatsapp-evidence-{token[:12]}{extension}",
    )


def _store_evidence_bytes(
    *,
    user_id: int,
    car_id: int,
    payload: bytes,
    evidence_type: str,
    content_type: str,
    extension: str,
    purpose: str,
    retention_until: datetime | None,
    storage_provider: EvidenceStorageProvider,
    source_channel: str = "whatsapp",
) -> VehicleEvidence:
    object_key, display_name = _object_key(extension)
    now = _utcnow_naive()
    row = VehicleEvidence(
        car_id=car_id,
        uploaded_by_user_id=user_id,
        evidence_type=evidence_type,
        purpose=purpose,
        source_channel=source_channel,
        historical_source_type="whatsapp_conversation",
        visibility="advisor",
        review_status="pending_review",
        storage_provider=storage_provider.provider_name,
        storage_state="pending",
        object_key=object_key,
        safe_display_name=display_name,
        content_type=content_type,
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        uploaded_at=now,
        consent_basis="advisor_whatsapp_case_import",
        lawful_purpose="vehicle_care_recordkeeping",
        retention_until=retention_until,
    )
    db.session.add(row)
    db.session.commit()

    try:
        stored = storage_provider.put_bytes(
            object_key=object_key,
            payload=payload,
            content_type=content_type,
        )
        if stored.object_key != object_key or stored.byte_size != len(payload):
            raise EvidenceStorageError("Private storage confirmation did not match.")
    except EvidenceStorageError as exc:
        row.storage_state = "failed"
        row.storage_failure_reason_code = "bundle_child_write_failed"
        db.session.commit()
        raise HistoricalIngestionError(
            "Aura could not securely store a file from the WhatsApp bundle."
        ) from exc

    row.storage_state = "available"
    row.storage_failure_reason_code = None
    db.session.commit()
    return row


def _archive_evidence(
    *,
    user_id: int,
    car_id: int,
    payload: bytes,
    purpose: str,
    retention_days: object,
    storage_provider: EvidenceStorageProvider,
) -> VehicleEvidence:
    source_sha = hashlib.sha256(payload).hexdigest()
    existing = (
        VehicleEvidence.query.filter_by(
            car_id=car_id,
            evidence_type="archive",
            sha256=source_sha,
            storage_state="available",
        )
        .filter(VehicleEvidence.deleted_at.is_(None))
        .order_by(VehicleEvidence.id.desc())
        .first()
    )
    if existing is not None:
        return existing

    return _store_evidence_bytes(
        user_id=user_id,
        car_id=car_id,
        payload=payload,
        evidence_type="archive",
        content_type="application/zip",
        extension=".zip",
        purpose=purpose,
        retention_until=_retention_deadline(retention_days),
        storage_provider=storage_provider,
    )


def latest_whatsapp_bundle_extraction(evidence_id: int) -> EvidenceExtraction | None:
    rows = (
        EvidenceExtraction.query.filter_by(
            evidence_id=evidence_id,
            extraction_type="structured_fields",
        )
        .order_by(EvidenceExtraction.id.desc())
        .limit(30)
        .all()
    )
    for row in rows:
        if (row.provenance or {}).get("analysis_pipeline") == PIPELINE:
            return row
    return None


def _manifest_extraction(evidence_id: int) -> EvidenceExtraction | None:
    return (
        EvidenceExtraction.query.filter_by(
            evidence_id=evidence_id,
            extraction_type="archive_manifest",
            status="completed",
        )
        .order_by(EvidenceExtraction.id.desc())
        .first()
    )


def _create_processing_extraction(
    *,
    evidence_id: int,
    actor_user_id: int,
    stage: str,
) -> EvidenceExtraction:
    row = EvidenceExtraction(
        evidence_id=evidence_id,
        extraction_type="structured_fields",
        provider="openai",
        provider_model=None,
        status="processing",
        review_status="unreviewed",
        provenance={
            "analysis_pipeline": PIPELINE,
            "background_stage": stage,
            "started_by_user_id": actor_user_id,
            "semantic_authority": "candidate_only",
            "schema_version": 1,
        },
    )
    db.session.add(row)
    db.session.commit()
    return row


def ingest_whatsapp_bundle(
    *,
    user_id: int,
    car_id: int,
    file_stream,
    purpose: str,
    retention_days: object,
    storage_provider: EvidenceStorageProvider | None = None,
    storage_config: Mapping[str, object] | None = None,
) -> WhatsAppBundleStartResult:
    car = db.session.get(Car, car_id)
    if car is None:
        raise HistoricalIngestionAccessError("Vehicle was not found.")
    _authority(user_id, car_id)

    purpose = (purpose or "service_document").strip().lower()
    if purpose not in {
        "service_document",
        "diagnostic_document",
        "treatment_evidence",
        "vehicle_history_context",
    }:
        raise WhatsAppBundleValidationError("Select a supported historical source purpose.")

    payload = _read_archive(file_stream)
    _validate_zip(payload)
    provider = _provider(storage_provider, storage_config or current_app.config)
    evidence = _archive_evidence(
        user_id=user_id,
        car_id=car_id,
        payload=payload,
        purpose=purpose,
        retention_days=retention_days,
        storage_provider=provider,
    )

    existing = latest_whatsapp_bundle_extraction(evidence.id)
    if existing is not None and existing.status in {"processing", "completed"}:
        return WhatsAppBundleStartResult(
            evidence_id=evidence.id,
            extraction_id=existing.id,
            status=existing.status,
            phase=str((existing.provenance or {}).get("background_stage") or "unpacking"),
            reused_existing=True,
        )

    analysis = _create_processing_extraction(
        evidence_id=evidence.id,
        actor_user_id=user_id,
        stage="unpacking",
    )
    return WhatsAppBundleStartResult(
        evidence_id=evidence.id,
        extraction_id=analysis.id,
        status="processing",
        phase="unpacking",
    )


def restart_whatsapp_bundle_analysis(
    *,
    evidence_id: int,
    actor_user_id: int,
) -> WhatsAppBundleStartResult:
    evidence = db.session.get(VehicleEvidence, evidence_id)
    if evidence is None or evidence.evidence_type != "archive":
        raise HistoricalIngestionError("WhatsApp case bundle was not found.")
    _authority(actor_user_id, evidence.car_id)

    current = latest_whatsapp_bundle_extraction(evidence.id)
    if current is not None and current.status == "processing":
        return WhatsAppBundleStartResult(
            evidence_id=evidence.id,
            extraction_id=current.id,
            status="processing",
            phase=str((current.provenance or {}).get("background_stage") or "preprocessing"),
            reused_existing=True,
        )

    stage = "preprocessing" if _manifest_extraction(evidence.id) else "unpacking"
    row = _create_processing_extraction(
        evidence_id=evidence.id,
        actor_user_id=actor_user_id,
        stage=stage,
    )
    return WhatsAppBundleStartResult(
        evidence_id=evidence.id,
        extraction_id=row.id,
        status="processing",
        phase=stage,
    )


def _decode_text(payload: bytes) -> str:
    if len(payload) > MAX_TEXT_BYTES:
        raise WhatsAppBundleValidationError("WhatsApp transcript is too large.")
    for encoding in ("utf-8-sig", "utf-16", "utf-8", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise WhatsAppBundleValidationError("Aura could not decode the WhatsApp transcript.")


_IOS_LINE = re.compile(
    r"^\[(?P<stamp>[^\]]+)\]\s*(?P<sender>[^:]+):\s*(?P<message>.*)$"
)
_ANDROID_LINE = re.compile(
    r"^(?P<stamp>\d{1,2}/\d{1,2}/\d{2,4},\s*.*?)\s+-\s+(?P<sender>[^:]+):\s*(?P<message>.*)$"
)


def _label_whatsapp_transcript(raw: str) -> tuple[str, int]:
    messages: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for raw_line in raw.splitlines():
        line = raw_line.replace("\u200e", "").strip("\ufeff")
        match = _IOS_LINE.match(line) or _ANDROID_LINE.match(line)
        if match:
            if current is not None:
                messages.append(current)
            current = {
                "stamp": match.group("stamp").strip(),
                "sender": match.group("sender").strip(),
                "message": match.group("message").strip(),
            }
        elif current is not None:
            current["message"] = (current["message"] + "\n" + line).strip()

    if current is not None:
        messages.append(current)

    if not messages:
        # Preserve an unparsed export rather than inventing message boundaries.
        return "[CHAT raw-export]\n" + raw[:MAX_CORPUS_CHARS], 0

    chunks: list[str] = []
    for idx, message in enumerate(messages, start=1):
        chunks.append(
            f"[CHAT m{idx:06d} | {message['stamp']} | {message['sender']}] "
            f"{message['message']}"
        )
    return "\n".join(chunks), len(messages)


def _create_encrypted_extraction(
    *,
    evidence_id: int,
    extraction_type: str,
    provider: str,
    payload: dict[str, Any],
    provenance: dict[str, Any],
    provider_model: str | None = None,
) -> EvidenceExtraction:
    cipher, version, digest = _payload_cipher(payload)
    row = EvidenceExtraction(
        evidence_id=evidence_id,
        extraction_type=extraction_type,
        provider=provider,
        provider_model=provider_model,
        status="completed",
        result_ciphertext=cipher,
        result_key_version=version,
        result_sha256=digest,
        provenance=provenance,
        review_status="unreviewed",
        completed_at=_utcnow_naive(),
    )
    db.session.add(row)
    db.session.commit()
    return row


def _extract_member_bytes(archive_payload: bytes, original_name: str) -> bytes:
    try:
        with zipfile.ZipFile(BytesIO(archive_payload)) as archive:
            info = archive.getinfo(original_name)
            if info.file_size > MAX_MEMBER_BYTES:
                raise WhatsAppBundleValidationError("Archive member exceeds safe limit.")
            payload = archive.read(info, pwd=None)
    except (zipfile.BadZipFile, KeyError, RuntimeError, OSError) as exc:
        raise WhatsAppBundleValidationError(
            "Aura could not safely read a file inside this WhatsApp export."
        ) from exc
    if len(payload) != info.file_size:
        raise WhatsAppBundleValidationError("Archive member size did not match its manifest.")
    return payload


def _materialize_bundle_children(
    *,
    evidence: VehicleEvidence,
    actor_user_id: int,
    archive_payload: bytes,
    storage_provider: EvidenceStorageProvider,
) -> EvidenceExtraction:
    existing_manifest = _manifest_extraction(evidence.id)
    if existing_manifest is not None:
        return existing_manifest

    manifest = _validate_zip(archive_payload)
    created: list[dict[str, Any]] = []
    try:
        valid_transcripts = 0
        for item in manifest:
            kind = item["kind"]
            if kind == "unsupported":
                created.append({**item, "child_evidence_id": None, "status": "skipped"})
                continue

            try:
                member_payload = _extract_member_bytes(
                    archive_payload,
                    str(item["original_name"]),
                )
                suffix = PurePosixPath(str(item["original_name"])).suffix.lower()

                if kind == "image":
                    stored_payload, content_type, extension = _sanitize_bundle_image(
                        member_payload
                    )
                    evidence_type = "image"
                elif kind == "document":
                    if not member_payload.startswith(b"%PDF-"):
                        raise WhatsAppBundleValidationError(
                            "A .pdf member did not contain a valid PDF signature."
                        )
                    stored_payload = member_payload
                    content_type = "application/pdf"
                    extension = ".pdf"
                    evidence_type = "document"
                elif kind == "transcript":
                    # The chat transcript is the chronology spine of a WhatsApp export.
                    _decode_text(member_payload)
                    stored_payload = member_payload
                    content_type = "text/plain"
                    extension = ".txt"
                    evidence_type = "document"
                elif kind == "audio":
                    _validate_media_signature(member_payload, suffix, kind)
                    stored_payload = member_payload
                    content_type = _AUDIO_MIME.get(suffix, "application/octet-stream")
                    extension = suffix or ".audio"
                    evidence_type = "audio"
                elif kind == "video":
                    _validate_media_signature(member_payload, suffix, kind)
                    stored_payload = member_payload
                    content_type = _VIDEO_MIME.get(suffix, "application/octet-stream")
                    extension = suffix or ".video"
                    evidence_type = "video"
                else:
                    continue

                child = _store_evidence_bytes(
                    user_id=actor_user_id,
                    car_id=evidence.car_id,
                    payload=stored_payload,
                    evidence_type=evidence_type,
                    content_type=content_type,
                    extension=extension,
                    purpose=evidence.purpose,
                    retention_until=evidence.retention_until,
                    storage_provider=storage_provider,
                )
                lineage = EvidenceBundleItem(
                    bundle_evidence_id=evidence.id,
                    child_evidence_id=child.id,
                    member_index=int(item["member_index"]),
                    member_kind=kind,
                    member_sha256=hashlib.sha256(stored_payload).hexdigest(),
                )
                db.session.add(lineage)
                db.session.commit()
                if kind == "transcript":
                    valid_transcripts += 1
                created.append(
                    {**item, "child_evidence_id": child.id, "status": "materialized"}
                )
            except (
                WhatsAppBundleValidationError,
                EvidenceImageValidationError,
            ) as exc:
                db.session.rollback()
                if kind == "transcript":
                    raise
                created.append(
                    {
                        **item,
                        "child_evidence_id": None,
                        "status": "rejected_unsafe",
                        "reason": type(exc).__name__,
                    }
                )

        if valid_transcripts == 0:
            raise WhatsAppBundleValidationError(
                "Aura could not safely materialize the WhatsApp chat transcript."
            )
    except Exception:
        db.session.rollback()
        raise

    payload = {
        "schema_version": 1,
        "bundle_type": "whatsapp_export",
        "member_count": len(manifest),
        "supported_member_count": sum(
            1 for item in created if item.get("child_evidence_id") is not None
        ),
        "members": created,
    }
    return _create_encrypted_extraction(
        evidence_id=evidence.id,
        extraction_type="archive_manifest",
        provider="aura_zip",
        payload=payload,
        provenance={
            "parser": "zipfile",
            "analysis_pipeline": PIPELINE,
            "semantic_authority": "none",
        },
    )


def _member_source_ref(item: EvidenceBundleItem) -> str:
    kind = item.member_kind.upper()
    name = None
    manifest = _manifest_extraction(item.bundle_evidence_id)
    if manifest is not None:
        payload = decrypt_extraction_payload(manifest)
        members = payload.get("members")
        if isinstance(members, list):
            for row in members:
                if (
                    isinstance(row, dict)
                    and int(row.get("member_index", -1)) == item.member_index
                ):
                    name = str(row.get("original_name") or "").strip() or None
                    break

    base = f"[{kind} evidence:{item.child_evidence_id}"
    if name:
        return f"{base} | member:{name}]"
    return base + "]"


def _child_done(item: EvidenceBundleItem) -> bool:
    required = {
        "transcript": {"document_text"},
        "document": {"document_text"},
        "image": {"image_observation"},
        "audio": {"transcription"},
        "video": {"transcription", "image_observation"},
    }[item.member_kind]
    rows = EvidenceExtraction.query.filter(
        EvidenceExtraction.evidence_id == item.child_evidence_id,
        EvidenceExtraction.extraction_type.in_(required),
        EvidenceExtraction.status.in_({"completed", "failed"}),
    ).all()
    terminal = {row.extraction_type for row in rows}
    return required <= terminal


def _mark_child_failure(
    *,
    evidence_id: int,
    extraction_type: str,
    provider: str,
    exc: Exception,
) -> None:
    row = EvidenceExtraction(
        evidence_id=evidence_id,
        extraction_type=extraction_type,
        provider=provider,
        status="failed",
        review_status="unreviewed",
        provenance={
            "analysis_pipeline": PIPELINE,
            "failure_class": type(exc).__name__,
            "failure_detail": str(exc).replace("\n", " ")[:500],
        },
        completed_at=None,
    )
    db.session.add(row)
    db.session.commit()


def _audio_wav_chunks(payload: bytes) -> list[bytes]:
    """Decode supported WhatsApp audio into bounded transcription-safe WAV chunks."""
    try:
        import av
    except ImportError as exc:
        raise HistoricalIngestionConfigurationError(
            "Audio analysis runtime is not installed."
        ) from exc

    sample_rate = 16_000
    chunk_seconds = 6 * 60
    max_total_seconds = 30 * 60
    bytes_per_sample = 2
    chunk_pcm_limit = sample_rate * chunk_seconds * bytes_per_sample
    total_sample_limit = sample_rate * max_total_seconds

    pcm_chunks: list[bytes] = []
    current = bytearray()
    samples_written = 0

    try:
        with av.open(BytesIO(payload), mode="r") as container:
            if not container.streams.audio:
                raise HistoricalIngestionError(
                    "This WhatsApp audio attachment contains no decodable audio stream."
                )

            resampler = av.AudioResampler(
                format="s16",
                layout="mono",
                rate=sample_rate,
            )

            def append_frame(resampled) -> None:
                nonlocal current, samples_written
                pcm = bytes(resampled.planes[0])[: resampled.samples * bytes_per_sample]
                cursor = 0

                while cursor < len(pcm):
                    remaining_total_samples = total_sample_limit - samples_written
                    if remaining_total_samples <= 0:
                        raise HistoricalIngestionError(
                            "This WhatsApp audio attachment exceeds Aura's current "
                            "30-minute per-file transcription limit."
                        )

                    room = chunk_pcm_limit - len(current)
                    allowed_total_bytes = remaining_total_samples * bytes_per_sample
                    take = min(len(pcm) - cursor, room, allowed_total_bytes)
                    take -= take % bytes_per_sample
                    if take <= 0:
                        continue

                    current.extend(pcm[cursor : cursor + take])
                    cursor += take
                    samples_written += take // bytes_per_sample

                    if len(current) >= chunk_pcm_limit:
                        pcm_chunks.append(bytes(current))
                        current = bytearray()

            for frame in container.decode(audio=0):
                for resampled in resampler.resample(frame):
                    append_frame(resampled)

            for resampled in resampler.resample(None):
                append_frame(resampled)

    except HistoricalIngestionError:
        raise
    except (av.error.FFmpegError, OSError, ValueError) as exc:
        raise HistoricalIngestionError(
            "Aura could not decode this WhatsApp audio attachment."
        ) from exc

    if current:
        pcm_chunks.append(bytes(current))
    if not pcm_chunks:
        raise HistoricalIngestionError(
            "This WhatsApp audio attachment contained no usable speech audio."
        )

    wav_chunks: list[bytes] = []
    for pcm in pcm_chunks:
        output = BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(bytes_per_sample)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm)
        wav_chunks.append(output.getvalue())

    return wav_chunks


def _video_derivatives(payload: bytes, suffix: str) -> tuple[bytes | None, list[bytes]]:
    del suffix  # Media type has already been signature-validated at archive intake.
    try:
        import av
    except ImportError as exc:
        raise HistoricalIngestionConfigurationError(
            "Video analysis runtime is not installed."
        ) from exc

    audio: bytes | None = None
    frames: list[bytes] = []

    try:
        with av.open(BytesIO(payload), mode="r") as container:
            if container.streams.audio:
                output = BytesIO()
                sample_limit = 16_000 * 10 * 60  # At most 10 minutes per video.
                samples_written = 0
                resampler = av.AudioResampler(
                    format="s16",
                    layout="mono",
                    rate=16_000,
                )
                with wave.open(output, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(16_000)
                    for frame in container.decode(audio=0):
                        for resampled in resampler.resample(frame):
                            remaining = sample_limit - samples_written
                            if remaining <= 0:
                                break
                            pcm = bytes(resampled.planes[0])
                            max_bytes = remaining * 2
                            pcm = pcm[:max_bytes]
                            wav.writeframes(pcm)
                            samples_written += len(pcm) // 2
                        if samples_written >= sample_limit:
                            break
                value = output.getvalue()
                if len(value) > 44:
                    audio = value
    except (av.error.FFmpegError, OSError, ValueError):
        audio = None

    try:
        with av.open(BytesIO(payload), mode="r") as container:
            if container.streams.video:
                stream = container.streams.video[0]
                targets = [0.0, 3.0, 8.0]
                target_index = 0

                for frame in container.decode(video=0):
                    if target_index >= len(targets):
                        break
                    when = frame.time
                    if when is None and frame.pts is not None:
                        when = float(frame.pts * stream.time_base)
                    if when is None:
                        when = 0.0

                    if when + 0.001 < targets[target_index]:
                        continue

                    image = frame.to_image()
                    try:
                        image.thumbnail((1280, 1280))
                        if image.mode != "RGB":
                            image = image.convert("RGB")
                        output = BytesIO()
                        image.save(output, format="JPEG", quality=85, optimize=True)
                        value = output.getvalue()
                        if value and len(value) <= 3 * 1024 * 1024:
                            frames.append(value)
                    finally:
                        image.close()
                    target_index += 1
    except (av.error.FFmpegError, OSError, ValueError):
        frames = []

    return audio, frames


def _process_child(
    *,
    item: EvidenceBundleItem,
    storage_provider: EvidenceStorageProvider,
    analyzer: WhatsAppBundleAdvisorAnalyzer,
) -> None:
    child = item.child
    if child is None:
        return
    retrieved = storage_provider.get_bytes(
        object_key=child.object_key,
        max_bytes=MAX_MEMBER_BYTES,
    )
    payload = retrieved.payload
    source_ref = _member_source_ref(item)

    if item.member_kind == "transcript":
        raw = _decode_text(payload)
        labelled, count = _label_whatsapp_transcript(raw)
        _create_encrypted_extraction(
            evidence_id=child.id,
            extraction_type="document_text",
            provider="aura_whatsapp_parser",
            payload={
                "schema_version": 1,
                "text": labelled,
                "message_count": count,
                "format": "whatsapp_export",
            },
            provenance={
                "analysis_pipeline": PIPELINE,
                "semantic_authority": "source_text",
            },
        )
        return

    if item.member_kind == "document":
        try:
            text, pages = _extract_pdf_text(payload)
            labelled = "\n\n".join(
                f"{source_ref} {chunk}"
                for chunk in text.split("\n\n")
            )
            _create_encrypted_extraction(
                evidence_id=child.id,
                extraction_type="document_text",
                provider="pypdf",
                payload={"schema_version": 1, "text": labelled, "page_count": pages},
                provenance={
                    "analysis_pipeline": PIPELINE,
                    "semantic_authority": "source_text",
                },
            )
        except HistoricalDocumentValidationError:
            observation = analyzer.observe_pdf(payload=payload, source_ref=source_ref)
            _create_encrypted_extraction(
                evidence_id=child.id,
                extraction_type="document_text",
                provider="openai",
                provider_model=analyzer.media_model,
                payload={
                    "schema_version": 1,
                    "text": (
                        f"{source_ref} PDF observation: {observation.get('summary', '')}\n"
                        + "\n".join(observation.get("observations") or [])
                        + "\nVisible text: "
                        + str(observation.get("visible_text") or "")
                    ),
                    "page_count": 0,
                },
                provenance={
                    "analysis_pipeline": PIPELINE,
                    "semantic_authority": "candidate_only",
                    "direct_pdf_analysis": True,
                },
            )
        return

    if item.member_kind == "image":
        observation = analyzer.observe_image(
            payload=payload,
            content_type=child.content_type,
            source_ref=source_ref,
        )
        _create_encrypted_extraction(
            evidence_id=child.id,
            extraction_type="image_observation",
            provider="openai",
            provider_model=analyzer.media_model,
            payload={"schema_version": 1, **observation},
            provenance={
                "analysis_pipeline": PIPELINE,
                "semantic_authority": "candidate_only",
            },
        )
        return

    if item.member_kind == "audio":
        wav_chunks = _audio_wav_chunks(payload)
        transcript_parts: list[str] = []
        for chunk_index, wav_payload in enumerate(wav_chunks, start=1):
            text = analyzer.transcribe_audio(
                payload=wav_payload,
                filename=f"whatsapp-audio-{chunk_index:03d}.wav",
                content_type="audio/wav",
            )
            transcript_parts.append(text)

        transcript = "\n".join(
            (
                f"[audio part {index}/{len(transcript_parts)}] {text}"
                if len(transcript_parts) > 1
                else text
            )
            for index, text in enumerate(transcript_parts, start=1)
        )
        _create_encrypted_extraction(
            evidence_id=child.id,
            extraction_type="transcription",
            provider="openai",
            provider_model=analyzer.transcription_model,
            payload={"schema_version": 1, "text": f"{source_ref} {transcript}"},
            provenance={
                "analysis_pipeline": PIPELINE,
                "semantic_authority": "source_transcription",
                "audio_normalization": "pcm_s16_mono_16khz_wav",
                "transcription_chunks": len(wav_chunks),
            },
        )
        return

    if item.member_kind == "video":
        suffix = PurePosixPath(child.safe_display_name).suffix.lower() or ".mp4"
        audio, frames = _video_derivatives(payload, suffix)
        transcript = ""
        if audio:
            try:
                transcript = analyzer.transcribe_audio(
                    payload=audio,
                    filename="video-audio.wav",
                    content_type="audio/wav",
                )
            except Exception as exc:
                _mark_child_failure(
                    evidence_id=child.id,
                    extraction_type="transcription",
                    provider="openai",
                    exc=exc,
                )
        if transcript:
            _create_encrypted_extraction(
                evidence_id=child.id,
                extraction_type="transcription",
                provider="openai",
                provider_model=analyzer.transcription_model,
                payload={"schema_version": 1, "text": f"{source_ref} {transcript}"},
                provenance={
                    "analysis_pipeline": PIPELINE,
                    "semantic_authority": "source_transcription",
                },
            )
        elif not any(
            r.extraction_type == "transcription" for r in child.extractions
        ):
            _create_encrypted_extraction(
                evidence_id=child.id,
                extraction_type="transcription",
                provider="aura_video",
                payload={"schema_version": 1, "text": f"{source_ref} No usable audio transcript."},
                provenance={
                    "analysis_pipeline": PIPELINE,
                    "semantic_authority": "none",
                },
            )

        observation = analyzer.observe_video_frames(
            frames=frames,
            transcript=transcript,
            source_ref=source_ref,
        )
        _create_encrypted_extraction(
            evidence_id=child.id,
            extraction_type="image_observation",
            provider="openai",
            provider_model=analyzer.media_model,
            payload={"schema_version": 1, **observation},
            provenance={
                "analysis_pipeline": PIPELINE,
                "semantic_authority": "candidate_only",
                "representative_frames": len(frames),
            },
        )


def _iso(value: object) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _historical_intelligence_context(car: Car) -> dict[str, Any]:
    """Trusted Aura context used only to disambiguate and diff source history."""

    base = _trusted_vehicle_context(car)
    active_ownership = (
        CarOwnership.query.filter_by(car_id=car.id, is_active=True)
        .order_by(CarOwnership.id.desc())
        .first()
    )

    ownerships = []
    if active_ownership is not None:
        ownerships = (
            CarOwnership.query.filter_by(
                user_id=active_ownership.user_id,
                is_active=True,
            )
            .order_by(CarOwnership.start_date.desc(), CarOwnership.id.desc())
            .limit(24)
            .all()
        )

    if not ownerships:
        ownerships = [active_ownership] if active_ownership is not None else []

    known_vehicles: list[dict[str, Any]] = []
    known_car_ids: list[int] = []
    for ownership in ownerships:
        if ownership is None or ownership.car is None:
            continue
        known_car = ownership.car
        known_car_ids.append(known_car.id)
        known_vehicles.append(
            {
                "car_id": known_car.id,
                "display_name": known_car.rina_display_name,
                "vin": (known_car.vin or "").strip().upper() or None,
                "plate_number": (ownership.plate_number or "").strip() or None,
                "is_selected_vehicle": known_car.id == car.id,
            }
        )

    if car.id not in known_car_ids:
        known_car_ids.insert(0, car.id)
        known_vehicles.insert(
            0,
            {
                "car_id": car.id,
                "display_name": car.rina_display_name,
                "vin": (car.vin or "").strip().upper() or None,
                "plate_number": (
                    (active_ownership.plate_number or "").strip()
                    if active_ownership is not None
                    else None
                ),
                "is_selected_vehicle": True,
            },
        )

    actions = (
        TreatmentAction.query.filter(TreatmentAction.car_id.in_(known_car_ids))
        .order_by(TreatmentAction.completed_at.desc(), TreatmentAction.id.desc())
        .limit(240)
        .all()
    )
    plans_by_id = {
        row.id: row
        for row in TreatmentPlan.query.filter(
            TreatmentPlan.id.in_(
                sorted({action.treatment_plan_id for action in actions})
            )
        ).all()
    } if actions else {}

    canonical_actions = []
    for action in actions:
        plan = plans_by_id.get(action.treatment_plan_id)
        canonical_actions.append(
            {
                "treatment_action_id": action.id,
                "treatment_plan_id": action.treatment_plan_id,
                "car_id": action.car_id,
                "plan_title": plan.title if plan is not None else None,
                "plan_record_origin": plan.record_origin if plan is not None else None,
                "title": action.title,
                "status": action.status,
                "completed_at": _iso(action.completed_at),
                "created_at": _iso(action.created_at),
            }
        )

    historical_episodes = [
        {
            "historical_episode_id": episode.id,
            "car_id": episode.car_id,
            "title": episode.title,
            "job_reference": episode.job_reference,
            "episode_date": _iso(episode.episode_date),
            "status": episode.status,
        }
        for episode in (
            HistoricalServiceEpisode.query.filter(
                HistoricalServiceEpisode.car_id.in_(known_car_ids),
                HistoricalServiceEpisode.status == "active",
            )
            .order_by(
                HistoricalServiceEpisode.episode_date.desc(),
                HistoricalServiceEpisode.id.desc(),
            )
            .limit(120)
            .all()
        )
    ]

    vehicle_events = [
        {
            "vehicle_event_id": event.id,
            "car_id": event.car_id,
            "event_type": event.event_type,
            "title": event.title,
            "description": str(event.description or "")[:500] or None,
            "event_date": _iso(event.event_date),
            "occurred_at": _iso(event.occurred_at),
            "source": event.source,
        }
        for event in (
            VehicleEvent.query.filter(VehicleEvent.car_id.in_(known_car_ids))
            .order_by(VehicleEvent.occurred_at.desc(), VehicleEvent.id.desc())
            .limit(180)
            .all()
        )
    ]

    return {
        **base,
        "historical_intelligence_contract": "multi_vehicle_episode_diff_v2",
        "selected_vehicle_car_id": car.id,
        "known_client_vehicles": known_vehicles,
        "canonical_history": {
            "treatment_actions": canonical_actions,
            "historical_service_episodes": historical_episodes,
            "vehicle_events": vehicle_events,
        },
        "canonical_rule": (
            "Do not call source-supported work missing when an equivalent durable "
            "Treatment Action, historical episode, or vehicle event already represents it."
        ),
    }


def _bundle_source_coverage(
    evidence: VehicleEvidence,
    *,
    corpus: str,
) -> dict[str, Any]:
    """Deterministic proof of how much of an imported bundle Aura processed."""

    items = (
        EvidenceBundleItem.query.filter_by(bundle_evidence_id=evidence.id)
        .order_by(EvidenceBundleItem.member_index.asc())
        .all()
    )
    expected_by_kind = Counter(str(item.member_kind or "unknown") for item in items)
    completed_by_kind: Counter[str] = Counter()
    failed_by_kind: Counter[str] = Counter()
    failed_items: list[dict[str, Any]] = []

    required_by_kind = {
        "transcript": {"document_text"},
        "document": {"document_text"},
        "image": {"image_observation"},
        "audio": {"transcription"},
        "video": {"transcription", "image_observation"},
    }

    for item in items:
        child = item.child
        kind = str(item.member_kind or "unknown")
        if child is None or kind not in required_by_kind:
            failed_by_kind[kind] += 1
            failed_items.append(
                {
                    "member_kind": kind,
                    "evidence_id": item.child_evidence_id,
                    "safe_display_name": None,
                    "failed_extraction_types": ["materialization"],
                }
            )
            continue

        required = required_by_kind[kind]
        rows = EvidenceExtraction.query.filter(
            EvidenceExtraction.evidence_id == child.id,
            EvidenceExtraction.extraction_type.in_(required),
        ).all()
        statuses = {
            extraction_type: {
                row.status
                for row in rows
                if row.extraction_type == extraction_type
            }
            for extraction_type in required
        }
        successful = {
            extraction_type
            for extraction_type, values in statuses.items()
            if "completed" in values
        }
        failed = sorted(
            extraction_type
            for extraction_type, values in statuses.items()
            if "completed" not in values and "failed" in values
        )

        if required <= successful:
            completed_by_kind[kind] += 1
        else:
            failed_by_kind[kind] += 1
            failed_items.append(
                {
                    "member_kind": kind,
                    "evidence_id": child.id,
                    "safe_display_name": child.safe_display_name,
                    "failed_extraction_types": failed
                    or sorted(required - successful),
                }
            )

    chat_message_count = len(
        re.findall(r"(?m)^\[CHAT m\d{6}\b", corpus)
    )
    total_items = len(items)
    completed_items = sum(completed_by_kind.values())
    failed_count = len(failed_items)

    archive_member_count = total_items
    unsupported_or_skipped_count = 0
    rejected_unsafe_count = 0
    non_materialized_members: list[dict[str, Any]] = []
    manifest = _manifest_extraction(evidence.id)
    if manifest is not None:
        try:
            manifest_payload = decrypt_extraction_payload(manifest)
        except HistoricalIngestionError:
            manifest_payload = {}
        archive_member_count = int(
            manifest_payload.get("member_count") or total_items
        )
        for member in manifest_payload.get("members") or []:
            if not isinstance(member, dict):
                continue
            status = str(member.get("status") or "").strip().lower()
            if status == "skipped":
                unsupported_or_skipped_count += 1
            elif status == "rejected_unsafe":
                rejected_unsafe_count += 1
            else:
                continue
            non_materialized_members.append(
                {
                    "member_kind": str(member.get("kind") or "unknown")[:40],
                    "safe_display_name": str(
                        member.get("safe_name")
                        or member.get("original_name")
                        or "archive member"
                    )[:180],
                    "status": status,
                    "reason": str(member.get("reason") or "")[:120] or None,
                }
            )

    supported_media_complete = bool(
        total_items and completed_items == total_items and failed_count == 0
    )
    coverage_complete = bool(
        supported_media_complete
        and rejected_unsafe_count == 0
        and unsupported_or_skipped_count == 0
    )

    return {
        "coverage_version": 2,
        "chat_message_count": chat_message_count,
        "archive_member_count": archive_member_count,
        "bundle_item_count": total_items,
        "materialized_supported_item_count": total_items,
        "expected_by_kind": dict(expected_by_kind),
        "completed_by_kind": dict(completed_by_kind),
        "failed_by_kind": dict(failed_by_kind),
        "completed_item_count": completed_items,
        "failed_item_count": failed_count,
        "unsupported_or_skipped_count": unsupported_or_skipped_count,
        "rejected_unsafe_count": rejected_unsafe_count,
        "supported_media_complete": supported_media_complete,
        "coverage_complete": coverage_complete,
        "failed_items": failed_items[:40],
        "non_materialized_members": non_materialized_members[:40],
        "claim": (
            "complete_supported_evidence"
            if coverage_complete
            else "partial"
        ),
    }


_CANONICAL_MATCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "authorised",
    "authorized",
    "complete",
    "completed",
    "completion",
    "done",
    "for",
    "in",
    "install",
    "installed",
    "installation",
    "of",
    "on",
    "plan",
    "planned",
    "rebuild",
    "rebuilt",
    "reconstruct",
    "reconstructed",
    "reconstruction",
    "repair",
    "repaired",
    "replacement",
    "replace",
    "replaced",
    "service",
    "serviced",
    "the",
    "to",
    "was",
    "work",
}

_LOCATION_TOKENS = {
    "front",
    "rear",
    "left",
    "right",
    "upper",
    "lower",
    "top",
    "bottom",
    "inner",
    "outer",
}


def _canonical_match_tokens(value: object) -> set[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", str(value or "").lower()):
        token = raw
        if token.endswith("ies") and len(token) > 4:
            token = token[:-3] + "y"
        elif token.endswith("s") and len(token) > 4 and not token.endswith("ss"):
            token = token[:-1]
        if token in _CANONICAL_MATCH_STOPWORDS or len(token) < 2:
            continue
        tokens.add(token)
    return tokens


def _semantically_equivalent_action(left: object, right: object) -> bool:
    """Conservative deterministic backstop for obvious canonical duplicates."""

    left_tokens = _canonical_match_tokens(left)
    right_tokens = _canonical_match_tokens(right)
    if not left_tokens or not right_tokens:
        return False

    left_locations = left_tokens & _LOCATION_TOKENS
    right_locations = right_tokens & _LOCATION_TOKENS
    if left_locations and right_locations and left_locations != right_locations:
        return False

    left_core = left_tokens - _LOCATION_TOKENS
    right_core = right_tokens - _LOCATION_TOKENS
    if not left_core or not right_core:
        return False

    overlap = left_core & right_core
    smaller = min(len(left_core), len(right_core))
    if smaller <= 0:
        return False

    # Require at least two meaningful shared terms except for a very distinctive
    # exact one-token component name. This keeps the server guard conservative;
    # the reasoning model still performs the broader semantic comparison.
    if len(overlap) >= 2 and len(overlap) / smaller >= 0.75:
        return True

    distinctive_singletons = {
        "alternator",
        "battery",
        "compressor",
        "radiator",
        "starter",
        "valve",
    }
    return (
        len(left_core) == 1
        and len(right_core) == 1
        and next(iter(left_core)) in distinctive_singletons
        and left_core == right_core
    )


def _canonical_action_matches_for_episode(
    episode: dict[str, Any],
    *,
    car_id: int | None,
    canonical_actions: list[dict[str, Any]],
) -> tuple[list[int], list[str], list[str]]:
    interventions = [
        str(value).strip()
        for value in (episode.get("completed_interventions") or [])
        if str(value).strip()
    ]
    matched_ids: list[int] = []
    represented: list[str] = []
    unmatched: list[str] = []

    for intervention in interventions:
        matches = [
            row
            for row in canonical_actions
            if (
                (car_id is None or int(row.get("car_id") or 0) == car_id)
                and str(row.get("status") or "").lower() == "completed"
                and _semantically_equivalent_action(
                    intervention,
                    row.get("title"),
                )
            )
        ]
        if not matches:
            unmatched.append(intervention)
            continue
        represented.append(intervention)
        for row in matches:
            action_id = row.get("treatment_action_id")
            if action_id is not None and int(action_id) not in matched_ids:
                matched_ids.append(int(action_id))

    return matched_ids, represented, unmatched


def _validated_historical_intelligence(
    payload: dict[str, Any],
    trusted_context: dict[str, Any],
) -> dict[str, Any]:
    """Reject invented canonical IDs and impossible missing/already-recorded states."""

    normalized = dict(payload)
    known_car_ids = {
        int(row.get("car_id"))
        for row in (trusted_context.get("known_client_vehicles") or [])
        if row.get("car_id") is not None
    }
    canonical = trusted_context.get("canonical_history") or {}
    known_action_ids = {
        int(row.get("treatment_action_id"))
        for row in (canonical.get("treatment_actions") or [])
        if row.get("treatment_action_id") is not None
    }
    known_episode_ids = {
        int(row.get("historical_episode_id"))
        for row in (canonical.get("historical_service_episodes") or [])
        if row.get("historical_episode_id") is not None
    }

    episode_by_id = {
        str(row.get("episode_candidate_id") or ""): row
        for row in (normalized.get("service_episode_candidates") or [])
        if isinstance(row, dict) and row.get("episode_candidate_id")
    }
    canonical_actions = [
        row
        for row in (canonical.get("treatment_actions") or [])
        if isinstance(row, dict)
    ]

    comparisons = []
    for row in normalized.get("canonical_comparisons") or []:
        if not isinstance(row, dict):
            continue
        clean = dict(row)
        clean["matched_car_id"] = (
            int(clean["matched_car_id"])
            if clean.get("matched_car_id") in known_car_ids
            else None
        )
        clean["matched_treatment_action_ids"] = [
            int(value)
            for value in (clean.get("matched_treatment_action_ids") or [])
            if value in known_action_ids
        ]
        clean["matched_historical_episode_ids"] = [
            int(value)
            for value in (clean.get("matched_historical_episode_ids") or [])
            if value in known_episode_ids
        ]

        represented = list(clean.get("already_represented_facts") or [])
        comparison = str(clean.get("comparison") or "uncertain")
        episode = episode_by_id.get(str(clean.get("episode_candidate_id") or ""))

        if episode is not None and comparison in {
            "missing_from_durable_history",
            "partially_represented",
        }:
            effective_car_id = clean.get("matched_car_id")
            if effective_car_id is None:
                vehicle_candidate_id = str(episode.get("vehicle_candidate_id") or "")
                vehicle_candidate = next(
                    (
                        item
                        for item in (normalized.get("vehicle_candidates") or [])
                        if isinstance(item, dict)
                        and str(item.get("candidate_id") or "") == vehicle_candidate_id
                    ),
                    None,
                )
                if (
                    vehicle_candidate is not None
                    and vehicle_candidate.get("identity_state")
                    == "selected_vehicle_match"
                ):
                    effective_car_id = int(
                        trusted_context.get("selected_vehicle_car_id")
                        or trusted_context.get("car_id")
                        or 0
                    ) or None

            deterministic_ids, deterministic_facts, unmatched_interventions = (
                _canonical_action_matches_for_episode(
                    episode,
                    car_id=effective_car_id,
                    canonical_actions=canonical_actions,
                )
            )
            for action_id in deterministic_ids:
                if action_id not in clean["matched_treatment_action_ids"]:
                    clean["matched_treatment_action_ids"].append(action_id)
            for fact in deterministic_facts:
                if fact not in represented:
                    represented.append(fact)

            missing_facts = [
                str(value)
                for value in (clean.get("missing_facts") or [])
                if str(value).strip()
            ]
            clean["missing_facts"] = [
                fact
                for fact in missing_facts
                if not any(
                    _semantically_equivalent_action(fact, represented_fact)
                    for represented_fact in deterministic_facts
                )
            ]
            if deterministic_facts:
                clean["already_represented_facts"] = represented
                clean["advisor_confirmation_required"] = True
                if unmatched_interventions or clean["missing_facts"]:
                    clean["comparison"] = "partially_represented"
                else:
                    clean["comparison"] = "already_represented"
                clean["reason"] = (
                    "Aura's deterministic canonical guard matched source-supported "
                    "completed work to an existing completed Treatment Action. "
                    "Only unmatched source facts may be proposed."
                )

        represented = list(clean.get("already_represented_facts") or [])
        matched_any = bool(
            clean["matched_treatment_action_ids"]
            or clean["matched_historical_episode_ids"]
            or represented
        )
        comparison = str(clean.get("comparison") or "uncertain")
        if comparison == "missing_from_durable_history" and matched_any:
            clean["comparison"] = "partially_represented"
            clean["advisor_confirmation_required"] = True
            clean["reason"] = (
                "Aura found canonical matches in the model comparison. The episode "
                "cannot be treated as wholly missing; review only the remaining facts."
            )
        elif comparison == "already_represented" and not matched_any:
            clean["comparison"] = "uncertain"
            clean["advisor_confirmation_required"] = True
            clean["reason"] = (
                "The model marked this episode represented but did not cite a supplied "
                "canonical record. Advisor confirmation is required."
            )
        comparisons.append(clean)

    normalized["canonical_comparisons"] = comparisons
    return normalized


def _corpus(evidence: VehicleEvidence) -> str:
    chunks: list[str] = []
    items = (
        EvidenceBundleItem.query.filter_by(bundle_evidence_id=evidence.id)
        .order_by(EvidenceBundleItem.member_index.asc())
        .all()
    )
    for item in items:
        child = item.child
        if child is None:
            continue
        source_ref = _member_source_ref(item)
        for extraction_type in (
            "document_text",
            "transcription",
            "image_observation",
        ):
            row = (
                EvidenceExtraction.query.filter_by(
                    evidence_id=child.id,
                    extraction_type=extraction_type,
                    status="completed",
                )
                .order_by(EvidenceExtraction.id.desc())
                .first()
            )
            if row is None:
                continue
            data = decrypt_extraction_payload(row)
            if extraction_type in {"document_text", "transcription"}:
                value = str(data.get("text") or "").strip()
                if value:
                    chunks.append(value)
            else:
                summary = str(data.get("summary") or "").strip()
                observations = data.get("observations") or []
                visible_text = str(data.get("visible_text") or "").strip()
                uncertainty = data.get("uncertainties") or []
                chunks.append(
                    source_ref
                    + " "
                    + summary
                    + ("\nObservations: " + " | ".join(map(str, observations)) if observations else "")
                    + ("\nVisible text: " + visible_text if visible_text else "")
                    + ("\nUncertainty: " + " | ".join(map(str, uncertainty)) if uncertainty else "")
                )

    joined = "\n\n".join(chunk for chunk in chunks if chunk).strip()
    if not joined:
        raise HistoricalIngestionError("No usable evidence was extracted from this bundle.")
    if len(joined) > MAX_CORPUS_CHARS:
        raise HistoricalIngestionError(
            "This WhatsApp case bundle contains more extracted evidence than Aura can "
            "reconcile safely in one whole-case pass. No source was silently truncated."
        )
    return joined


def _counts(evidence_id: int) -> tuple[int, int]:
    items = EvidenceBundleItem.query.filter_by(bundle_evidence_id=evidence_id).all()
    return sum(1 for item in items if _child_done(item)), len(items)


def _mark_analysis_failed(
    analysis: EvidenceExtraction,
    exc: Exception | str,
) -> WhatsAppBundleStatus:
    db.session.rollback()
    analysis = db.session.get(EvidenceExtraction, analysis.id)
    if analysis is None:
        raise HistoricalIngestionError("Bundle analysis state disappeared.")
    analysis.status = "failed"
    analysis.completed_at = _utcnow_naive()
    analysis.provenance = {
        **(analysis.provenance or {}),
        "background_stage": "failed",
        "failure_class": type(exc).__name__ if isinstance(exc, Exception) else "BundleFailure",
        "failure_detail": str(exc).replace("\n", " ")[:700],
    }
    db.session.commit()
    done, total = _counts(analysis.evidence_id)
    return WhatsAppBundleStatus(
        evidence_id=analysis.evidence_id,
        extraction_id=analysis.id,
        status="failed",
        phase="failed",
        message="Rina could not complete this bundle analysis. No vehicle history was changed.",
        completed_items=done,
        total_items=total,
    )


def advance_whatsapp_bundle_analysis(
    *,
    extraction_id: int,
    actor_user_id: int,
    storage_provider: EvidenceStorageProvider | None = None,
    storage_config: Mapping[str, object] | None = None,
    analyzer: WhatsAppBundleAdvisorAnalyzer | None = None,
) -> WhatsAppBundleStatus:
    analysis = db.session.get(EvidenceExtraction, extraction_id)
    if analysis is None or analysis.evidence is None:
        raise HistoricalIngestionError("WhatsApp bundle analysis was not found.")
    evidence = analysis.evidence
    _authority(actor_user_id, evidence.car_id)
    if evidence.evidence_type != "archive":
        raise HistoricalIngestionError("This source is not a WhatsApp archive.")

    if analysis.status == "completed":
        done, total = _counts(evidence.id)
        return WhatsAppBundleStatus(
            evidence_id=evidence.id,
            extraction_id=analysis.id,
            status="completed",
            phase="completed",
            message="WhatsApp case-bundle extraction is ready for advisor review.",
            completed_items=done,
            total_items=total,
            review_ready=True,
        )
    if analysis.status == "failed":
        done, total = _counts(evidence.id)
        return WhatsAppBundleStatus(
            evidence_id=evidence.id,
            extraction_id=analysis.id,
            status="failed",
            phase="failed",
            message="WhatsApp case-bundle analysis failed safely.",
            completed_items=done,
            total_items=total,
        )

    provider = _provider(storage_provider, storage_config or current_app.config)
    analyzer = analyzer or WhatsAppBundleAdvisorAnalyzer()
    provenance = dict(analysis.provenance or {})
    stage = str(provenance.get("background_stage") or "unpacking")

    try:
        if stage == "unpacking":
            retrieved = provider.get_bytes(
                object_key=evidence.object_key,
                max_bytes=MAX_ARCHIVE_BYTES,
            )
            manifest = _materialize_bundle_children(
                evidence=evidence,
                actor_user_id=actor_user_id,
                archive_payload=retrieved.payload,
                storage_provider=provider,
            )
            analysis.provenance = {
                **provenance,
                "background_stage": "preprocessing",
                "manifest_extraction_id": manifest.id,
            }
            db.session.commit()
            done, total = _counts(evidence.id)
            return WhatsAppBundleStatus(
                evidence_id=evidence.id,
                extraction_id=analysis.id,
                status="processing",
                phase="preprocessing",
                message=f"Rina secured the bundle. Analysing media 0/{total}.",
                completed_items=done,
                total_items=total,
            )

        if stage == "preprocessing":
            items = (
                EvidenceBundleItem.query.filter_by(bundle_evidence_id=evidence.id)
                .order_by(EvidenceBundleItem.member_index.asc())
                .all()
            )
            pending = next((item for item in items if not _child_done(item)), None)
            if pending is not None:
                try:
                    _process_child(
                        item=pending,
                        storage_provider=provider,
                        analyzer=analyzer,
                    )
                except (
                    RinaProviderError,
                    EvidenceStorageError,
                    HistoricalIngestionError,
                    EvidenceImageValidationError,
                    OSError,
                    ValueError,
                ) as exc:
                    required_type = {
                        "transcript": "document_text",
                        "document": "document_text",
                        "image": "image_observation",
                        "audio": "transcription",
                        "video": "image_observation",
                    }[pending.member_kind]
                    _mark_child_failure(
                        evidence_id=pending.child_evidence_id,
                        extraction_type=required_type,
                        provider="aura_bundle",
                        exc=exc,
                    )
                    if pending.member_kind == "video":
                        child = pending.child
                        existing_types = {
                            row.extraction_type for row in child.extractions
                        } if child is not None else set()
                        for missing_type in {"transcription", "image_observation"} - existing_types:
                            _mark_child_failure(
                                evidence_id=pending.child_evidence_id,
                                extraction_type=missing_type,
                                provider="aura_bundle",
                                exc=exc,
                            )
                done, total = _counts(evidence.id)
                return WhatsAppBundleStatus(
                    evidence_id=evidence.id,
                    extraction_id=analysis.id,
                    status="processing",
                    phase="preprocessing",
                    message=f"Rina is analysing WhatsApp media {done}/{total}.",
                    completed_items=done,
                    total_items=total,
                )

            corpus = _corpus(evidence)
            intelligence_context = _historical_intelligence_context(evidence.car)
            response = analyzer.start_bundle_understanding_background(
                corpus=corpus,
                trusted_vehicle_context=intelligence_context,
            )
            analysis.provider = analyzer.provider_name
            analysis.provider_model = response.model
            analysis.provider_request_id = response.response_id
            analysis.provenance = {
                **provenance,
                "background_stage": "understanding",
                "background_response_id": response.response_id,
                "corpus_sha256": hashlib.sha256(corpus.encode("utf-8")).hexdigest(),
                "corpus_characters": len(corpus),
            }
            db.session.commit()
            done, total = _counts(evidence.id)
            return WhatsAppBundleStatus(
                evidence_id=evidence.id,
                extraction_id=analysis.id,
                status="processing",
                phase="understanding",
                message="Rina is reconciling the full WhatsApp chronology and media context.",
                completed_items=done,
                total_items=total,
            )

        response_id = str(
            provenance.get("background_response_id")
            or analysis.provider_request_id
            or ""
        )
        if not response_id:
            return _mark_analysis_failed(analysis, "Background response id is missing.")

        try:
            response = analyzer.retrieve_background(response_id)
        except RinaProviderTransientError:
            done, total = _counts(evidence.id)
            return WhatsAppBundleStatus(
                evidence_id=evidence.id,
                extraction_id=analysis.id,
                status="processing",
                phase=stage,
                message="Rina is still analysing. Aura will check again automatically.",
                completed_items=done,
                total_items=total,
            )

        if response.status in {"queued", "in_progress"}:
            done, total = _counts(evidence.id)
            return WhatsAppBundleStatus(
                evidence_id=evidence.id,
                extraction_id=analysis.id,
                status="processing",
                phase=stage,
                message=(
                    "Rina is reconciling the complete case bundle."
                    if stage == "understanding"
                    else "Rina is preparing the advisor review."
                ),
                completed_items=done,
                total_items=total,
            )
        if response.status != "completed" or not isinstance(response.payload, dict):
            return _mark_analysis_failed(
                analysis,
                f"OpenAI background response ended with status {response.status}.",
            )

        corpus = _corpus(evidence)
        if stage == "understanding":
            intelligence_context = _historical_intelligence_context(evidence.car)
            coverage = _bundle_source_coverage(evidence, corpus=corpus)
            understanding_payload = {
                **response.payload,
                "source_coverage": coverage,
                "historical_intelligence_version": 2,
            }
            understanding = _create_encrypted_extraction(
                evidence_id=evidence.id,
                extraction_type="document_understanding",
                provider=analyzer.provider_name,
                provider_model=response.model,
                payload=understanding_payload,
                provenance={
                    "analysis_pipeline": PIPELINE,
                    "semantic_authority": "candidate_only",
                    "reasoning_stage": "whole_bundle_understanding",
                    "historical_intelligence_version": 2,
                },
            )
            next_response = analyzer.start_bundle_structuring_background(
                understanding=understanding_payload,
                trusted_vehicle_context=intelligence_context,
            )
            analysis.provider_model = next_response.model
            analysis.provider_request_id = next_response.response_id
            analysis.provenance = {
                **provenance,
                "background_stage": "structuring",
                "background_response_id": next_response.response_id,
                "understanding_extraction_id": understanding.id,
                "understanding_response_id": response.response_id,
            }
            db.session.commit()
            done, total = _counts(evidence.id)
            return WhatsAppBundleStatus(
                evidence_id=evidence.id,
                extraction_id=analysis.id,
                status="processing",
                phase="structuring",
                message="Rina understood the case and is preparing advisor-review candidates.",
                completed_items=done,
                total_items=total,
            )

        if stage == "structuring":
            normalized = _normalise_provider_payload(
                response.payload,
                source_text=corpus,
                page_count=500,
            )
            normalized["case_focus"] = str(
                response.payload.get("case_focus") or ""
            ).strip()[:4000]
            normalized["priority_threads"] = (
                response.payload.get("priority_threads")
                if isinstance(response.payload.get("priority_threads"), list)
                else []
            )[:40]
            normalized["supporting_context"] = (
                response.payload.get("supporting_context")
                if isinstance(response.payload.get("supporting_context"), list)
                else []
            )[:40]
            normalized["low_relevance_context"] = (
                response.payload.get("low_relevance_context")
                if isinstance(response.payload.get("low_relevance_context"), list)
                else []
            )[:40]
            normalized["vehicle_candidates"] = (
                response.payload.get("vehicle_candidates")
                if isinstance(response.payload.get("vehicle_candidates"), list)
                else []
            )[:24]
            normalized["service_episode_candidates"] = (
                response.payload.get("service_episode_candidates")
                if isinstance(response.payload.get("service_episode_candidates"), list)
                else []
            )[:80]
            normalized["canonical_comparisons"] = (
                response.payload.get("canonical_comparisons")
                if isinstance(response.payload.get("canonical_comparisons"), list)
                else []
            )[:80]
            normalized["source_coverage"] = _bundle_source_coverage(
                evidence,
                corpus=corpus,
            )
            normalized["historical_intelligence_version"] = 2
            normalized = _validated_historical_intelligence(
                normalized,
                _historical_intelligence_context(evidence.car),
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
                "reasoning_stage": "advisor_bundle_structuring",
                "historical_intelligence_version": 2,
            }
            db.session.commit()
            done, total = _counts(evidence.id)
            return WhatsAppBundleStatus(
                evidence_id=evidence.id,
                extraction_id=analysis.id,
                status="completed",
                phase="completed",
                message="WhatsApp case-bundle extraction is ready for advisor review.",
                completed_items=done,
                total_items=total,
                review_ready=True,
            )

        return _mark_analysis_failed(analysis, f"Unknown bundle stage: {stage}")
    except Exception as exc:
        current_app.logger.exception(
            "whatsapp_bundle_analysis_failed evidence_id=%s extraction_id=%s stage=%s",
            evidence.id,
            analysis.id,
            stage,
        )
        return _mark_analysis_failed(analysis, exc)
