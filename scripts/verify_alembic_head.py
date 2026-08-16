"""Verify that PostgreSQL is upgraded to every current repository Alembic head."""

from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    database_uri = os.environ["SQLALCHEMY_DATABASE_URI"]
    alembic_config = Config(str(ROOT / "migrations" / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(alembic_config)
    expected_heads = set(script.get_heads())
    if not expected_heads:
        raise SystemExit("Repository Alembic history has no head revision")

    engine = create_engine(database_uri)
    with engine.connect() as connection:
        current_heads = set(
            connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
        )

    if current_heads != expected_heads:
        raise SystemExit(
            "PostgreSQL is not at the repository Alembic head(s): "
            f"database={sorted(current_heads)} repository={sorted(expected_heads)}"
        )

    print(
        "PostgreSQL matches repository Alembic head(s): "
        + ", ".join(sorted(expected_heads))
    )


if __name__ == "__main__":
    main()
