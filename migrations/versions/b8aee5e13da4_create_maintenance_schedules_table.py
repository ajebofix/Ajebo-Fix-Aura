"""create maintenance_schedules table (missing from original history)

Revision ID: b8aee5e13da4
Revises: 82c0c175b392
Create Date: 2026-07-14 19:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8aee5e13da4'
down_revision = '82c0c175b392'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('maintenance_schedules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('car_id', sa.Integer(), nullable=False),
    sa.Column('service_name', sa.String(length=150), nullable=False),
    sa.Column('due_mileage', sa.Integer(), nullable=True),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('source', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['car_id'], ['cars.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_maintenance_car', 'maintenance_schedules', ['car_id'], unique=False)
    op.create_index('idx_maintenance_status', 'maintenance_schedules', ['status'], unique=False)


def downgrade():
    op.drop_index('idx_maintenance_status', table_name='maintenance_schedules')
    op.drop_index('idx_maintenance_car', table_name='maintenance_schedules')
    op.drop_table('maintenance_schedules')
