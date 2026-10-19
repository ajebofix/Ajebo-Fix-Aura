"""Add immutable retrospective treatment-reconciliation attestations.

Revision ID: a6e4d9b2c731
Revises: 92b7e3c4d510
Create Date: 2026-10-19

Additive only. Does not modify existing treatment states, ownership,
invoice, payment, or reported concern records.
"""
from alembic import op
import sqlalchemy as sa

revision = "a6e4d9b2c731"
down_revision = "92b7e3c4d510"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "treatment_reconciliations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("treatment_plan_id", sa.Integer(), sa.ForeignKey("treatment_plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("car_id", sa.Integer(), sa.ForeignKey("cars.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("treatment_action_id", sa.Integer(), sa.ForeignKey("treatment_actions.id", ondelete="RESTRICT")),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("finding", sa.String(24), nullable=False),
        sa.Column("provenance", sa.String(24), nullable=False),
        sa.Column("occurred_on", sa.Date()),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("recipient", sa.String(200)),
        sa.Column("location", sa.String(240)),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("vehicle_evidence.id", ondelete="SET NULL")),
        sa.Column("operation_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('action', 'handover')", name="ck_treatment_reconcile_kind"),
        sa.CheckConstraint(
            "finding IN ('performed', 'not_performed', 'unverified', 'handed_over')",
            name="ck_treatment_reconcile_finding",
        ),
        sa.CheckConstraint(
            "provenance IN ('advisor_firsthand', 'reviewed_evidence', 'client_report')",
            name="ck_treatment_reconcile_provenance",
        ),
        sa.CheckConstraint(
            "(kind = 'action' AND treatment_action_id IS NOT NULL AND finding <> 'handed_over') "
            "OR (kind = 'handover' AND treatment_action_id IS NULL AND finding = 'handed_over')",
            name="ck_treatment_reconcile_scope",
        ),
        sa.CheckConstraint("length(trim(description)) > 0", name="ck_treatment_reconcile_description"),
        sa.CheckConstraint("length(trim(operation_key)) > 0", name="ck_treatment_reconcile_key"),
    )
    op.create_index("ix_treatment_reconciliations_treatment_plan_id", "treatment_reconciliations", ["treatment_plan_id"])
    op.create_index("ix_treatment_reconciliations_car_id", "treatment_reconciliations", ["car_id"])
    op.create_index("ix_treatment_reconciliations_treatment_action_id", "treatment_reconciliations", ["treatment_action_id"])


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM treatment_reconciliations")).scalar():
        raise RuntimeError("Reconciliation evidence exists; refusing destructive downgrade.")
    for field in ("treatment_action_id", "car_id", "treatment_plan_id"):
        op.drop_index(f"ix_treatment_reconciliations_{field}", table_name="treatment_reconciliations")
    op.drop_table("treatment_reconciliations")
