"""historical record ingestion and completed-work provenance

Revision ID: e1a7c9d4b620
Revises: a4c9e7b2d610
Create Date: 2026-09-19 20:40:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e1a7c9d4b620"
down_revision = "a4c9e7b2d610"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("treatment_plans") as batch_op:
        batch_op.add_column(
            sa.Column(
                "record_origin",
                sa.String(length=40),
                nullable=False,
                server_default="live",
            )
        )
        batch_op.add_column(
            sa.Column("source_evidence_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("source_extraction_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_treatment_plans_source_evidence_id",
            "vehicle_evidence",
            ["source_evidence_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_treatment_plans_source_extraction_id",
            "evidence_extractions",
            ["source_extraction_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_treatment_plans_source_extraction_id",
            ["source_extraction_id"],
        )

    op.create_table(
        "treatment_action_completion_details",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("treatment_action_id", sa.Integer(), nullable=False),
        sa.Column("action_kind", sa.String(length=40), nullable=False),
        sa.Column("component_name", sa.String(length=255), nullable=True),
        sa.Column("component_location", sa.String(length=120), nullable=True),
        sa.Column(
            "component_condition",
            sa.String(length=40),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("odometer_km", sa.Integer(), nullable=True),
        sa.Column("source_evidence_id", sa.Integer(), nullable=True),
        sa.Column(
            "verification_status",
            sa.String(length=40),
            nullable=False,
            server_default="advisor_confirmed",
        ),
        sa.Column("verified_by_user_id", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "action_kind IN ('service', 'component_replacement', 'other_intervention')",
            name="ck_treatment_action_completion_kind",
        ),
        sa.CheckConstraint(
            "component_condition IN ('new', 'preowned_tokunbo', 'refurbished', 'client_supplied', 'unknown', 'not_applicable')",
            name="ck_treatment_action_completion_condition",
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity > 0",
            name="ck_treatment_action_completion_quantity",
        ),
        sa.CheckConstraint(
            "odometer_km IS NULL OR odometer_km >= 0",
            name="ck_treatment_action_completion_odometer",
        ),
        sa.ForeignKeyConstraint(
            ["source_evidence_id"],
            ["vehicle_evidence.id"],
            name="fk_treatment_action_completion_source_evidence_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["treatment_action_id"],
            ["treatment_actions.id"],
            name="fk_treatment_action_completion_action_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"],
            ["users.id"],
            name="fk_treatment_action_completion_verified_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "treatment_action_id",
            name="uq_treatment_action_completion_action_id",
        ),
    )
    op.create_index(
        "ix_treatment_action_completion_details_treatment_action_id",
        "treatment_action_completion_details",
        ["treatment_action_id"],
        unique=True,
    )
    op.create_index(
        "ix_treatment_action_completion_details_source_evidence_id",
        "treatment_action_completion_details",
        ["source_evidence_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_treatment_action_completion_details_source_evidence_id",
        table_name="treatment_action_completion_details",
    )
    op.drop_index(
        "ix_treatment_action_completion_details_treatment_action_id",
        table_name="treatment_action_completion_details",
    )
    op.drop_table("treatment_action_completion_details")

    with op.batch_alter_table("treatment_plans") as batch_op:
        batch_op.drop_constraint(
            "uq_treatment_plans_source_extraction_id",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_treatment_plans_source_extraction_id",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_treatment_plans_source_evidence_id",
            type_="foreignkey",
        )
        batch_op.drop_column("source_extraction_id")
        batch_op.drop_column("source_evidence_id")
        batch_op.drop_column("record_origin")
