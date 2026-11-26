"""Add synthetic Prince payment reports and review states.

Revision ID: d04f8a2b615c
Revises: c93e1f7a204b
"""
from alembic import op
import sqlalchemy as sa

revision = "d04f8a2b615c"
down_revision = "c93e1f7a204b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prince_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("method", sa.String(12), nullable=False),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("reported_by_user_id", sa.Integer(), nullable=False),
        sa.Column("reported_at", sa.DateTime(), nullable=False),
        sa.Column("decision", sa.String(12)),
        sa.Column("reviewed_by_user_id", sa.Integer()),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("review_note", sa.Text()),
        sa.ForeignKeyConstraint(["tenant_id"], ["prince_tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_payment_job_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["prince_invoices.job_id"],
                                name="fk_prince_payment_invoice_job", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reported_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "reference", name="uq_prince_payment_reference"),
        sa.CheckConstraint("amount_kobo > 0", name="ck_prince_payment_amount"),
        sa.CheckConstraint("method IN ('transfer', 'cash')", name="ck_prince_payment_method"),
        sa.CheckConstraint(
            "(decision IS NULL AND reviewed_by_user_id IS NULL AND reviewed_at IS NULL AND review_note IS NULL) OR "
            "(decision IS NOT NULL AND decision IN ('verified', 'rejected') AND "
            "reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL AND review_note IS NOT NULL)",
            name="ck_prince_payment_review",
        ),
    )
    op.create_index("ix_prince_payments_tenant_id", "prince_payments", ["tenant_id"])
    op.create_index("ix_prince_payments_job_id", "prince_payments", ["job_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM prince_payments")).scalar():
        raise RuntimeError("Payment history exists; refusing destructive downgrade.")
    op.drop_index("ix_prince_payments_job_id", table_name="prince_payments")
    op.drop_index("ix_prince_payments_tenant_id", table_name="prince_payments")
    op.drop_table("prince_payments")
