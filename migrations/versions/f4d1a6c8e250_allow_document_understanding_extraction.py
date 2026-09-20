"""allow document understanding evidence extraction type

Revision ID: f4d1a6c8e250
Revises: e1a7c9d4b620
Create Date: 2026-09-20 16:52:00
"""

from alembic import op


revision = "f4d1a6c8e250"
down_revision = "e1a7c9d4b620"
branch_labels = None
depends_on = None


OLD_TYPES = (
    "image_observation",
    "document_text",
    "transcription",
    "structured_fields",
)

NEW_TYPES = (
    "image_observation",
    "document_text",
    "document_understanding",
    "transcription",
    "structured_fields",
)


def _quoted(values):
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        return

    op.drop_constraint(
        "ck_evidence_extractions_type",
        "evidence_extractions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_evidence_extractions_type",
        "evidence_extractions",
        f"extraction_type IN ({_quoted(NEW_TYPES)})",
    )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        return

    op.drop_constraint(
        "ck_evidence_extractions_type",
        "evidence_extractions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_evidence_extractions_type",
        "evidence_extractions",
        f"extraction_type IN ({_quoted(OLD_TYPES)})",
    )
