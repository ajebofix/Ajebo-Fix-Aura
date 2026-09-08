"""add mileage report review state

Revision ID: b2d0f5e8c731
Revises: a1c9e4d7b620
Create Date: 2026-09-08
"""

from alembic import op
import sqlalchemy as sa


revision = "b2d0f5e8c731"
down_revision = "a1c9e4d7b620"
branch_labels = None
depends_on = None


REVIEW_STATUS_CHECK = (
    "review_status IN ('not_required', 'pending', 'accepted', 'rejected')"
)


def upgrade():
    op.add_column(
        "mileage_observations",
        sa.Column(
            "review_status",
            sa.String(length=20),
            nullable=False,
            server_default="not_required",
        ),
    )
    op.add_column(
        "mileage_observations",
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "mileage_observations",
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "mileage_observations",
        sa.Column("review_note", sa.Text(), nullable=True),
    )

    op.create_foreign_key(
        "fk_mileage_observations_reviewed_by_user_id_users",
        "mileage_observations",
        "users",
        ["reviewed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_mileage_observation_review_status",
        "mileage_observations",
        REVIEW_STATUS_CHECK,
    )
    op.create_index(
        "idx_mileage_observation_review_status",
        "mileage_observations",
        ["review_status"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "idx_mileage_observation_review_status",
        table_name="mileage_observations",
    )
    op.drop_constraint(
        "ck_mileage_observation_review_status",
        "mileage_observations",
        type_="check",
    )
    op.drop_constraint(
        "fk_mileage_observations_reviewed_by_user_id_users",
        "mileage_observations",
        type_="foreignkey",
    )
    op.drop_column("mileage_observations", "review_note")
    op.drop_column("mileage_observations", "reviewed_at")
    op.drop_column("mileage_observations", "reviewed_by_user_id")
    op.drop_column("mileage_observations", "review_status")
