"""enforce manufacturer NOT NULL on diagnostic_code_definitions

Backfills any existing NULL manufacturer rows to the canonical
'GENERIC' value, then makes the column NOT NULL so the
(code, manufacturer) unique constraint actually enforces
uniqueness for generic codes. Postgres and most databases treat
NULL as distinct from any other NULL for uniqueness purposes, so
without this fix multiple "generic" rows for the same code could
be inserted silently.

Revision ID: dd71355ad494
Revises: 737b4134e370
Create Date: 2026-07-14 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'dd71355ad494'
down_revision = '737b4134e370'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE diagnostic_code_definitions "
        "SET manufacturer = 'GENERIC' "
        "WHERE manufacturer IS NULL"
    )

    with op.batch_alter_table('diagnostic_code_definitions', schema=None) as batch_op:
        batch_op.alter_column(
            'manufacturer',
            existing_type=sa.String(length=100),
            nullable=False,
            server_default='GENERIC',
        )


def downgrade():
    with op.batch_alter_table('diagnostic_code_definitions', schema=None) as batch_op:
        batch_op.alter_column(
            'manufacturer',
            existing_type=sa.String(length=100),
            nullable=True,
            server_default=None,
        )
