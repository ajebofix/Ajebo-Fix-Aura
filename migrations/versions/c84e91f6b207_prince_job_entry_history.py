"""Add source-labelled, append-only Prince workshop job entries.

Revision ID: c84e91f6b207
Revises: b71e5ca90d24

No legacy job text is silently reclassified or deleted. Previous
reported_concern remains available as an explicitly unclassified note.
"""
from alembic import op
import sqlalchemy as sa

revision = "c84e91f6b207"
down_revision = "b71e5ca90d24"
branch_labels = None
depends_on = None


def upgrade():
    # Needed for an enforced tenant-safe composite foreign key.
    with op.batch_alter_table("prince_jobs") as batch:
        batch.create_unique_constraint(
            "uq_prince_job_id_tenant", ["id", "tenant_id"]
        )
    op.create_table(
        "prince_job_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=36), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("review_state", sa.String(length=16), nullable=False),
        sa.Column("recorded_by_user_id", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["prince_tenants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_entry_job_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "category IN ('customer_complaint', 'technician_observation', "
            "'diagnostic_finding', 'temporary_intervention', 'reported_work', "
            "'outstanding_concern', 'next_action')",
            name="ck_prince_entry_category",
        ),
        sa.CheckConstraint(
            "review_state = 'unverified'",
            name="ck_prince_entry_review_state",
        ),
    )
    op.create_index("ix_prince_job_entries_tenant_id", "prince_job_entries", ["tenant_id"])
    op.create_index("ix_prince_job_entries_job_id", "prince_job_entries", ["job_id"])


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM prince_job_entries")).scalar():
        raise RuntimeError("Prince job history exists: refusing destructive downgrade.")
    op.drop_index("ix_prince_job_entries_job_id", table_name="prince_job_entries")
    op.drop_index("ix_prince_job_entries_tenant_id", table_name="prince_job_entries")
    op.drop_table("prince_job_entries")
    with op.batch_alter_table("prince_jobs") as batch:
        batch.drop_constraint("uq_prince_job_id_tenant", type_="unique")
