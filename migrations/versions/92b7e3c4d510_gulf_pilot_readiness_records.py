"""Track internal Gulf readiness without touching production launch switches.

Revision ID: 92b7e3c4d510
Revises: 85c2f7d4e301
Create Date: 2026-10-16

No existing worker, job, customer, product or appointment data is modified.
"""
from alembic import op
import sqlalchemy as sa


revision = "92b7e3c4d510"
down_revision = "85c2f7d4e301"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gulf_readiness_records",
        sa.Column("key", sa.String(48), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("evidence_reference", sa.String(240)),
        sa.Column("internal_note", sa.String(600)),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "gulf_readiness_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(48), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=False),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("evidence_reference", sa.String(240)),
        sa.Column("note", sa.String(600)),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_gulf_readiness_history_key", "gulf_readiness_history", ["key"])


def downgrade():
    connection = op.get_bind()
    for table in ("gulf_readiness_history", "gulf_readiness_records"):
        if connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError(
                "Gulf readiness preparation data exists; refusing destructive downgrade."
            )
    op.drop_index("ix_gulf_readiness_history_key", table_name="gulf_readiness_history")
    op.drop_table("gulf_readiness_history")
    op.drop_table("gulf_readiness_records")
