"""add maintenance service classifications

Revision ID: f9c7d2b5a341
Revises: e8b6a1c4f230
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa


revision = "f9c7d2b5a341"
down_revision = "e8b6a1c4f230"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "maintenance_service_classifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_event_id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("maintenance_item_key", sa.String(length=100), nullable=False),
        sa.Column("knowledge_rule_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("classification_source", sa.String(length=40), nullable=False),
        sa.Column("classified_by", sa.Integer(), nullable=False),
        sa.Column("classified_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'removed')",
            name="ck_maintenance_service_classification_status",
        ),
        sa.CheckConstraint(
            "classification_source IN ('advisor_service_entry', 'advisor_review')",
            name="ck_maintenance_service_classification_source",
        ),
        sa.ForeignKeyConstraint(
            ["service_event_id"],
            ["vehicle_events.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["car_id"],
            ["cars.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_rule_id"],
            ["maintenance_knowledge_rules.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["classified_by"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["maintenance_service_classifications.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_maintenance_service_classification_event_status",
        "maintenance_service_classifications",
        ["service_event_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_maintenance_service_classification_car_item",
        "maintenance_service_classifications",
        ["car_id", "maintenance_item_key", "status"],
        unique=False,
    )
    op.create_index(
        "uq_maintenance_service_classification_active_event",
        "maintenance_service_classifications",
        ["service_event_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade():
    op.drop_index(
        "uq_maintenance_service_classification_active_event",
        table_name="maintenance_service_classifications",
    )
    op.drop_index(
        "ix_maintenance_service_classification_car_item",
        table_name="maintenance_service_classifications",
    )
    op.drop_index(
        "ix_maintenance_service_classification_event_status",
        table_name="maintenance_service_classifications",
    )
    op.drop_table("maintenance_service_classifications")
