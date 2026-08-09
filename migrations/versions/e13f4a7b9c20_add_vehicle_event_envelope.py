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


def upgrade():
    with op.batch_alter_table("vehicle_events") as batch_op:
        batch_op.alter_column(
            "mileage",
            existing_type=sa.Integer(),
            nullable=True,
        )

        batch_op.add_column(
            sa.Column("schema_version", sa.Integer(), nullable=True)
        )

        batch_op.add_column(
            sa.Column("occurred_at", sa.DateTime(), nullable=True)
        )

        batch_op.add_column(
            sa.Column("recorded_at", sa.DateTime(), nullable=True)
        )

        batch_op.add_column(
            sa.Column("subject_type", sa.String(length=64), nullable=True)
        )

        batch_op.add_column(
            sa.Column("subject_id", sa.Integer(), nullable=True)
        )

        batch_op.add_column(
            sa.Column("actor_type", sa.String(length=32), nullable=True)
        )

        batch_op.add_column(
            sa.Column("actor_user_id", sa.Integer(), nullable=True)
        )

        batch_op.add_column(
            sa.Column("actor_authority", sa.String(length=32), nullable=True)
        )

        batch_op.add_column(
            sa.Column("visibility", sa.String(length=20), nullable=True)
        )

        batch_op.add_column(
            sa.Column("previous_state", sa.String(length=64), nullable=True)
        )

        batch_op.add_column(
            sa.Column("new_state", sa.String(length=64), nullable=True)
        )

        batch_op.add_column(
            sa.Column(
                "progression_direction",
                sa.String(length=32),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column("correlation_id", sa.String(length=64), nullable=True)
        )

        batch_op.add_column(
            sa.Column("causation_id", sa.String(length=64), nullable=True)
        )

        batch_op.add_column(
            sa.Column("evidence_refs", sa.JSON(), nullable=True)
        )

        batch_op.add_column(
            sa.Column("correction_of_event_id", sa.Integer(), nullable=True)
        )

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

    with op.batch_alter_table("vehicle_events") as batch_op:
        batch_op.drop_constraint(
            "fk_vehicle_events_correction_of_event_id",
            type_="foreignkey",
        )

        batch_op.drop_constraint(
            "fk_vehicle_events_actor_user_id",
            type_="foreignkey",
        )

        batch_op.drop_column("correction_of_event_id")
        batch_op.drop_column("evidence_refs")
        batch_op.drop_column("causation_id")
        batch_op.drop_column("correlation_id")
        batch_op.drop_column("progression_direction")
        batch_op.drop_column("new_state")
        batch_op.drop_column("previous_state")
        batch_op.drop_column("visibility")
        batch_op.drop_column("actor_authority")
        batch_op.drop_column("actor_user_id")
        batch_op.drop_column("actor_type")
        batch_op.drop_column("subject_id")
        batch_op.drop_column("subject_type")
        batch_op.drop_column("recorded_at")
        batch_op.drop_column("occurred_at")
        batch_op.drop_column("schema_version")

        batch_op.alter_column(
            "mileage",
            existing_type=sa.Integer(),
            nullable=False,
        )