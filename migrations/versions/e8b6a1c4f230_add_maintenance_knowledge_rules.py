"""add maintenance knowledge rules

Revision ID: e8b6a1c4f230
Revises: d7a4c2e9f610
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa


revision = "e8b6a1c4f230"
down_revision = "d7a4c2e9f610"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "maintenance_knowledge_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("maintenance_item_key", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=150), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=True),
        sa.Column("brand", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("year_start", sa.Integer(), nullable=True),
        sa.Column("year_end", sa.Integer(), nullable=True),
        sa.Column("trim", sa.String(length=120), nullable=True),
        sa.Column("engine_type", sa.String(length=120), nullable=True),
        sa.Column("fuel_type", sa.String(length=80), nullable=True),
        sa.Column("transmission", sa.String(length=120), nullable=True),
        sa.Column("drive_type", sa.String(length=80), nullable=True),
        sa.Column("interval_km", sa.Integer(), nullable=True),
        sa.Column("interval_months", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_name", sa.String(length=150), nullable=False),
        sa.Column("source_reference", sa.String(length=500), nullable=False),
        sa.Column("source_version", sa.String(length=120), nullable=True),
        sa.Column(
            "verification_status",
            sa.String(length=30),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column("verified_by", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "verification_status IN "
            "('unverified', 'source_verified', 'advisor_verified', "
            "'superseded', 'rejected')",
            name="ck_maintenance_knowledge_verification_status",
        ),
        sa.CheckConstraint(
            "source_type IN "
            "('oem', 'advisor_reference', 'provider', 'manual_reference', 'test')",
            name="ck_maintenance_knowledge_source_type",
        ),
        sa.CheckConstraint(
            "interval_km IS NOT NULL OR interval_months IS NOT NULL",
            name="ck_maintenance_knowledge_has_interval",
        ),
        sa.CheckConstraint(
            "interval_km IS NULL OR interval_km > 0",
            name="ck_maintenance_knowledge_interval_km_positive",
        ),
        sa.CheckConstraint(
            "interval_months IS NULL OR interval_months > 0",
            name="ck_maintenance_knowledge_interval_months_positive",
        ),
        sa.CheckConstraint(
            "year_start IS NULL OR year_end IS NULL OR year_start <= year_end",
            name="ck_maintenance_knowledge_year_range",
        ),
        sa.CheckConstraint(
            "car_id IS NOT NULL OR brand IS NOT NULL",
            name="ck_maintenance_knowledge_has_applicability",
        ),
        sa.CheckConstraint(
            "verification_status = 'unverified' OR "
            "(verified_by IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_maintenance_knowledge_review_metadata",
        ),
        sa.ForeignKeyConstraint(
            ["car_id"],
            ["cars.id"],
            name="fk_maintenance_knowledge_rules_car_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"],
            ["users.id"],
            name="fk_maintenance_knowledge_rules_verified_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["maintenance_knowledge_rules.id"],
            name="fk_maintenance_knowledge_rules_superseded_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "fingerprint",
            name="uq_maintenance_knowledge_rules_fingerprint",
        ),
    )
    op.create_index(
        "ix_maintenance_knowledge_item_status",
        "maintenance_knowledge_rules",
        ["maintenance_item_key", "verification_status"],
        unique=False,
    )
    op.create_index(
        "ix_maintenance_knowledge_vehicle_override",
        "maintenance_knowledge_rules",
        ["car_id", "verification_status"],
        unique=False,
    )
    op.create_index(
        "ix_maintenance_knowledge_applicability",
        "maintenance_knowledge_rules",
        ["brand", "model", "year_start", "year_end"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_maintenance_knowledge_applicability",
        table_name="maintenance_knowledge_rules",
    )
    op.drop_index(
        "ix_maintenance_knowledge_vehicle_override",
        table_name="maintenance_knowledge_rules",
    )
    op.drop_index(
        "ix_maintenance_knowledge_item_status",
        table_name="maintenance_knowledge_rules",
    )
    op.drop_table("maintenance_knowledge_rules")
