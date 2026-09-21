"""allow historical episode reconciliation extraction

Revision ID: d0f5b8c3e620
Revises: c9e4a7b2d510
Create Date: 2026-09-21 15:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "d0f5b8c3e620"
down_revision = "c9e4a7b2d510"
branch_labels = None
depends_on = None


OLD_EXTRACTION_TYPES = (
    "image_observation",
    "document_text",
    "document_understanding",
    "archive_manifest",
    "transcription",
    "structured_fields",
    "historical_case_attribution",
)
NEW_EXTRACTION_TYPES = (
    *OLD_EXTRACTION_TYPES,
    "historical_reconciliation",
)


def _quoted(values):
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

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
    bind = op.get_bind()

    reconciliation_count = bind.execute(
        sa.text(
            "SELECT count(*) FROM evidence_extractions "
            "WHERE extraction_type = 'historical_reconciliation'"
        )
    ).scalar_one()
    if reconciliation_count:
        raise RuntimeError(
            "Refusing to downgrade while historical reconciliation data exists."
        )

    if bind.dialect.name == "sqlite":
        return

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
