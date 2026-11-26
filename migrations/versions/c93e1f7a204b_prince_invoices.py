"""Add synthetic Prince invoice snapshots.

Revision ID: c93e1f7a204b
Revises: b82d7f6a109c
"""
from alembic import op
import sqlalchemy as sa

revision = "c93e1f7a204b"
down_revision = "b82d7f6a109c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prince_invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("estimate_version", sa.Integer(), nullable=False),
        sa.Column("authorization_sequence", sa.Integer(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("subtotal_kobo", sa.BigInteger(), nullable=False),
        sa.Column("discount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("total_kobo", sa.BigInteger(), nullable=False),
        sa.Column("issued_by_user_id", sa.Integer(), nullable=False),
        sa.Column("issue_reason", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["prince_tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_invoice_job_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "estimate_version", "authorization_sequence"],
            ["prince_customer_authorizations.job_id",
             "prince_customer_authorizations.estimate_version",
             "prince_customer_authorizations.sequence"],
            name="fk_prince_invoice_authorization", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["issued_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("job_id", name="uq_prince_invoice_job"),
        sa.CheckConstraint("estimate_version > 0 AND authorization_sequence > 0",
                           name="ck_prince_invoice_source"),
        sa.CheckConstraint(
            "subtotal_kobo >= 0 AND discount_kobo >= 0 AND discount_kobo <= subtotal_kobo "
            "AND total_kobo = subtotal_kobo - discount_kobo",
            name="ck_prince_invoice_totals",
        ),
    )
    op.create_index("ix_prince_invoices_tenant_id", "prince_invoices", ["tenant_id"])
    op.create_index("ix_prince_invoices_job_id", "prince_invoices", ["job_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM prince_invoices")).scalar():
        raise RuntimeError("Invoice history exists; refusing destructive downgrade.")
    op.drop_index("ix_prince_invoices_job_id", table_name="prince_invoices")
    op.drop_index("ix_prince_invoices_tenant_id", table_name="prince_invoices")
    op.drop_table("prince_invoices")
