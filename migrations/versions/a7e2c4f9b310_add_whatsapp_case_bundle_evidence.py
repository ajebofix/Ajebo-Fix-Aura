"""add WhatsApp case bundle evidence lineage

Revision ID: a7e2c4f9b310
Revises: f4d1a6c8e250
Create Date: 2026-09-20 19:28:00
"""

from alembic import op
import sqlalchemy as sa


revision = "a7e2c4f9b310"
down_revision = "f4d1a6c8e250"
branch_labels = None
depends_on = None


OLD_EVIDENCE_TYPES = ("image", "document", "audio")
NEW_EVIDENCE_TYPES = ("image", "document", "audio", "video", "archive")

OLD_EXTRACTION_TYPES = (
    "image_observation",
    "document_text",
    "document_understanding",
    "transcription",
    "structured_fields",
)
NEW_EXTRACTION_TYPES = (
    "image_observation",
    "document_text",
    "document_understanding",
    "archive_manifest",
    "transcription",
    "structured_fields",
)

MEMBER_KINDS = ("transcript", "image", "document", "audio", "video")


def _quoted(values):
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    op.create_table(
        "evidence_bundle_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bundle_evidence_id", sa.Integer(), nullable=False),
        sa.Column("child_evidence_id", sa.Integer(), nullable=False),
        sa.Column("member_index", sa.Integer(), nullable=False),
        sa.Column("member_kind", sa.String(length=24), nullable=False),
        sa.Column("member_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["bundle_evidence_id"],
            ["vehicle_evidence.id"],
            name="fk_evidence_bundle_items_bundle_evidence_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["child_evidence_id"],
            ["vehicle_evidence.id"],
            name="fk_evidence_bundle_items_child_evidence_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "child_evidence_id",
            name="uq_evidence_bundle_items_child_evidence_id",
        ),
        sa.UniqueConstraint(
            "bundle_evidence_id",
            "member_index",
            name="uq_evidence_bundle_member_index",
        ),
    )

    op.create_index(
        "ix_evidence_bundle_items_bundle_kind",
        "evidence_bundle_items",
        ["bundle_evidence_id", "member_kind", "member_index"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_bundle_items_child_evidence_id",
        "evidence_bundle_items",
        ["child_evidence_id"],
        unique=False,
    )

    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_evidence_bundle_items_member_kind",
            "evidence_bundle_items",
            f"member_kind IN ({_quoted(MEMBER_KINDS)})",
        )
        op.create_check_constraint(
            "ck_evidence_bundle_items_member_index",
            "evidence_bundle_items",
            "member_index >= 0",
        )
        op.create_check_constraint(
            "ck_evidence_bundle_items_sha256_length",
            "evidence_bundle_items",
            "char_length(member_sha256) = 64",
        )
        op.create_check_constraint(
            "ck_evidence_bundle_items_not_self",
            "evidence_bundle_items",
            "bundle_evidence_id <> child_evidence_id",
        )

        op.drop_constraint(
            "ck_vehicle_evidence_type",
            "vehicle_evidence",
            type_="check",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_type",
            "vehicle_evidence",
            f"evidence_type IN ({_quoted(NEW_EVIDENCE_TYPES)})",
        )

        op.drop_constraint(
            "ck_evidence_extractions_type",
            "evidence_extractions",
            type_="check",
        )
        op.create_check_constraint(
            "ck_evidence_extractions_type",
            "evidence_extractions",
            f"extraction_type IN ({_quoted(NEW_EXTRACTION_TYPES)})",
        )


def downgrade():
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "ck_evidence_extractions_type",
            "evidence_extractions",
            type_="check",
        )
        op.create_check_constraint(
            "ck_evidence_extractions_type",
            "evidence_extractions",
            f"extraction_type IN ({_quoted(OLD_EXTRACTION_TYPES)})",
        )

        op.drop_constraint(
            "ck_vehicle_evidence_type",
            "vehicle_evidence",
            type_="check",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_type",
            "vehicle_evidence",
            f"evidence_type IN ({_quoted(OLD_EVIDENCE_TYPES)})",
        )

        for name in (
            "ck_evidence_bundle_items_not_self",
            "ck_evidence_bundle_items_sha256_length",
            "ck_evidence_bundle_items_member_index",
            "ck_evidence_bundle_items_member_kind",
        ):
            op.drop_constraint(name, "evidence_bundle_items", type_="check")

    op.drop_index(
        "ix_evidence_bundle_items_child_evidence_id",
        table_name="evidence_bundle_items",
    )
    op.drop_index(
        "ix_evidence_bundle_items_bundle_kind",
        table_name="evidence_bundle_items",
    )
    op.drop_table("evidence_bundle_items")
