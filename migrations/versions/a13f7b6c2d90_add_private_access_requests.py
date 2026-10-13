"""add private access enquiries for public Aura homepage

Revision ID: a13f7b6c2d90
Revises: e1f6c9d4a730
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa


revision = "a13f7b6c2d90"
down_revision = "e1f6c9d4a730"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "private_access_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("vehicle", sa.String(length=120), nullable=True),
        sa.Column("request_type", sa.String(length=20), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("consented_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("request_type IN ('private_access', 'assessment', 'fleet')", name="ck_private_access_request_type"),
        sa.CheckConstraint("status IN ('pending', 'contacted', 'closed')", name="ck_private_access_request_status"),
    )
    op.create_index("ix_private_access_requests_email", "private_access_requests", ["email"])
    op.create_index("ix_private_access_requests_created_at", "private_access_requests", ["created_at"])


def downgrade():
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT count(*) FROM private_access_requests")).scalar_one()
    if count:
        raise RuntimeError("Cannot discard stored private access requests during downgrade.")
    op.drop_index("ix_private_access_requests_created_at", table_name="private_access_requests")
    op.drop_index("ix_private_access_requests_email", table_name="private_access_requests")
    op.drop_table("private_access_requests")
