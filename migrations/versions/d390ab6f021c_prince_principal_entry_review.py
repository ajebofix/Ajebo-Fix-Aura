"""Add principal work attestations without granting operator verification.

Revision ID: d390ab6f021c
Revises: c84e91f6b207
"""
from alembic import op
import sqlalchemy as sa

revision = "d390ab6f021c"
down_revision = "c84e91f6b207"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("prince_job_entries") as batch:
        batch.create_unique_constraint(
            "uq_prince_entry_id_tenant", ["id", "tenant_id"]
        )
    op.create_table(
        "prince_entry_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("basis", sa.String(36), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["prince_tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_review_job_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["entry_id", "tenant_id"],
            ["prince_job_entries.id", "prince_job_entries.tenant_id"],
            name="fk_prince_review_entry_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"], ["users.id"], ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "decision IN ('acknowledged', 'needs_evidence', 'work_confirmed', 'not_confirmed')",
            name="ck_prince_review_decision",
        ),
        sa.CheckConstraint(
            "basis IN ('direct_inspection', 'documented_evidence', "
            "'technician_demonstration', 'not_applicable')",
            name="ck_prince_review_basis",
        ),
        sa.UniqueConstraint("entry_id", name="uq_prince_single_entry_review"),
    )
    op.create_index("ix_prince_entry_reviews_tenant_id", "prince_entry_reviews", ["tenant_id"])
    op.create_index("ix_prince_entry_reviews_entry_id", "prince_entry_reviews", ["entry_id"])


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM prince_entry_reviews")).scalar():
        raise RuntimeError("Prince principal decisions exist: refusing destructive downgrade.")
    op.drop_index("ix_prince_entry_reviews_entry_id", table_name="prince_entry_reviews")
    op.drop_index("ix_prince_entry_reviews_tenant_id", table_name="prince_entry_reviews")
    op.drop_table("prince_entry_reviews")
    with op.batch_alter_table("prince_job_entries") as batch:
        batch.drop_constraint("uq_prince_entry_id_tenant", type_="unique")
