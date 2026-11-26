"""Record principal-attested customer authorization events.

Revision ID: b82d7f6a109c
Revises: a91d7c2e450b
"""
from alembic import op
import sqlalchemy as sa

revision = "b82d7f6a109c"
down_revision = "a91d7c2e450b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prince_customer_authorizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("estimate_version", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("evidence_reference", sa.Text(), nullable=False),
        sa.Column("total_kobo", sa.BigInteger(), nullable=False),
        sa.Column("recorded_by_user_id", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["prince_tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_authorization_job_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "estimate_version"], ["prince_estimates.job_id", "prince_estimates.version"],
            name="fk_prince_authorization_estimate_version", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("job_id", "estimate_version", "sequence",
                            name="uq_prince_authorization_sequence"),
        sa.CheckConstraint("sequence > 0 AND total_kobo >= 0",
                           name="ck_prince_authorization_amount_sequence"),
        sa.CheckConstraint("decision IN ('accepted', 'declined', 'withdrawn')",
                           name="ck_prince_authorization_decision"),
        sa.CheckConstraint("channel IN ('written_message', 'signed_document', 'phone', 'in_person')",
                           name="ck_prince_authorization_channel"),
    )
    op.create_index("ix_prince_customer_authorizations_tenant_id",
                    "prince_customer_authorizations", ["tenant_id"])
    op.create_index("ix_prince_customer_authorizations_job_id",
                    "prince_customer_authorizations", ["job_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM prince_customer_authorizations")).scalar():
        raise RuntimeError("Customer authorization history exists; refusing destructive downgrade.")
    op.drop_index("ix_prince_customer_authorizations_job_id",
                  table_name="prince_customer_authorizations")
    op.drop_index("ix_prince_customer_authorizations_tenant_id",
                  table_name="prince_customer_authorizations")
    op.drop_table("prince_customer_authorizations")
