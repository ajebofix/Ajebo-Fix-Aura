"""add profile audit events

Revision ID: d42e7a1c9b50
Revises: c19f2a8b6d41
Create Date: 2026-07-26 03:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d42e7a1c9b50"
down_revision = "c19f2a8b6d41"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "profile_audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_profile_audit_events_user_id"),
        "profile_audit_events",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_profile_audit_events_request_id"),
        "profile_audit_events",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_profile_audit_events_created_at"),
        "profile_audit_events",
        ["created_at"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_profile_audit_events_created_at"),
        table_name="profile_audit_events",
    )
    op.drop_index(
        op.f("ix_profile_audit_events_request_id"),
        table_name="profile_audit_events",
    )
    op.drop_index(
        op.f("ix_profile_audit_events_user_id"),
        table_name="profile_audit_events",
    )
    op.drop_table("profile_audit_events")
