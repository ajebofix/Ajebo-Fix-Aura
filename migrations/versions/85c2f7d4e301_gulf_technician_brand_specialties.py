"""Separate Gulf technician service classes from brand specialisation.

Revision ID: 85c2f7d4e301
Revises: 3bf9d1e6a225
Create Date: 2026-10-14

Existing rows default to unknown specialisations: they must be reviewed
before new Gulf work is allocated. No client or technician records deleted.
"""
from alembic import op
import sqlalchemy as sa

revision = "85c2f7d4e301"
down_revision = "3bf9d1e6a225"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "gulf_technicians",
        sa.Column(
            "brand_specialties", sa.String(160), nullable=False,
            server_default=sa.text("''"),
        ),
    )
    # New technician forms must declare their brands explicitly.
    # The empty default preserves old records for manual review.


def downgrade():
    # Downgrading loses brand capability information and must not happen
    # accidentally after administrators have registered technicians.
    conn = op.get_bind()
    if conn.execute(
        sa.text("SELECT COUNT(*) FROM gulf_technicians WHERE brand_specialties <> ''")
    ).scalar():
        raise RuntimeError(
            "Technician brand specialisations exist; refusing destructive downgrade."
        )
    op.drop_column("gulf_technicians", "brand_specialties")
