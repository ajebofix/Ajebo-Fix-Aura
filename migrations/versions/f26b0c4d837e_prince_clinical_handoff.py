"""Add isolated Prince clinical handoff candidates and withdrawals.

Revision ID: f26b0c4d837e
Revises: e15a9b3c726d
"""
from alembic import op
import sqlalchemy as sa

revision = "f26b0c4d837e"
down_revision = "e15a9b3c726d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prince_clinical_handoffs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("consent_channel", sa.String(24), nullable=False),
        sa.Column("consent_reference", sa.Text(), nullable=False),
        sa.Column("consent_notice_version", sa.String(48), nullable=False),
        sa.Column("recorded_by_user_id", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["prince_tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_handoff_job_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("job_id", name="uq_prince_handoff_job"),
        sa.CheckConstraint("consent_channel IN ('written_message', 'signed_document')",
                           name="ck_prince_handoff_consent_channel"),
    )
    op.create_index("ix_prince_clinical_handoffs_tenant_id", "prince_clinical_handoffs", ["tenant_id"])
    op.create_index("ix_prince_clinical_handoffs_job_id", "prince_clinical_handoffs", ["job_id"])
    op.create_table(
        "prince_clinical_withdrawals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("handoff_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("recorded_by_user_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["handoff_id"], ["prince_clinical_handoffs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"], ondelete="RESTRICT"),
    )


def downgrade():
    connection = op.get_bind()
    if (connection.execute(sa.text("SELECT COUNT(*) FROM prince_clinical_handoffs")).scalar()
            or connection.execute(sa.text("SELECT COUNT(*) FROM prince_clinical_withdrawals")).scalar()):
        raise RuntimeError("Clinical handoff history exists; refusing destructive downgrade.")
    op.drop_table("prince_clinical_withdrawals")
    op.drop_index("ix_prince_clinical_handoffs_job_id", table_name="prince_clinical_handoffs")
    op.drop_index("ix_prince_clinical_handoffs_tenant_id", table_name="prince_clinical_handoffs")
    op.drop_table("prince_clinical_handoffs")
