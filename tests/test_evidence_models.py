"""Wave 1.4 evidence schema contracts that do not require object storage."""

from evidence.models import (
    CAPTURE_TIME_SOURCES,
    EVIDENCE_PURPOSES,
    EVIDENCE_RELATIONSHIP_TYPES,
    EVIDENCE_REVIEW_STATUSES,
    EVIDENCE_SOURCE_CHANNELS,
    EVIDENCE_STORAGE_STATES,
    HISTORICAL_SOURCE_TYPES,
    SUPPORTED_HISTORICAL_IMPORT_SOURCE_TYPES,
    EVIDENCE_SUBJECT_TYPES,
    EVIDENCE_TYPES,
    EVIDENCE_VISIBILITY,
    EXTRACTION_REVIEW_STATUSES,
    EXTRACTION_STATUSES,
    EXTRACTION_TYPES,
    EvidenceBundleItem,
    EvidenceExtraction,
    EvidenceLink,
    VehicleEvidence,
)


def test_vehicle_evidence_metadata_only_boundary():
    columns = set(VehicleEvidence.__table__.columns.keys())

    assert {
        "car_id",
        "uploaded_by_user_id",
        "evidence_type",
        "purpose",
        "source_channel",
        "historical_source_type",
        "visibility",
        "review_status",
        "storage_provider",
        "storage_state",
        "object_key",
        "safe_display_name",
        "content_type",
        "byte_size",
        "sha256",
        "consent_basis",
        "lawful_purpose",
        "retention_until",
        "deleted_at",
    } <= columns

    prohibited = {
        "bytes",
        "blob",
        "file_bytes",
        "raw_media",
        "public_url",
        "presigned_url",
        "original_filename",
    }
    assert not (columns & prohibited)


def test_evidence_link_is_controlled_polymorphic_boundary():
    columns = set(EvidenceLink.__table__.columns.keys())
    assert {
        "evidence_id",
        "car_id",
        "subject_type",
        "subject_id",
        "relationship_type",
        "created_by_user_id",
    } <= columns

    constraint_names = {
        constraint.name
        for constraint in EvidenceLink.__table__.constraints
        if constraint.name
    }
    assert "uq_evidence_link_subject_relationship" in constraint_names


def test_extraction_payload_slots_are_encrypted_not_plaintext():
    columns = set(EvidenceExtraction.__table__.columns.keys())

    assert {
        "result_ciphertext",
        "result_key_version",
        "result_sha256",
        "reviewed_result_ciphertext",
        "reviewed_result_key_version",
        "reviewed_result_sha256",
        "provider",
        "provider_model",
        "provider_request_id",
        "provenance",
    } <= columns

    prohibited = {
        "extracted_text",
        "transcript",
        "raw_text",
        "provider_response",
        "prompt",
        "chain_of_thought",
    }
    assert not (columns & prohibited)


def test_initial_vocabularies_match_wave_1_4_architecture():
    assert EVIDENCE_TYPES == ("image", "document", "audio", "video", "archive")
    assert set(EVIDENCE_SOURCE_CHANNELS) == {"web", "whatsapp", "api"}
    assert set(HISTORICAL_SOURCE_TYPES) == {
        "standalone_document",
        "whatsapp_conversation",
        "instagram_conversation",
        "tiktok_conversation",
        "email_conversation",
        "sms_imessage_conversation",
        "other_conversation_archive",
    }
    assert set(SUPPORTED_HISTORICAL_IMPORT_SOURCE_TYPES) == {
        "standalone_document",
        "whatsapp_conversation",
    }
    assert set(EVIDENCE_VISIBILITY) == {"client", "advisor", "internal"}
    assert set(EVIDENCE_REVIEW_STATUSES) == {
        "pending_review",
        "accepted",
        "rejected",
        "superseded",
        "deleted",
    }

    wave_1_4_subjects = {
        "reported_concern",
        "consultation",
        "assessment",
        "treatment_plan",
        "vehicle_event",
    }
    assert wave_1_4_subjects <= set(EVIDENCE_SUBJECT_TYPES)
    assert set(EVIDENCE_SUBJECT_TYPES) - wave_1_4_subjects == {
        "treatment_action",
        "treatment_outcome",
    }

    assert "driver_observation" in EVIDENCE_PURPOSES
    assert "diagnostic_document" in EVIDENCE_PURPOSES
    assert "service_document" in EVIDENCE_PURPOSES
    assert "vehicle_history_context" in EVIDENCE_PURPOSES
    assert set(EVIDENCE_RELATIONSHIP_TYPES) == {"supports", "documents"}
    assert set(EVIDENCE_STORAGE_STATES) == {
        "pending",
        "available",
        "failed",
        "delete_pending",
        "deleted",
    }
    assert set(CAPTURE_TIME_SOURCES) == {"user_declared", "embedded_verified"}

    assert set(EXTRACTION_TYPES) == {
        "image_observation",
        "document_text",
        "document_understanding",
        "archive_manifest",
        "transcription",
        "structured_fields",
        "historical_case_attribution",
    }
    assert set(EXTRACTION_STATUSES) == {"pending", "processing", "completed", "failed"}
    assert set(EXTRACTION_REVIEW_STATUSES) == {
        "unreviewed",
        "accepted",
        "rejected",
        "corrected",
    }


def test_evidence_bundle_lineage_is_metadata_only():
    columns = set(EvidenceBundleItem.__table__.columns.keys())
    assert {
        "bundle_evidence_id",
        "child_evidence_id",
        "member_index",
        "member_kind",
        "member_sha256",
        "created_at",
    } <= columns
    prohibited = {"original_name", "raw_bytes", "transcript", "public_url"}
    assert not (columns & prohibited)

    constraint_names = {
        constraint.name
        for constraint in EvidenceBundleItem.__table__.constraints
        if constraint.name
    }
    assert "uq_evidence_bundle_member_index" in constraint_names
