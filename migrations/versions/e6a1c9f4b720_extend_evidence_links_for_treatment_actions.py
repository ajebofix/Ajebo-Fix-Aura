"""Extend governed EvidenceLink subjects for Wave 2.3C treatment records.

Revision ID: e6a1c9f4b720
Revises: d7c4b2a9e615
Create Date: 2026-09-01

This revision deliberately changes only the PostgreSQL evidence-link subject
contract. It does not synthesize links or treatment history.
"""

from alembic import op
import sqlalchemy as sa


revision = "e6a1c9f4b720"
down_revision = "d7c4b2a9e615"
branch_labels = None
depends_on = None


SUBJECT_TYPES_BEFORE = (
    "reported_concern",
    "consultation",
    "assessment",
    "treatment_plan",
    "vehicle_event",
)
SUBJECT_TYPES_AFTER = (
    "reported_concern",
    "consultation",
    "assessment",
    "treatment_plan",
    "treatment_action",
    "treatment_outcome",
    "vehicle_event",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    invalid = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM evidence_links "
            f"WHERE subject_type NOT IN ({_quoted(SUBJECT_TYPES_AFTER)})"
        )
    ).scalar_one()
    if invalid:
        raise RuntimeError(
            "Cannot extend Wave 2.3C EvidenceLink subjects: "
            f"found {invalid} incompatible evidence link row(s)"
        )

    op.drop_constraint(
        "ck_evidence_links_subject_type",
        "evidence_links",
        type_="check",
    )
    op.create_check_constraint(
        "ck_evidence_links_subject_type",
        "evidence_links",
        f"subject_type IN ({_quoted(SUBJECT_TYPES_AFTER)})",
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    new_subject_rows = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM evidence_links "
            "WHERE subject_type IN ('treatment_action', 'treatment_outcome')"
        )
    ).scalar_one()
    if new_subject_rows:
        raise RuntimeError(
            "Cannot downgrade Wave 2.3C evidence-link contract while "
            "Treatment Action/Outcome evidence history exists"
        )

    op.drop_constraint(
        "ck_evidence_links_subject_type",
        "evidence_links",
        type_="check",
    )
    op.create_check_constraint(
        "ck_evidence_links_subject_type",
        "evidence_links",
        f"subject_type IN ({_quoted(SUBJECT_TYPES_BEFORE)})",
    )
