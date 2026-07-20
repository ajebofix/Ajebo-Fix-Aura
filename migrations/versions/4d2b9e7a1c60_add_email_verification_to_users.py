"""add email verification to users

Revision ID: 4d2b9e7a1c60
Revises: dd71355ad494
Create Date: 2026-07-20 18:00:00.000000

Existing accounts are backfilled as verified so deployment does not
unexpectedly block established clients, drivers, or advisors. The migration
also tolerates a production schema where the column was created before
Alembic history was reconciled.
"""

from alembic import op
import sqlalchemy as sa


revision = "4d2b9e7a1c60"
down_revision = "dd71355ad494"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {
        column["name"]
        for column in inspector.get_columns("users")
    }

    if "email_verified_at" not in user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "email_verified_at",
                    sa.DateTime(),
                    nullable=True,
                )
            )

    op.execute(
        "UPDATE users "
        "SET email_verified_at = CURRENT_TIMESTAMP "
        "WHERE email_verified_at IS NULL"
    )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {
        column["name"]
        for column in inspector.get_columns("users")
    }

    if "email_verified_at" in user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("email_verified_at")
