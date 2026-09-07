"""add mileage observations

Revision ID: f0a3b6c2d901
Revises: c5f3e2d9a841
Create Date: 2026-09-07
"""

from alembic import op
import sqlalchemy as sa


revision = "f0a3b6c2d901"
down_revision = "c5f3e2d9a841"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mileage_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("ownership_id", sa.Integer(), nullable=True),
        sa.Column("odometer_km", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("verification_status", sa.String(length=40), nullable=False),
        sa.Column("recorded_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "is_historical",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("source_reference", sa.String(length=128), nullable=True),
        sa.Column("evidence_reference", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "odometer_km >= 0 AND odometer_km <= 5000000",
            name="ck_mileage_observation_range",
        ),
        sa.ForeignKeyConstraint(
            ["car_id"],
            ["cars.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ownership_id"],
            ["car_ownerships.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "car_id",
            "source",
            "source_reference",
            name="uq_mileage_observation_source_reference",
        ),
    )
    op.create_index(
        "idx_mileage_observation_car_observed",
        "mileage_observations",
        ["car_id", "observed_at"],
        unique=False,
    )
    op.create_index(
        "idx_mileage_observation_source",
        "mileage_observations",
        ["source"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "idx_mileage_observation_source",
        table_name="mileage_observations",
    )
    op.drop_index(
        "idx_mileage_observation_car_observed",
        table_name="mileage_observations",
    )
    op.drop_table("mileage_observations")
