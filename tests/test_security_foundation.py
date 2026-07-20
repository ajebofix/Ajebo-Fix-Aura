from __future__ import annotations

from flask import session
from sqlalchemy.exc import OperationalError

from extensions import db


def test_security_headers_are_applied(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_unsafe_request_without_csrf_token_is_rejected(client):
    response = client.post(
        "/auth/login",
        data={"email": "someone@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 400


def test_expected_route_prefixes_are_registered_once(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/auth/login" in rules
    assert "/cars/" in rules
    assert "/dashboard/" in rules
    assert "/healthz" in rules
    assert "/version" in rules

    assert "/auth/auth/login" not in rules
    assert "/cars/cars/" not in rules
    assert "/dashboard/dashboard/" not in rules


def test_root_does_not_disclose_internal_route_inventory(client):
    response = client.get("/")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload == {"status": "ok", "service": "Ajebo Fix Aura"}


def test_health_endpoint_validates_database_readiness(client):
    response = client.get("/healthz")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["database"] == "sqlite"


def test_version_bypasses_stale_authenticated_cookie(client, monkeypatch):
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = "3"
        browser_session["session_token"] = "stale-token"
        browser_session["session_token_hash"] = "stale-hash"

    def fail_user_load(*_args, **_kwargs):
        raise OperationalError("SELECT users", {}, Exception("missing column"))

    monkeypatch.setattr(db.session, "get", fail_user_load)

    response = client.get("/version")

    assert response.status_code == 200
    assert response.get_json()["database"] == "sqlite"


def test_user_loader_discards_incompatible_session(app, monkeypatch):
    def fail_user_load(*_args, **_kwargs):
        raise OperationalError("SELECT users", {}, Exception("missing column"))

    monkeypatch.setattr(db.session, "get", fail_user_load)

    with app.test_request_context("/dashboard/"):
        session["_user_id"] = "3"
        session["session_token"] = "stale-token"
        session["session_token_hash"] = "stale-hash"

        loaded_user = app.login_manager._user_callback("3")

        assert loaded_user is None
        assert not session
