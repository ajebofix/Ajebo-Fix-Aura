"""create vehicle_profiles table (missing from original history)

Revision ID: ea28d5290dcb
Revises: 554e83093870
Create Date: 2026-07-14 18:55:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ea28d5290dcb'
down_revision = '554e83093870'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('vehicle_profiles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('car_id', sa.Integer(), nullable=False),
    sa.Column('trim', sa.String(length=100), nullable=True),
    sa.Column('body_style', sa.String(length=100), nullable=True),
    sa.Column('fuel_type', sa.String(length=50), nullable=True),
    sa.Column('drive_type', sa.String(length=50), nullable=True),
    sa.Column('plant_country', sa.String(length=100), nullable=True),
    sa.Column('vin_decoded', sa.Boolean(), nullable=False),
    sa.Column('decoded_at', sa.DateTime(), nullable=True),
    sa.Column('source', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['car_id'], ['cars.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('car_id')
    )


def downgrade():
    op.drop_table('vehicle_profiles')
