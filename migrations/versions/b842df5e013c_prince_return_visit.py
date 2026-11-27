"""Preserve odometer readings and privacy notice per Prince service visit.

Revision ID: b842df5e013c
Revises: a731df4e902b
"""
from alembic import op
import sqlalchemy as sa

revision = "b842df5e013c"
down_revision = "a731df4e902b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("prince_jobs", sa.Column("visit_odometer_km", sa.Integer()))
    op.add_column("prince_jobs", sa.Column("visit_notice_version", sa.String(48)))


def downgrade():
    op.drop_column("prince_jobs", "visit_notice_version")
    op.drop_column("prince_jobs", "visit_odometer_km")
