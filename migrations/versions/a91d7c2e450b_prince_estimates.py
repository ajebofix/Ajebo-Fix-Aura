"""Add versioned Prince draft estimates and internal approval.

Revision ID: a91d7c2e450b
Revises: f12c9e48a620
"""
from alembic import op
import sqlalchemy as sa

revision = "a91d7c2e450b"
down_revision = "f12c9e48a620"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prince_estimates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("subtotal_kobo", sa.BigInteger(), nullable=False),
        sa.Column("discount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("total_kobo", sa.BigInteger(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer()),
        sa.Column("approved_at", sa.DateTime()),
        sa.Column("approval_note", sa.Text()),
        sa.ForeignKeyConstraint(["tenant_id"], ["prince_tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
                                name="fk_prince_estimate_job_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("job_id", "version", name="uq_prince_estimate_job_version"),
        sa.CheckConstraint("version > 0", name="ck_prince_estimate_version"),
        sa.CheckConstraint(
            "subtotal_kobo >= 0 AND discount_kobo >= 0 AND "
            "discount_kobo <= subtotal_kobo AND total_kobo = subtotal_kobo - discount_kobo",
            name="ck_prince_estimate_amounts",
        ),
        sa.CheckConstraint(
            "(approved_by_user_id IS NULL AND approved_at IS NULL AND approval_note IS NULL) OR "
            "(approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL AND approval_note IS NOT NULL)",
            name="ck_prince_estimate_approval",
        ),
    )
    op.create_index("ix_prince_estimates_tenant_id", "prince_estimates", ["tenant_id"])
    op.create_index("ix_prince_estimates_job_id", "prince_estimates", ["job_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM prince_estimates")).scalar():
        raise RuntimeError("Estimate history exists; refusing destructive downgrade.")
    op.drop_index("ix_prince_estimates_job_id", table_name="prince_estimates")
    op.drop_index("ix_prince_estimates_tenant_id", table_name="prince_estimates")
    op.drop_table("prince_estimates")
