"""add client invitations and allow pending owner email

Revision ID: a4c9e7b2d610
Revises: f9c7d2b5a341
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa


revision = "a4c9e7b2d610"
down_revision = "f9c7d2b5a341"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "email",
            existing_type=sa.String(length=120),
            nullable=True,
        )

    op.create_table(
        "client_invitations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_client_invitations_user_id",
        "client_invitations",
        ["user_id"],
        unique=False,
    )


def downgrade():
    bind = op.get_bind()
    missing_email_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE email IS NULL")
    ).scalar() or 0
    if missing_email_count:
        raise RuntimeError(
            "Cannot downgrade owner onboarding while users with no email remain. "
            "Complete or remove pending assisted accounts first."
        )

    op.drop_index(
        "ix_client_invitations_user_id",
        table_name="client_invitations",
    )
    op.drop_table("client_invitations")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "email",
            existing_type=sa.String(length=120),
            nullable=False,
        )
