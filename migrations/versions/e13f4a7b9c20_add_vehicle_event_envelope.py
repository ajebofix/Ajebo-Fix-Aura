"""Add additive VehicleEvent event-envelope fields.

Revision ID: e13f4a7b9c20
Revises: d42e7a1c9b50
Create Date: 2026-08-03

This migration preserves all legacy columns and readers while adding the
nullable fields required for Aura's canonical progression envelope.
Historical rows are backfilled only with deterministic facts.
"""

from alembic import op
import sqlalchemy as sa


revision = "e13f4a7b9c20"
down_revision = "d42e7a1c9b50"
branch_labels = None
depends_on = None


ENVELOPE_COLUMNS = (
    sa.Column("schema_version", sa.Integer(), nullable=True),
    sa.Column("occurred_at", sa.DateTime(), nullable=True),
    sa.Column("recorded_at", sa.DateTime(), nullable=True),
    sa.Column("subject_type", sa.String(length=64), nullable=True),
    sa.Column("subject_id", sa.Integer(), nullable=True),
    sa.Column("actor_type", sa.String(length=32), nullable=True),
    sa.Column("actor_user_id", sa.Integer(), nullable=True),
    sa.Column("actor_authority", sa.String(length=32), nullable=True),
    sa.Column("visibility", sa.String(length=20), nullable=True),
    sa.Column("previous_state", sa.String(length=64), nullable=True),
    sa.Column("new_state", sa.String(length=64), nullable=True),
    sa.Column("progression_direction", sa.String(length=32), nullable=True),
    sa.Column("correlation_id", sa.String(length=64), nullable=True),
    sa.Column("causation_id", sa.String(length=64), nullable=True),
    sa.Column("evidence_refs", sa.JSON(), nullable=True),
    sa.Column("correction_of_event_id", sa.Integer(), nullable=True),
)

ENVELOPE_COLUMN_NAMES = tuple(column.name for column in ENVELOPE_COLUMNS)


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _relax_mileage_nullability():
    if _is_sqlite():
        # SQLite requires table recreation for nullability changes. Keep this
        # batch isolated from new-column ordering and self-referential FKs.
        with op.batch_alter_table("vehicle_events") as batch_op:
            batch_op.alter_column(
                "mileage",
                existing_type=sa.Integer(),
                nullable=True,
            )
        return

    op.alter_column(
        "vehicle_events",
        "mileage",
        existing_type=sa.Integer(),
        nullable=True,
    )


def _add_envelope_columns():
    # SQLite and PostgreSQL both support additive nullable columns directly.
    # Keeping these outside batch mode avoids Alembic's SQLite partial-ordering
    # circular dependency when the self-referential correction FK is present.
    for column in ENVELOPE_COLUMNS:
        op.add_column("vehicle_events", column.copy())


def _create_envelope_foreign_keys():
    if _is_sqlite():
        # SQLite cannot ALTER TABLE ADD CONSTRAINT, so create the FKs in their
        # own batch after all columns already exist.
        with op.batch_alter_table("vehicle_events") as batch_op:
            batch_op.create_foreign_key(
                "fk_vehicle_events_actor_user_id",
                "users",
                ["actor_user_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_foreign_key(
                "fk_vehicle_events_correction_of_event_id",
                "vehicle_events",
                ["correction_of_event_id"],
                ["id"],
                ondelete="SET NULL",
            )
        return

    op.create_foreign_key(
        "fk_vehicle_events_actor_user_id",
        "vehicle_events",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_vehicle_events_correction_of_event_id",
        "vehicle_events",
        "vehicle_events",
        ["correction_of_event_id"],
        ["id"],
        ondelete="SET NULL",
    )


def upgrade():
    _relax_mileage_nullability()
    _add_envelope_columns()
    _create_envelope_foreign_keys()

    # Deterministic backfill only. Do not invent authority, state,
    # progression, evidence, correlation or subject identity.
    op.execute(
        sa.text(
            """
            UPDATE vehicle_events
            SET schema_version = 1
            WHERE schema_version IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE vehicle_events
            SET occurred_at = COALESCE(
                CAST(event_date AS TIMESTAMP),
                created_at
            )
            WHERE occurred_at IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE vehicle_events
            SET recorded_at = created_at
            WHERE recorded_at IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE vehicle_events
            SET actor_user_id = created_by,
                actor_type = CASE
                    WHEN created_by IS NOT NULL THEN 'user'
                    ELSE actor_type
                END
            WHERE actor_user_id IS NULL
            """
        )
    )

    # Legacy rows are not automatically client-safe. Use the conservative
    # visibility default and require future emitters to set visibility.
    op.execute(
        sa.text(
            """
            UPDATE vehicle_events
            SET visibility = 'internal'
            WHERE visibility IS NULL
            """
        )
    )

    op.create_index(
        "ix_vehicle_events_car_occurred_at",
        "vehicle_events",
        ["car_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_vehicle_events_subject",
        "vehicle_events",
        ["subject_type", "subject_id"],
        unique=False,
    )
    op.create_index(
        "ix_vehicle_events_correlation_id",
        "vehicle_events",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        "ix_vehicle_events_correction_of_event_id",
        "vehicle_events",
        ["correction_of_event_id"],
        unique=False,
    )


def _drop_envelope_foreign_keys():
    if _is_sqlite():
        with op.batch_alter_table("vehicle_events") as batch_op:
            batch_op.drop_constraint(
                "fk_vehicle_events_correction_of_event_id",
                type_="foreignkey",
            )
            batch_op.drop_constraint(
                "fk_vehicle_events_actor_user_id",
                type_="foreignkey",
            )
        return

    op.drop_constraint(
        "fk_vehicle_events_correction_of_event_id",
        "vehicle_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_vehicle_events_actor_user_id",
        "vehicle_events",
        type_="foreignkey",
    )


def _remove_envelope_columns_and_restore_mileage():
    if _is_sqlite():
        with op.batch_alter_table("vehicle_events") as batch_op:
            for column_name in reversed(ENVELOPE_COLUMN_NAMES):
                batch_op.drop_column(column_name)
            batch_op.alter_column(
                "mileage",
                existing_type=sa.Integer(),
                nullable=False,
            )
        return

    for column_name in reversed(ENVELOPE_COLUMN_NAMES):
        op.drop_column("vehicle_events", column_name)
    op.alter_column(
        "vehicle_events",
        "mileage",
        existing_type=sa.Integer(),
        nullable=False,
    )


def downgrade():
    op.drop_index(
        "ix_vehicle_events_correction_of_event_id",
        table_name="vehicle_events",
    )
    op.drop_index(
        "ix_vehicle_events_correlation_id",
        table_name="vehicle_events",
    )
    op.drop_index(
        "ix_vehicle_events_subject",
        table_name="vehicle_events",
    )
    op.drop_index(
        "ix_vehicle_events_car_occurred_at",
        table_name="vehicle_events",
    )

    _drop_envelope_foreign_keys()
    _remove_envelope_columns_and_restore_mileage()
