"""Reconcile one known Aura production schema-history mismatch.

Some production databases received the maintenance schedule and vehicle
recall tables before their Alembic revisions were added. In that exact
state, Alembic remains at 82c0c175b392 and attempts to recreate tables
that already exist.

This script advances the version pointer only when:
- the current revision is exactly 82c0c175b392;
- both pre-existing tables are present; and
- both tables contain the columns expected by their migrations.

All other database states are left untouched.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from app import app
from extensions import db


SOURCE_REVISION = "82c0c175b392"
RECONCILED_REVISION = "737b4134e370"

EXPECTED_COLUMNS = {
    "maintenance_schedules": {
        "id",
        "car_id",
        "service_name",
        "due_mileage",
        "due_date",
        "completed_at",
        "status",
        "source",
        "created_at",
    },
    "vehicle_recalls": {
        "id",
        "car_id",
        "recall_number",
        "title",
        "summary",
        "risk_level",
        "is_open",
        "published_at",
        "closed_at",
        "source",
    },
}


def current_revision(connection) -> str | None:
    inspector = inspect(connection)
    if "alembic_version" not in inspector.get_table_names():
        return None

    return connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one_or_none()


def table_columns(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def reconcile() -> None:
    with app.app_context():
        with db.engine.begin() as connection:
            revision = current_revision(connection)

            if revision != SOURCE_REVISION:
                print(
                    "Alembic reconciliation not required: "
                    f"current revision is {revision!r}."
                )
                return

            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            missing_tables = set(EXPECTED_COLUMNS) - tables

            if missing_tables:
                print(
                    "Alembic reconciliation not applied because tables are "
                    f"missing: {sorted(missing_tables)}."
                )
                return

            for table_name, expected in EXPECTED_COLUMNS.items():
                actual = table_columns(inspector, table_name)
                missing_columns = expected - actual
                if missing_columns:
                    raise RuntimeError(
                        f"Refusing to reconcile {table_name}: missing columns "
                        f"{sorted(missing_columns)}."
                    )

            connection.execute(
                text(
                    "UPDATE alembic_version "
                    "SET version_num = :revision"
                ),
                {"revision": RECONCILED_REVISION},
            )

            print(
                "Reconciled Alembic history from "
                f"{SOURCE_REVISION} to {RECONCILED_REVISION}."
            )


if __name__ == "__main__":
    reconcile()
