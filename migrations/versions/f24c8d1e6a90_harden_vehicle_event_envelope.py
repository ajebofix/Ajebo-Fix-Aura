"""Harden canonical VehicleEvent envelope constraints and timeline index.

Revision ID: f24c8d1e6a90
Revises: e13f4a7b9c20
Create Date: 2026-08-13

Wave 1.2 first proves additive compatibility, canonical emission, one migrated
domain, and timeline reconstruction. This follow-up adds production-database
constraints only after those write/read paths have been exercised.

Legacy rows remain valid because envelope fields are still allowed to be NULL.
The migration fails closed if any existing non-NULL value falls outside the
approved canonical vocabularies instead of silently rewriting history.
"""

from alembic import op
import sqlalchemy as sa


revision = "f24c8d1e6a90"
down_revision = "e13f4a7b9c20"
branch_labels = None
depends_on = None


ACTOR_TYPES = ("user", "system", "provider")
ACTOR_AUTHORITIES = (
    "owner",
    "driver",
    "advisor",
    "administrator",
    "system",
    "provider",
)
VISIBILITIES = ("client", "advisor", "internal")
PROGRESSION_DIRECTIONS = (
    "improving",
    "stable",
    "deteriorating",
    "recurring",
    "resolved",
    "insufficient_evidence",
    "not_applicable",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _assert_no_invalid_values(
    *,
    column: str,
    allowed_values: tuple[str, ...],
) -> None:
    bind = op.get_bind()
    count = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*)
            FROM vehicle_events
            WHERE {column} IS NOT NULL
              AND {column} NOT IN ({_quoted(allowed_values)})
            """
        )
    ).scalar_one()

    if count:
        raise RuntimeError(
            f"Cannot harden vehicle_events.{column}: "
            f"found {count} value(s) outside the canonical vocabulary"
        )


def _preflight() -> None:
    _assert_no_invalid_values(
        column="actor_type",
        allowed_values=ACTOR_TYPES,
    )
    _assert_no_invalid_values(
        column="actor_authority",
        allowed_values=ACTOR_AUTHORITIES,
    )
    _assert_no_invalid_values(
        column="visibility",
        allowed_values=VISIBILITIES,
    )
    _assert_no_invalid_values(
        column="progression_direction",
        allowed_values=PROGRESSION_DIRECTIONS,
    )

    invalid_schema_versions = op.get_bind().execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM vehicle_events
            WHERE schema_version IS NOT NULL
              AND schema_version < 1
            """
        )
    ).scalar_one()
    if invalid_schema_versions:
        raise RuntimeError(
            "Cannot harden vehicle_events.schema_version: "
            f"found {invalid_schema_versions} value(s) below 1"
        )


def _create_postgres_checks() -> None:
    # PostgreSQL is Aura's production database. SQLite remains a local/test
    # compatibility dialect; application/service validation is authoritative
    # there so Alembic does not need to recreate the self-referential event
    # table merely to add CHECK constraints.
    if op.get_bind().dialect.name == "sqlite":
        return

    op.create_check_constraint(
        "ck_vehicle_events_actor_type",
        "vehicle_events",
        f"actor_type IS NULL OR actor_type IN ({_quoted(ACTOR_TYPES)})",
    )
    op.create_check_constraint(
        "ck_vehicle_events_actor_authority",
        "vehicle_events",
        (
            "actor_authority IS NULL OR actor_authority IN "
            f"({_quoted(ACTOR_AUTHORITIES)})"
        ),
    )
    op.create_check_constraint(
        "ck_vehicle_events_visibility",
        "vehicle_events",
        f"visibility IS NULL OR visibility IN ({_quoted(VISIBILITIES)})",
    )
    op.create_check_constraint(
        "ck_vehicle_events_progression_direction",
        "vehicle_events",
        (
            "progression_direction IS NULL OR progression_direction IN "
            f"({_quoted(PROGRESSION_DIRECTIONS)})"
        ),
    )
    op.create_check_constraint(
        "ck_vehicle_events_schema_version",
        "vehicle_events",
        "schema_version IS NULL OR schema_version >= 1",
    )


def upgrade():
    _preflight()
    _create_postgres_checks()

    op.create_index(
        "ix_vehicle_events_timeline_scope",
        "vehicle_events",
        [
            "car_id",
            "subject_type",
            "subject_id",
            "recorded_at",
            "id",
        ],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_vehicle_events_timeline_scope",
        table_name="vehicle_events",
    )

    if op.get_bind().dialect.name == "sqlite":
        return

    op.drop_constraint(
        "ck_vehicle_events_schema_version",
        "vehicle_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_vehicle_events_progression_direction",
        "vehicle_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_vehicle_events_visibility",
        "vehicle_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_vehicle_events_actor_authority",
        "vehicle_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_vehicle_events_actor_type",
        "vehicle_events",
        type_="check",
    )
