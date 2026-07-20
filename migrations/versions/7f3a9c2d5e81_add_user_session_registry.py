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


def _index_names(inspector):
    return {
        item["name"]
        for item in inspector.get_indexes("user_sessions")
        if item.get("name")
    }


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "user_sessions" not in set(inspector.get_table_names()):
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
        inspector = sa.inspect(bind)

    indexes = _index_names(inspector)
    required_indexes = {
        op.f("ix_user_sessions_user_id"): (["user_id"], False),
        op.f("ix_user_sessions_token_hash"): (["token_hash"], True),
        op.f("ix_user_sessions_expires_at"): (["expires_at"], False),
        op.f("ix_user_sessions_revoked_at"): (["revoked_at"], False),
    }

    for index_name, (columns, unique) in required_indexes.items():
        if index_name not in indexes:
            op.create_index(
                index_name,
                "user_sessions",
                columns,
                unique=unique,
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "user_sessions" not in set(inspector.get_table_names()):
        return

    indexes = _index_names(inspector)
    for index_name in (
        op.f("ix_user_sessions_revoked_at"),
        op.f("ix_user_sessions_expires_at"),
        op.f("ix_user_sessions_token_hash"),
        op.f("ix_user_sessions_user_id"),
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="user_sessions")

    op.drop_table("user_sessions")
