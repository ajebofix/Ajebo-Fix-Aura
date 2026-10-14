from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault(
    "PROFILE_ENCRYPTION_KEY",
    "Xa3TmWWEgrSvl-KCaDY5lYoQHDk4OBsfx60fJXCf_i0=",
)
os.environ.setdefault("PROFILE_ENCRYPTION_KEY_VERSION", "test-v1")

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402


@pytest.fixture()
def app():
    application = create_app()
    # An explicitly provisioned disposable CI database may exercise the
    # same Gulf workflow on real PostgreSQL instead of SQLite semantics.
    test_db_url = os.environ.get("GULF_CI_POSTGRES_URL", "sqlite:///:memory:")
    application.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=test_db_url,
        WTF_CSRF_ENABLED=False,
    )

    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()
