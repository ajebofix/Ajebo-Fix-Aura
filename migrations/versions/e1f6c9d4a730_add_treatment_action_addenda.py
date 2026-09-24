"""add immutable treatment action addenda

Revision ID: e1f6c9d4a730
Revises: d0f5b8c3e620
Create Date: 2026-09-24 08:45:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e1f6c9d4a730"
down_revision = "d0f5b8c3e620"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "treatment_action_addenda",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("treatment_action_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("detail_text", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "category IN ('clarification', 'correction', 'additional_information')",
            name="ck_treatment_action_addenda_category",
        ),
        sa.CheckConstraint(
            "visibility IN ('client', 'advisor')",
            name="ck_treatment_action_addenda_visibility",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_treatment_action_addenda_reason_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(detail_text)) > 0",
            name="ck_treatment_action_addenda_detail_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(idempotency_key)) > 0",
            name="ck_treatment_action_addenda_key_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["treatment_action_id"],
            ["treatment_actions.id"],
            name="fk_treatment_action_addenda_action_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_treatment_action_addenda_created_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_treatment_action_addenda_idempotency_key",
        ),
    )
    op.create_index(
        "ix_treatment_action_addenda_treatment_action_id",
        "treatment_action_addenda",
        ["treatment_action_id"],
        unique=False,
    )
    op.create_index(
        "ix_treatment_action_addenda_action_created",
        "treatment_action_addenda",
        ["treatment_action_id", "created_at", "id"],
        unique=False,
    )


def downgrade():
    bind = op.get_bind()
    addendum_count = bind.execute(
        sa.text("SELECT count(*) FROM treatment_action_addenda")
    ).scalar_one()
    if addendum_count:
        raise RuntimeError(
            "Refusing to downgrade while published Treatment Action addenda exist."
        )

    op.drop_index(
        "ix_treatment_action_addenda_action_created",
        table_name="treatment_action_addenda",
    )
    op.drop_index(
        "ix_treatment_action_addenda_treatment_action_id",
        table_name="treatment_action_addenda",
    )
    op.drop_table("treatment_action_addenda")
