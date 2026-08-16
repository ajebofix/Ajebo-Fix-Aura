"""Verify the Wave 1.4 evidence schema on PostgreSQL.

This script intentionally uses metadata and synthetic rows only. It never touches
object storage or external AI providers. It validates the evidence-domain
milestone independently of whichever later compatible Alembic revision is the
current application head.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


EVIDENCE_SCHEMA_REVISION = "c62f1a4e8d30"


def _must_fail(engine, sql: str, params: dict[str, object], label: str) -> None:
    try:
        with engine.begin() as connection:
            connection.execute(text(sql), params)
    except IntegrityError:
        return
    raise SystemExit(f"{label} bypassed a PostgreSQL integrity constraint")


def main() -> None:
    database_uri = os.environ["SQLALCHEMY_DATABASE_URI"]
    engine = create_engine(database_uri)
    inspector = inspect(engine)

    tables = set(inspector.get_table_names())
    required_tables = {"vehicle_evidence", "evidence_links", "evidence_extractions"}
    missing_tables = required_tables - tables
    if missing_tables:
        raise SystemExit(f"Evidence schema missing tables: {sorted(missing_tables)}")

    evidence_columns = {c["name"] for c in inspector.get_columns("vehicle_evidence")}
    required_evidence = {
        "id",
        "car_id",
        "uploaded_by_user_id",
        "evidence_type",
        "purpose",
        "source_channel",
        "visibility",
        "review_status",
        "storage_provider",
        "storage_state",
        "storage_failure_reason_code",
        "object_key",
        "safe_display_name",
        "content_type",
        "byte_size",
        "sha256",
        "captured_at",
        "capture_time_source",
        "uploaded_at",
        "consent_basis",
        "lawful_purpose",
        "retention_until",
        "deleted_at",
        "reviewed_by_user_id",
        "reviewed_at",
        "review_reason_code",
        "created_at",
        "updated_at",
    }
    if required_evidence - evidence_columns:
        raise SystemExit(
            f"VehicleEvidence columns missing: {sorted(required_evidence - evidence_columns)}"
        )

    prohibited_evidence = {
        "bytes",
        "blob",
        "file_bytes",
        "raw_media",
        "public_url",
        "presigned_url",
        "original_filename",
    }
    if prohibited_evidence & evidence_columns:
        raise SystemExit(
            "VehicleEvidence contains prohibited raw/public storage columns: "
            f"{sorted(prohibited_evidence & evidence_columns)}"
        )

    extraction_columns = {
        c["name"] for c in inspector.get_columns("evidence_extractions")
    }
    prohibited_extraction = {
        "extracted_text",
        "transcript",
        "raw_text",
        "provider_response",
        "prompt",
        "chain_of_thought",
    }
    if prohibited_extraction & extraction_columns:
        raise SystemExit(
            "EvidenceExtraction contains prohibited plaintext/provider columns: "
            f"{sorted(prohibited_extraction & extraction_columns)}"
        )

    required_extraction = {
        "result_ciphertext",
        "result_key_version",
        "result_sha256",
        "reviewed_result_ciphertext",
        "reviewed_result_key_version",
        "reviewed_result_sha256",
        "provenance",
    }
    if required_extraction - extraction_columns:
        raise SystemExit(
            "Encrypted extraction boundary missing: "
            f"{sorted(required_extraction - extraction_columns)}"
        )

    evidence_checks = {
        check["name"] for check in inspector.get_check_constraints("vehicle_evidence")
    }
    required_evidence_checks = {
        "ck_vehicle_evidence_type",
        "ck_vehicle_evidence_purpose",
        "ck_vehicle_evidence_source_channel",
        "ck_vehicle_evidence_visibility",
        "ck_vehicle_evidence_review_status",
        "ck_vehicle_evidence_storage_state",
        "ck_vehicle_evidence_byte_size_positive",
        "ck_vehicle_evidence_sha256_length",
        "ck_vehicle_evidence_capture_time_source",
        "ck_vehicle_evidence_review_metadata",
        "ck_vehicle_evidence_deleted_timestamp",
        "ck_vehicle_evidence_storage_deleted_requires_logical_delete",
    }
    if required_evidence_checks - evidence_checks:
        raise SystemExit(
            "VehicleEvidence PostgreSQL CHECKs missing: "
            f"{sorted(required_evidence_checks - evidence_checks)}"
        )

    link_checks = {
        check["name"] for check in inspector.get_check_constraints("evidence_links")
    }
    if {
        "ck_evidence_links_subject_type",
        "ck_evidence_links_relationship_type",
    } - link_checks:
        raise SystemExit("EvidenceLink controlled vocabulary CHECKs are missing")

    extraction_checks = {
        check["name"]
        for check in inspector.get_check_constraints("evidence_extractions")
    }
    required_extraction_checks = {
        "ck_evidence_extractions_type",
        "ck_evidence_extractions_status",
        "ck_evidence_extractions_review_status",
        "ck_evidence_extractions_confidence",
        "ck_evidence_extractions_completed_at",
        "ck_evidence_extractions_result_encryption_pair",
        "ck_evidence_extractions_review_metadata",
        "ck_evidence_extractions_corrected_payload",
        "ck_evidence_extractions_reviewed_result_pair",
    }
    if required_extraction_checks - extraction_checks:
        raise SystemExit(
            "EvidenceExtraction PostgreSQL CHECKs missing: "
            f"{sorted(required_extraction_checks - extraction_checks)}"
        )

    evidence_indexes = {
        index["name"] for index in inspector.get_indexes("vehicle_evidence")
    }
    if {
        "ix_vehicle_evidence_car_time",
        "ix_vehicle_evidence_car_sha256",
        "ix_vehicle_evidence_review_status",
        "ix_vehicle_evidence_storage_state",
    } - evidence_indexes:
        raise SystemExit("VehicleEvidence operational indexes are missing")

    now = datetime.utcnow()
    with engine.begin() as connection:
        current_revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

        user_id = connection.execute(
            text(
                "INSERT INTO users "
                "(name, email, phone_number, password_hash, role, is_active, created_at, email_verified_at) "
                "VALUES ('Evidence CI User', 'evidence-ci@example.com', '+2348000000144', 'hash', 'user', TRUE, :now, :now) "
                "RETURNING id"
            ),
            {"now": now},
        ).scalar_one()
        car_id = connection.execute(
            text(
                "INSERT INTO cars "
                "(brand, model, year, vin, current_mileage, created_at, vehicle_identity_source) "
                "VALUES ('Mercedes-Benz', 'GLE', 2024, 'WDDEVIDENCE0000144', 1000, :now, 'manual') "
                "RETURNING id"
            ),
            {"now": now},
        ).scalar_one()
        evidence_id = connection.execute(
            text(
                "INSERT INTO vehicle_evidence "
                "(car_id, uploaded_by_user_id, evidence_type, purpose, source_channel, visibility, review_status, "
                "storage_provider, storage_state, object_key, safe_display_name, content_type, byte_size, sha256, "
                "uploaded_at, consent_basis, lawful_purpose, created_at, updated_at) "
                "VALUES (:car_id, :user_id, 'image', 'concern_support', 'web', 'client', 'pending_review', "
                "'r2', 'pending', 'production/evidence/test-object', 'evidence.jpg', 'image/jpeg', 1024, :sha256, "
                ":now, 'explicit_upload', 'vehicle_care', :now, :now) RETURNING id"
            ),
            {
                "car_id": car_id,
                "user_id": user_id,
                "sha256": "a" * 64,
                "now": now,
            },
        ).scalar_one()

        connection.execute(
            text(
                "INSERT INTO evidence_links "
                "(evidence_id, car_id, subject_type, subject_id, relationship_type, created_by_user_id, created_at) "
                "VALUES (:evidence_id, :car_id, 'reported_concern', 999001, 'supports', :user_id, :now)"
            ),
            {
                "evidence_id": evidence_id,
                "car_id": car_id,
                "user_id": user_id,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO evidence_extractions "
                "(evidence_id, extraction_type, provider, status, review_status, created_at) "
                "VALUES (:evidence_id, 'image_observation', 'test-provider', 'pending', 'unreviewed', :now)"
            ),
            {"evidence_id": evidence_id, "now": now},
        )

    base_evidence_params = {
        "car_id": car_id,
        "user_id": user_id,
        "now": now,
        "sha256": "b" * 64,
    }

    _must_fail(
        engine,
        "INSERT INTO vehicle_evidence "
        "(car_id, uploaded_by_user_id, evidence_type, purpose, source_channel, visibility, review_status, "
        "storage_provider, storage_state, object_key, safe_display_name, content_type, byte_size, sha256, "
        "uploaded_at, consent_basis, lawful_purpose, created_at, updated_at) "
        "VALUES (:car_id, :user_id, 'video', 'concern_support', 'web', 'client', 'pending_review', "
        "'r2', 'pending', 'production/evidence/invalid-type', 'bad.bin', 'application/octet-stream', 100, :sha256, "
        ":now, 'explicit_upload', 'vehicle_care', :now, :now)",
        base_evidence_params,
        "invalid evidence type",
    )
    _must_fail(
        engine,
        "INSERT INTO vehicle_evidence "
        "(car_id, uploaded_by_user_id, evidence_type, purpose, source_channel, visibility, review_status, "
        "storage_provider, storage_state, object_key, safe_display_name, content_type, byte_size, sha256, "
        "uploaded_at, consent_basis, lawful_purpose, created_at, updated_at) "
        "VALUES (:car_id, :user_id, 'image', 'concern_support', 'web', 'client', 'pending_review', "
        "'r2', 'pending', 'production/evidence/zero-byte', 'zero.jpg', 'image/jpeg', 0, :sha256, "
        ":now, 'explicit_upload', 'vehicle_care', :now, :now)",
        base_evidence_params,
        "zero-byte evidence",
    )
    _must_fail(
        engine,
        "UPDATE vehicle_evidence SET review_status = 'accepted' WHERE id = :evidence_id",
        {"evidence_id": evidence_id},
        "accepted evidence without reviewer metadata",
    )
    _must_fail(
        engine,
        "UPDATE vehicle_evidence SET review_status = 'deleted' WHERE id = :evidence_id",
        {"evidence_id": evidence_id},
        "deleted evidence without deletion timestamp",
    )
    _must_fail(
        engine,
        "INSERT INTO evidence_links "
        "(evidence_id, car_id, subject_type, subject_id, relationship_type, created_by_user_id, created_at) "
        "VALUES (:evidence_id, :car_id, 'mystery_subject', 1, 'supports', :user_id, :now)",
        {
            "evidence_id": evidence_id,
            "car_id": car_id,
            "user_id": user_id,
            "now": now,
        },
        "invalid evidence link subject",
    )
    _must_fail(
        engine,
        "INSERT INTO evidence_extractions "
        "(evidence_id, extraction_type, provider, status, confidence, review_status, created_at) "
        "VALUES (:evidence_id, 'image_observation', 'test-provider', 'processing', 1.5, 'unreviewed', :now)",
        {"evidence_id": evidence_id, "now": now},
        "invalid extraction confidence",
    )
    _must_fail(
        engine,
        "INSERT INTO evidence_extractions "
        "(evidence_id, extraction_type, provider, status, review_status, reviewed_by_user_id, reviewed_at, completed_at, created_at) "
        "VALUES (:evidence_id, 'image_observation', 'test-provider', 'completed', 'corrected', :user_id, :now, :now, :now)",
        {"evidence_id": evidence_id, "user_id": user_id, "now": now},
        "corrected extraction without additive encrypted correction",
    )

    print(
        "Wave 1.4 PostgreSQL evidence schema milestone "
        f"{EVIDENCE_SCHEMA_REVISION} verified at current revision {current_revision}."
    )


if __name__ == "__main__":
    main()
