"""Safe WhatsApp ZIP intake and resumable multimodal historical analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import hashlib
import json
import mimetypes
from pathlib import PurePosixPath
import re
import subprocess
import tempfile
import uuid
import zipfile
from typing import Any, Mapping

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from evidence.image_sanitizer import (
    EvidenceImageValidationError,
    sanitize_evidence_image,
)
from evidence.models import EvidenceBundleItem, EvidenceExtraction, VehicleEvidence
from evidence.storage import (
    EvidenceStorageConfigurationError,
    EvidenceStorageError,
    EvidenceStorageProvider,
    build_evidence_storage_provider,
)
from extensions import db
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
from models import Car
from rina.providers.base import RinaProviderError, RinaProviderTransientError


MAX_ARCHIVE_BYTES = 150 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 800
MAX_TOTAL_UNCOMPRESSED_BYTES = 750 * 1024 * 1024
MAX_MEMBER_BYTES = 100 * 1024 * 1024
MAX_TEXT_BYTES = 12 * 1024 * 1024
MAX_CORPUS_CHARS = 500_000
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
    if purpose not in {"service_document", "diagnostic_document", "treatment_evidence"}:
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
        for item in manifest:
            kind = item["kind"]
            if kind == "unsupported":
                created.append({**item, "child_evidence_id": None, "status": "skipped"})
                continue

            member_payload = _extract_member_bytes(
                archive_payload,
                str(item["original_name"]),
            )
            suffix = PurePosixPath(str(item["original_name"])).suffix.lower()

            if kind == "image":
                content_type = mimetypes.types_map.get(suffix, "")
                sanitized = sanitize_evidence_image(
                    BytesIO(member_payload),
                    declared_content_type=content_type,
                )
                stored_payload = sanitized.payload
                content_type = sanitized.content_type
                extension = sanitized.extension
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
                # Validate the text now; store the original bytes as evidence.
                _decode_text(member_payload)
                stored_payload = member_payload
                content_type = "text/plain"
                extension = ".txt"
                evidence_type = "document"
            elif kind == "audio":
                stored_payload = member_payload
                content_type = _AUDIO_MIME.get(suffix, "application/octet-stream")
                extension = suffix or ".audio"
                evidence_type = "audio"
            elif kind == "video":
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
            created.append({**item, "child_evidence_id": child.id, "status": "materialized"})
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


def _child_done(item: EvidenceBundleItem) -> bool:
    required = {
        "transcript": {"document_text"},
        "document": {"document_text"},
        "image": {"image_observation"},
        "audio": {"transcription"},
        "video": {"transcription", "image_observation"},
    }[item.member_kind]
    completed = {
        row.extraction_type
        for row in item.child.extractions
        if row.status == "completed"
    }
    failed = {
        row.extraction_type
        for row in item.child.extractions
        if row.status == "failed"
    }
    return required <= (completed | failed)


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


def _video_derivatives(payload: bytes, suffix: str) -> tuple[bytes | None, list[bytes]]:
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise HistoricalIngestionConfigurationError(
            "Video analysis runtime is not installed."
        ) from exc

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    frames: list[bytes] = []
    audio: bytes | None = None

    with tempfile.TemporaryDirectory(prefix="aura-whatsapp-video-") as tmp:
        source = f"{tmp}/source{suffix or '.mp4'}"
        with open(source, "wb") as handle:
            handle.write(payload)

        audio_path = f"{tmp}/audio.mp3"
        audio_run = subprocess.run(
            [
                ffmpeg, "-y", "-i", source, "-vn", "-ac", "1", "-ar", "16000",
                "-b:a", "48k", audio_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
        if audio_run.returncode == 0:
            try:
                with open(audio_path, "rb") as handle:
                    value = handle.read(25 * 1024 * 1024 + 1)
                if value and len(value) <= 25 * 1024 * 1024:
                    audio = value
            except OSError:
                audio = None

        for idx, timestamp in enumerate(("0.5", "3", "8"), start=1):
            frame_path = f"{tmp}/frame-{idx}.jpg"
            run = subprocess.run(
                [
                    ffmpeg, "-y", "-ss", timestamp, "-i", source,
                    "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2",
                    "-q:v", "3", frame_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=12,
                check=False,
            )
            if run.returncode == 0:
                try:
                    with open(frame_path, "rb") as handle:
                        frame = handle.read(3 * 1024 * 1024 + 1)
                    if frame and len(frame) <= 3 * 1024 * 1024:
                        frames.append(frame)
                except OSError:
                    pass

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
    source_ref = f"[{item.member_kind.upper()} evidence:{child.id}]"

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
        transcript = analyzer.transcribe_audio(
            payload=payload,
            filename=child.safe_display_name,
            content_type=child.content_type,
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
                    filename="video-audio.mp3",
                    content_type="audio/mpeg",
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
        source_ref = f"[{item.member_kind.upper()} evidence:{child.id}]"
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
    return joined[:MAX_CORPUS_CHARS]


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
                    subprocess.SubprocessError,
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
            response = analyzer.start_bundle_understanding_background(
                corpus=corpus,
                trusted_vehicle_context=_trusted_vehicle_context(evidence.car),
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
            understanding = _create_encrypted_extraction(
                evidence_id=evidence.id,
                extraction_type="document_understanding",
                provider=analyzer.provider_name,
                provider_model=response.model,
                payload=response.payload,
                provenance={
                    "analysis_pipeline": PIPELINE,
                    "semantic_authority": "candidate_only",
                    "reasoning_stage": "whole_bundle_understanding",
                },
            )
            next_response = analyzer.start_bundle_structuring_background(
                understanding=response.payload,
                trusted_vehicle_context=_trusted_vehicle_context(evidence.car),
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
