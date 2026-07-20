"""add user session registry

Revision ID: 7f3a9c2d5e81
Revises: 4d2b9e7a1c60
Create Date: 2026-07-20 19:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "7f3a9c2d5e81"
down_revision = "4d2b9e7a1c60"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("password_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("device_label", sa.String(length=120), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_reason", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_user_sessions_user_id"),
        "user_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_sessions_token_hash"),
        "user_sessions",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_user_sessions_expires_at"),
        "user_sessions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_sessions_revoked_at"),
        "user_sessions",
        ["revoked_at"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_user_sessions_revoked_at"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_expires_at"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_token_hash"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_table("user_sessions")
