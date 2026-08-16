"""Add canonical Wave 1.4 vehicle evidence domain.

Revision ID: c62f1a4e8d30
Revises: b41d8e3f0a21
Create Date: 2026-08-16

This migration adds metadata/lifecycle tables only. Raw media remains in private
object storage. Extraction payload columns are ciphertext slots so provider output
is not introduced as plaintext professional truth.
"""

from alembic import op
import sqlalchemy as sa


revision = "c62f1a4e8d30"
down_revision = "b41d8e3f0a21"
branch_labels = None
depends_on = None


EVIDENCE_TYPES = ("image", "document", "audio")
EVIDENCE_PURPOSES = (
    "concern_support",
    "consultation_support",
    "assessment_evidence",
    "treatment_evidence",
    "diagnostic_document",
    "service_document",
    "driver_observation",
)
SOURCE_CHANNELS = ("web", "whatsapp", "api")
VISIBILITY = ("client", "advisor", "internal")
REVIEW_STATUSES = (
    "pending_review",
    "accepted",
    "rejected",
    "superseded",
    "deleted",
)
STORAGE_STATES = ("pending", "available", "failed", "delete_pending", "deleted")
CAPTURE_TIME_SOURCES = ("user_declared", "embedded_verified")
SUBJECT_TYPES = (
    "reported_concern",
    "consultation",
    "assessment",
    "treatment_plan",
    "vehicle_event",
)
RELATIONSHIP_TYPES = ("supports", "documents")
EXTRACTION_TYPES = (
    "image_observation",
    "document_text",
    "transcription",
    "structured_fields",
)
EXTRACTION_STATUSES = ("pending", "processing", "completed", "failed")
EXTRACTION_REVIEW_STATUSES = ("unreviewed", "accepted", "rejected", "corrected")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    op.create_table(
        "vehicle_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(length=24), nullable=False),
        sa.Column("purpose", sa.String(length=48), nullable=False),
        sa.Column(
            "source_channel",
            sa.String(length=24),
            nullable=False,
            server_default="web",
        ),
        sa.Column(
            "visibility",
            sa.String(length=20),
            nullable=False,
            server_default="client",
        ),
        sa.Column(
            "review_status",
            sa.String(length=24),
            nullable=False,
            server_default="pending_review",
        ),
        sa.Column(
            "storage_provider",
            sa.String(length=32),
            nullable=False,
            server_default="r2",
        ),
        sa.Column(
            "storage_state",
            sa.String(length=24),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("storage_failure_reason_code", sa.String(length=64), nullable=True),
        sa.Column("object_key", sa.String(length=255), nullable=False),
        sa.Column("safe_display_name", sa.String(length=160), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("capture_time_source", sa.String(length=32), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("consent_basis", sa.String(length=64), nullable=False),
        sa.Column("lawful_purpose", sa.String(length=128), nullable=False),
        sa.Column("retention_until", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_reason_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["car_id"],
            ["cars.id"],
            name="fk_vehicle_evidence_car_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["users.id"],
            name="fk_vehicle_evidence_uploaded_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name="fk_vehicle_evidence_reviewed_by_user_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("object_key", name="uq_vehicle_evidence_object_key"),
    )

    op.create_table(
        "evidence_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=40), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column(
            "relationship_type",
            sa.String(length=32),
            nullable=False,
            server_default="supports",
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["vehicle_evidence.id"],
            name="fk_evidence_links_evidence_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["car_id"],
            ["cars.id"],
            name="fk_evidence_links_car_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_evidence_links_created_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "evidence_id",
            "subject_type",
            "subject_id",
            "relationship_type",
            name="uq_evidence_link_subject_relationship",
        ),
    )

    op.create_table(
        "evidence_extractions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("extraction_type", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_model", sa.String(length=120), nullable=True),
        sa.Column("provider_request_id", sa.String(length=160), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("result_ciphertext", sa.Text(), nullable=True),
        sa.Column("result_key_version", sa.String(length=64), nullable=True),
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column(
            "review_status",
            sa.String(length=24),
            nullable=False,
            server_default="unreviewed",
        ),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_reason_code", sa.String(length=64), nullable=True),
        sa.Column("reviewed_result_ciphertext", sa.Text(), nullable=True),
        sa.Column("reviewed_result_key_version", sa.String(length=64), nullable=True),
        sa.Column("reviewed_result_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["vehicle_evidence.id"],
            name="fk_evidence_extractions_evidence_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name="fk_evidence_extractions_reviewed_by_user_id",
            ondelete="SET NULL",
        ),
    )

    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_vehicle_evidence_type",
            "vehicle_evidence",
            f"evidence_type IN ({_quoted(EVIDENCE_TYPES)})",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_purpose",
            "vehicle_evidence",
            f"purpose IN ({_quoted(EVIDENCE_PURPOSES)})",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_source_channel",
            "vehicle_evidence",
            f"source_channel IN ({_quoted(SOURCE_CHANNELS)})",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_visibility",
            "vehicle_evidence",
            f"visibility IN ({_quoted(VISIBILITY)})",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_review_status",
            "vehicle_evidence",
            f"review_status IN ({_quoted(REVIEW_STATUSES)})",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_storage_state",
            "vehicle_evidence",
            f"storage_state IN ({_quoted(STORAGE_STATES)})",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_byte_size_positive",
            "vehicle_evidence",
            "byte_size > 0",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_sha256_length",
            "vehicle_evidence",
            "char_length(sha256) = 64",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_capture_time_source",
            "vehicle_evidence",
            "(captured_at IS NULL AND capture_time_source IS NULL) OR "
            f"(captured_at IS NOT NULL AND capture_time_source IN ({_quoted(CAPTURE_TIME_SOURCES)}))",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_review_metadata",
            "vehicle_evidence",
            "(review_status = 'pending_review' AND reviewed_by_user_id IS NULL AND reviewed_at IS NULL) OR "
            "(review_status IN ('accepted', 'rejected', 'superseded') AND reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL) OR "
            "review_status = 'deleted'",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_deleted_timestamp",
            "vehicle_evidence",
            "(review_status = 'deleted' AND deleted_at IS NOT NULL) OR "
            "(review_status <> 'deleted' AND deleted_at IS NULL)",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_storage_deleted_requires_logical_delete",
            "vehicle_evidence",
            "storage_state NOT IN ('delete_pending', 'deleted') OR review_status = 'deleted'",
        )

        op.create_check_constraint(
            "ck_evidence_links_subject_type",
            "evidence_links",
            f"subject_type IN ({_quoted(SUBJECT_TYPES)})",
        )
        op.create_check_constraint(
            "ck_evidence_links_relationship_type",
            "evidence_links",
            f"relationship_type IN ({_quoted(RELATIONSHIP_TYPES)})",
        )

        op.create_check_constraint(
            "ck_evidence_extractions_type",
            "evidence_extractions",
            f"extraction_type IN ({_quoted(EXTRACTION_TYPES)})",
        )
        op.create_check_constraint(
            "ck_evidence_extractions_status",
            "evidence_extractions",
            f"status IN ({_quoted(EXTRACTION_STATUSES)})",
        )
        op.create_check_constraint(
            "ck_evidence_extractions_review_status",
            "evidence_extractions",
            f"review_status IN ({_quoted(EXTRACTION_REVIEW_STATUSES)})",
        )
        op.create_check_constraint(
            "ck_evidence_extractions_confidence",
            "evidence_extractions",
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
        )
        op.create_check_constraint(
            "ck_evidence_extractions_completed_at",
            "evidence_extractions",
            "status <> 'completed' OR completed_at IS NOT NULL",
        )
        op.create_check_constraint(
            "ck_evidence_extractions_result_encryption_pair",
            "evidence_extractions",
            "(result_ciphertext IS NULL AND result_key_version IS NULL) OR "
            "(result_ciphertext IS NOT NULL AND result_key_version IS NOT NULL)",
        )
        op.create_check_constraint(
            "ck_evidence_extractions_review_metadata",
            "evidence_extractions",
            "(review_status = 'unreviewed' AND reviewed_by_user_id IS NULL AND reviewed_at IS NULL) OR "
            "(review_status IN ('accepted', 'rejected', 'corrected') AND reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL)",
        )
        op.create_check_constraint(
            "ck_evidence_extractions_corrected_payload",
            "evidence_extractions",
            "review_status <> 'corrected' OR "
            "(reviewed_result_ciphertext IS NOT NULL AND reviewed_result_key_version IS NOT NULL)",
        )
        op.create_check_constraint(
            "ck_evidence_extractions_reviewed_result_pair",
            "evidence_extractions",
            "(reviewed_result_ciphertext IS NULL AND reviewed_result_key_version IS NULL) OR "
            "(reviewed_result_ciphertext IS NOT NULL AND reviewed_result_key_version IS NOT NULL)",
        )

    op.create_index(
        "ix_vehicle_evidence_car_time",
        "vehicle_evidence",
        ["car_id", "uploaded_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_vehicle_evidence_car_sha256",
        "vehicle_evidence",
        ["car_id", "sha256"],
        unique=False,
    )
    op.create_index(
        "ix_vehicle_evidence_review_status",
        "vehicle_evidence",
        ["review_status", "uploaded_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_vehicle_evidence_storage_state",
        "vehicle_evidence",
        ["storage_state", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_links_car_subject",
        "evidence_links",
        ["car_id", "subject_type", "subject_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_extractions_evidence_time",
        "evidence_extractions",
        ["evidence_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_extractions_provider_request_id",
        "evidence_extractions",
        ["provider_request_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_evidence_extractions_provider_request_id",
        table_name="evidence_extractions",
    )
    op.drop_index(
        "ix_evidence_extractions_evidence_time",
        table_name="evidence_extractions",
    )
    op.drop_index("ix_evidence_links_car_subject", table_name="evidence_links")
    op.drop_index("ix_vehicle_evidence_storage_state", table_name="vehicle_evidence")
    op.drop_index("ix_vehicle_evidence_review_status", table_name="vehicle_evidence")
    op.drop_index("ix_vehicle_evidence_car_sha256", table_name="vehicle_evidence")
    op.drop_index("ix_vehicle_evidence_car_time", table_name="vehicle_evidence")

    if op.get_bind().dialect.name != "sqlite":
        for constraint_name in (
            "ck_evidence_extractions_reviewed_result_pair",
            "ck_evidence_extractions_corrected_payload",
            "ck_evidence_extractions_review_metadata",
            "ck_evidence_extractions_result_encryption_pair",
            "ck_evidence_extractions_completed_at",
            "ck_evidence_extractions_confidence",
            "ck_evidence_extractions_review_status",
            "ck_evidence_extractions_status",
            "ck_evidence_extractions_type",
        ):
            op.drop_constraint(
                constraint_name,
                "evidence_extractions",
                type_="check",
            )

        for constraint_name in (
            "ck_evidence_links_relationship_type",
            "ck_evidence_links_subject_type",
        ):
            op.drop_constraint(constraint_name, "evidence_links", type_="check")

        for constraint_name in (
            "ck_vehicle_evidence_storage_deleted_requires_logical_delete",
            "ck_vehicle_evidence_deleted_timestamp",
            "ck_vehicle_evidence_review_metadata",
            "ck_vehicle_evidence_capture_time_source",
            "ck_vehicle_evidence_sha256_length",
            "ck_vehicle_evidence_byte_size_positive",
            "ck_vehicle_evidence_storage_state",
            "ck_vehicle_evidence_review_status",
            "ck_vehicle_evidence_visibility",
            "ck_vehicle_evidence_source_channel",
            "ck_vehicle_evidence_purpose",
            "ck_vehicle_evidence_type",
        ):
            op.drop_constraint(constraint_name, "vehicle_evidence", type_="check")

    op.drop_table("evidence_extractions")
    op.drop_table("evidence_links")
    op.drop_table("vehicle_evidence")
