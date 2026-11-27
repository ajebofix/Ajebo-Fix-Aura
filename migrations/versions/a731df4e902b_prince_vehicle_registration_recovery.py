"""Add Prince vehicle registration metadata and one-time staff recovery.
Revision ID: a731df4e902b
Revises: f26b0c4d837e
"""
from alembic import op
import sqlalchemy as sa

revision = "a731df4e902b"
down_revision = "f26b0c4d837e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("prince_tenants", sa.Column("vehicle_details_notice_version", sa.String(48)))
    op.add_column("prince_memberships", sa.Column("reset_code_hash", sa.String(64)))
    op.add_column("prince_memberships", sa.Column("reset_code_expires_at", sa.DateTime()))
    op.add_column("prince_vehicles", sa.Column("colour", sa.String(60)))
    op.add_column("prince_vehicles", sa.Column("engine_number", sa.String(80)))
    op.add_column("prince_vehicles", sa.Column("registration_number", sa.String(30)))
    op.add_column("prince_vehicles", sa.Column("trim", sa.String(100)))
    op.add_column("prince_vehicles", sa.Column("engine_type", sa.String(60)))
    op.add_column("prince_vehicles", sa.Column("fuel_type", sa.String(30)))
    op.add_column("prince_vehicles", sa.Column("transmission", sa.String(30)))
    op.add_column("prince_vehicles", sa.Column("body_type", sa.String(40)))
    op.add_column("prince_vehicles", sa.Column("odometer_km", sa.Integer()))
    op.add_column("prince_vehicles", sa.Column("details_notice_version", sa.String(48)))


def downgrade():
    for name in ("details_notice_version", "odometer_km", "colour", "engine_number", "registration_number", "trim", "engine_type", "fuel_type", "transmission", "body_type"):
        op.drop_column("prince_vehicles", name)
    op.drop_column("prince_memberships", "reset_code_expires_at")
    op.drop_column("prince_memberships", "reset_code_hash")
    op.drop_column("prince_tenants", "vehicle_details_notice_version")
