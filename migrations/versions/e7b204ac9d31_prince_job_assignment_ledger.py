"""Record immutable principal-driven staff assignments.

Revision ID: e7b204ac9d31
Revises: d390ab6f021c

Preserves existing assignments without manufacturing old audit events.
"""
from alembic import op
import sqlalchemy as sa

revision = "e7b204ac9d31"
down_revision = "d390ab6f021c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prince_job_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("previous_user_id", sa.Integer(), nullable=False),
        sa.Column("new_user_id", sa.Integer(), nullable=False),
        sa.Column("changed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["prince_tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_assignment_job_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "new_user_id"],
            ["prince_memberships.tenant_id", "prince_memberships.user_id"],
            name="fk_prince_assignment_member_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_user_id"], ["users.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"], ["users.id"], ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "previous_user_id <> new_user_id",
            name="ck_prince_assignment_different_users",
        ),
    )
    op.create_index(
        "ix_prince_job_assignments_tenant_id",
        "prince_job_assignments", ["tenant_id"],
    )
    op.create_index(
        "ix_prince_job_assignments_job_id",
        "prince_job_assignments", ["job_id"],
    )


def downgrade():
    connection = op.get_bind()
    count = connection.execute(
        sa.text("SELECT COUNT(*) FROM prince_job_assignments")
    ).scalar()
    if count:
        raise RuntimeError(
            "Assignment history exists; refusing destructive downgrade."
        )
    op.drop_index(
        "ix_prince_job_assignments_job_id",
        table_name="prince_job_assignments",
    )
    op.drop_index(
        "ix_prince_job_assignments_tenant_id",
        table_name="prince_job_assignments",
    )
    op.drop_table("prince_job_assignments")
