"""backfill legacy service mileage observations

Revision ID: a1c9e4d7b620
Revises: f0a3b6c2d901
Create Date: 2026-09-08
"""

from alembic import op
import sqlalchemy as sa


revision = "a1c9e4d7b620"
down_revision = "f0a3b6c2d901"
branch_labels = None
depends_on = None


_BACKFILL_NOTE = (
    "Backfilled from an existing service record during the mileage observation "
    "foundation rollout."
)


def upgrade():
    # Import lazily so Alembic revision-discovery tooling can inspect this file
    # without requiring the application root to already be on sys.path.
    from mileage.backfill import backfill_legacy_service_mileage_observations

    bind = op.get_bind()
    result = backfill_legacy_service_mileage_observations(bind)
    print(
        "Legacy service mileage backfill: "
        f"scanned={result.scanned} inserted={result.inserted} "
        f"skipped_existing={result.skipped_existing} "
        f"skipped_invalid={result.skipped_invalid}"
    )


def downgrade():
    bind = op.get_bind()
    metadata = sa.MetaData()
    observations = sa.Table(
        "mileage_observations",
        metadata,
        autoload_with=bind,
    )
    bind.execute(
        observations.delete().where(
            observations.c.source == "service_record",
            observations.c.note == _BACKFILL_NOTE,
        )
    )
