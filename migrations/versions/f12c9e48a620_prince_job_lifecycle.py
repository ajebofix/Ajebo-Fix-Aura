"""Add principal-controlled Prince job lifecycle audit.

Revision ID: f12c9e48a620
Revises: e7b204ac9d31
"""
from alembic import op
import sqlalchemy as sa

revision = "f12c9e48a620"
down_revision = "e7b204ac9d31"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prince_job_transitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("previous_status", sa.String(24), nullable=False),
        sa.Column("new_status", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("closure_disposition", sa.String(32)),
        sa.Column("follow_up_plan", sa.Text()),
        sa.Column("changed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["prince_tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_transition_job_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "(previous_status = 'draft' AND new_status = 'awaiting_approval') OR "
            "(previous_status = 'awaiting_approval' AND new_status = 'approved') OR "
            "(previous_status = 'approved' AND new_status = 'in_progress') OR "
            "(previous_status = 'in_progress' AND new_status = 'closed')",
            name="ck_prince_transition_path",
        ),
        sa.CheckConstraint(
            "(new_status = 'closed' AND closure_disposition IS NOT NULL AND "
            "closure_disposition IN ('follow_up_required', 'no_follow_up_identified')) OR "
            "(new_status <> 'closed' AND closure_disposition IS NULL)",
            name="ck_prince_transition_closure",
        ),
        sa.CheckConstraint(
            "(closure_disposition = 'follow_up_required' AND follow_up_plan IS NOT NULL) OR "
            "((closure_disposition IS NULL OR closure_disposition <> 'follow_up_required') "
            "AND follow_up_plan IS NULL)",
            name="ck_prince_transition_follow_up",
        ),
        sa.UniqueConstraint("job_id", "new_status", name="uq_prince_transition_job_stage"),
    )
    op.create_index("ix_prince_job_transitions_tenant_id", "prince_job_transitions", ["tenant_id"])
    op.create_index("ix_prince_job_transitions_job_id", "prince_job_transitions", ["job_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM prince_job_transitions")).scalar():
        raise RuntimeError("Job lifecycle history exists; refusing destructive downgrade.")
    op.drop_index("ix_prince_job_transitions_job_id", table_name="prince_job_transitions")
    op.drop_index("ix_prince_job_transitions_tenant_id", table_name="prince_job_transitions")
    op.drop_table("prince_job_transitions")
