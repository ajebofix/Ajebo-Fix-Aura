"""Wave 1.4 evidence schema contracts that do not require object storage."""

from evidence.models import (
    CAPTURE_TIME_SOURCES,
    EVIDENCE_PURPOSES,
    EVIDENCE_RELATIONSHIP_TYPES,
    EVIDENCE_REVIEW_STATUSES,
    EVIDENCE_SOURCE_CHANNELS,
    EVIDENCE_STORAGE_STATES,
    EVIDENCE_SUBJECT_TYPES,
    EVIDENCE_TYPES,
    EVIDENCE_VISIBILITY,
    EXTRACTION_REVIEW_STATUSES,
    EXTRACTION_STATUSES,
    EXTRACTION_TYPES,
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
    assert EVIDENCE_TYPES == ("image", "document", "audio")
    assert set(EVIDENCE_SOURCE_CHANNELS) == {"web", "whatsapp", "api"}
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
        "transcription",
        "structured_fields",
    }
    assert set(EXTRACTION_STATUSES) == {"pending", "processing", "completed", "failed"}
    assert set(EXTRACTION_REVIEW_STATUSES) == {
        "unreviewed",
        "accepted",
        "rejected",
        "corrected",
    }
