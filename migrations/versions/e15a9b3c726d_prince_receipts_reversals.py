"""Add training receipts and payment allocation reversals.

Revision ID: e15a9b3c726d
Revises: d04f8a2b615c
"""
from alembic import op
import sqlalchemy as sa

revision = "e15a9b3c726d"
down_revision = "d04f8a2b615c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prince_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("issued_by_user_id", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["prince_payments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["issued_by_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "prince_payment_reversals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("reversed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reversed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["prince_payments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reversed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
    )


def downgrade():
    connection = op.get_bind()
    if (connection.execute(sa.text("SELECT COUNT(*) FROM prince_receipts")).scalar()
            or connection.execute(sa.text("SELECT COUNT(*) FROM prince_payment_reversals")).scalar()):
        raise RuntimeError("Receipt or reversal history exists; refusing destructive downgrade.")
    op.drop_table("prince_payment_reversals")
    op.drop_table("prince_receipts")
