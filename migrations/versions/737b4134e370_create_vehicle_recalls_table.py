"""create vehicle_recalls table (missing from original history)

Revision ID: 737b4134e370
Revises: b8aee5e13da4
Create Date: 2026-07-14 19:06:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '737b4134e370'
down_revision = 'b8aee5e13da4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('vehicle_recalls',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('car_id', sa.Integer(), nullable=False),
    sa.Column('recall_number', sa.String(length=100), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('risk_level', sa.String(length=20), nullable=True),
    sa.Column('is_open', sa.Boolean(), nullable=False),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.Column('source', sa.String(length=100), nullable=True),
    sa.ForeignKeyConstraint(['car_id'], ['cars.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_vehicle_recall_car', 'vehicle_recalls', ['car_id'], unique=False)


def downgrade():
    op.drop_index('idx_vehicle_recall_car', table_name='vehicle_recalls')
    op.drop_table('vehicle_recalls')
