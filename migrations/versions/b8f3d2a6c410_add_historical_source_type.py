"""separate historical source type from evidence purpose

Revision ID: b8f3d2a6c410
Revises: a7e2c4f9b310
Create Date: 2026-09-20 20:56:00
"""

from alembic import op
import sqlalchemy as sa


revision = "b8f3d2a6c410"
down_revision = "a7e2c4f9b310"
branch_labels = None
depends_on = None


OLD_PURPOSES = (
    "concern_support",
    "consultation_support",
    "assessment_evidence",
    "treatment_evidence",
    "diagnostic_document",
    "service_document",
    "driver_observation",
)
NEW_PURPOSES = (
    "concern_support",
    "consultation_support",
    "assessment_evidence",
    "treatment_evidence",
    "diagnostic_document",
    "service_document",
    "vehicle_history_context",
    "driver_observation",
)
HISTORICAL_SOURCE_TYPES = (
    "standalone_document",
    "whatsapp_conversation",
    "instagram_conversation",
    "tiktok_conversation",
    "email_conversation",
    "sms_imessage_conversation",
    "other_conversation_archive",
)


def _quoted(values):
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    op.add_column(
        "vehicle_evidence",
        sa.Column("historical_source_type", sa.String(length=48), nullable=True),
    )
    op.create_index(
        "ix_vehicle_evidence_historical_source_type",
        "vehicle_evidence",
        ["historical_source_type", "car_id", "uploaded_at"],
        unique=False,
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE vehicle_evidence "
            "SET historical_source_type = 'whatsapp_conversation' "
            "WHERE source_channel = 'whatsapp' AND evidence_type = 'archive'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE vehicle_evidence "
            "SET historical_source_type = 'whatsapp_conversation' "
            "WHERE id IN (SELECT child_evidence_id FROM evidence_bundle_items)"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE vehicle_evidence "
            "SET historical_source_type = 'standalone_document' "
            "WHERE historical_source_type IS NULL "
            "AND evidence_type = 'document' "
            "AND source_channel = 'web' "
            "AND purpose IN ('service_document', 'diagnostic_document', 'treatment_evidence')"
        )
    )

    if bind.dialect.name != "sqlite":
        op.drop_constraint(
            "ck_vehicle_evidence_purpose",
            "vehicle_evidence",
            type_="check",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_purpose",
            "vehicle_evidence",
            f"purpose IN ({_quoted(NEW_PURPOSES)})",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_historical_source_type",
            "vehicle_evidence",
            "historical_source_type IS NULL OR "
            f"historical_source_type IN ({_quoted(HISTORICAL_SOURCE_TYPES)})",
        )


def downgrade():
    bind = op.get_bind()

    if bind.dialect.name != "sqlite":
        newer_purposes = bind.execute(
            sa.text(
                "SELECT count(*) FROM vehicle_evidence "
                "WHERE purpose = 'vehicle_history_context'"
            )
        ).scalar_one()
        if newer_purposes:
            raise RuntimeError(
                "Refusing to downgrade while vehicle_history_context evidence exists."
            )

        op.drop_constraint(
            "ck_vehicle_evidence_historical_source_type",
            "vehicle_evidence",
            type_="check",
        )
        op.drop_constraint(
            "ck_vehicle_evidence_purpose",
            "vehicle_evidence",
            type_="check",
        )
        op.create_check_constraint(
            "ck_vehicle_evidence_purpose",
            "vehicle_evidence",
            f"purpose IN ({_quoted(OLD_PURPOSES)})",
        )

    op.drop_index(
        "ix_vehicle_evidence_historical_source_type",
        table_name="vehicle_evidence",
    )
    op.drop_column("vehicle_evidence", "historical_source_type")
