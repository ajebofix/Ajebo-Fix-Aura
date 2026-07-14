"""create vehicle_dtcs table (missing from original history)

Revision ID: 09d596bc12e5
Revises: 1a56c84b07a6
Create Date: 2026-07-14 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '09d596bc12e5'
down_revision = '1a56c84b07a6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('vehicle_dtcs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('car_id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('code_type', sa.String(length=20), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('affected_system', sa.String(length=100), nullable=True),
    sa.Column('severity', sa.String(length=20), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('advisor_note', sa.Text(), nullable=True),
    sa.Column('detected_at', sa.DateTime(), nullable=False),
    sa.Column('cleared_at', sa.DateTime(), nullable=True),
    sa.Column('source', sa.String(length=100), nullable=False),
    sa.ForeignKeyConstraint(['car_id'], ['cars.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_vehicle_dtc_car', 'vehicle_dtcs', ['car_id'], unique=False)
    op.create_index('idx_vehicle_dtc_status', 'vehicle_dtcs', ['status'], unique=False)


def downgrade():
    op.drop_index('idx_vehicle_dtc_status', table_name='vehicle_dtcs')
    op.drop_index('idx_vehicle_dtc_car', table_name='vehicle_dtcs')
    op.drop_table('vehicle_dtcs')
