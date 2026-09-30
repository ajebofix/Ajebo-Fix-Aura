from __future__ import annotations

from flask import session
from sqlalchemy.exc import OperationalError

from extensions import db
from models import User
from tests import test_assessment_download_authorization as assessment_download_tests


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


def test_authenticated_duplicate_login_post_with_stale_csrf_recovers(app, client):
    with app.app_context():
        user = User(
            name="Duplicate Login Admin",
            email="duplicate-login@example.com",
            phone_number="+2348000000999",
            role="admin",
            is_active=True,
        )
        user.set_password("Password123")
        db.session.add(user)
        db.session.commit()

    client.get("/auth/login")
    with client.session_transaction() as browser_session:
        original_csrf = str(browser_session["_csrf_token"])

    first = client.post(
        "/auth/login",
        data={
            "identifier": "duplicate-login@example.com",
            "password": "Password123",
            "csrf_token": original_csrf,
        },
        follow_redirects=False,
    )
    assert first.status_code == 302
    assert first.headers["Location"].endswith("/admin/dashboard")

    # A safe request after login seeds the new authenticated-session token,
    # making the original login form token stale exactly as in production.
    already_signed_in = client.get("/auth/login", follow_redirects=False)
    assert already_signed_in.status_code == 302

    duplicate = client.post(
        "/auth/login",
        data={
            "identifier": "duplicate-login@example.com",
            "password": "Password123",
            "csrf_token": original_csrf,
        },
        follow_redirects=False,
    )
    assert duplicate.status_code == 303
    assert duplicate.headers["Location"].endswith("/auth/login")


def test_login_form_disables_submit_after_first_submission(client):
    response = client.get("/auth/login")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="aura-login-form"' in body
    assert 'id="aura-login-submit"' in body
    assert 'submit.disabled = true' in body
    assert 'submit.textContent = "Signing in…"' in body


def test_expected_route_prefixes_are_registered_once(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/login" in rules
    assert "/auth/login" in rules
    assert "/cars/" in rules
    assert "/dashboard/" in rules
    assert "/healthz" in rules
    assert "/version" in rules

    assert "/auth/auth/login" not in rules
    assert "/cars/cars/" not in rules
    assert "/dashboard/dashboard/" not in rules


def test_login_alias_redirects_to_canonical_login(client):
    response = client.get("/login?next=/dashboard/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        "/auth/login?next=/dashboard/"
    )


def test_login_alias_preserves_post_method(client):
    with client.session_transaction() as browser_session:
        browser_session["_csrf_token"] = "login-alias-csrf-token"

    response = client.post(
        "/login",
        data={
            "csrf_token": "login-alias-csrf-token",
            "email": "someone@example.com",
            "password": "wrong-password",
        },
    )

    assert response.status_code == 307
    assert response.headers["Location"].endswith("/auth/login")


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


def test_owner_assessment_download_security_regression(app):
    assessment_download_tests.test_active_owner_can_download_finalized_report_from_shared_profile_route(
        app
    )


def test_owner_assessment_report_data_correctness_regression(app):
    assessment_download_tests.test_active_owner_can_download_finalized_report_from_shared_profile_route(
        app
    )


def test_owner_real_pdf_download_security_regression(app):
    assessment_download_tests.test_active_owner_can_download_real_pdf_report(app)


def test_advisor_assessment_download_security_regression(app):
    assessment_download_tests.test_advisor_keeps_direct_report_access(app)


def test_outsider_assessment_download_security_regression(app):
    assessment_download_tests.test_unrelated_authenticated_user_cannot_receive_owner_report(
        app
    )


def test_former_owner_assessment_download_security_regression(app):
    assessment_download_tests.test_inactive_former_owner_cannot_receive_report(app)


def test_legacy_assessment_report_url_redirect_security_regression(app):
    assessment_download_tests.test_legacy_admin_prefixed_report_url_redirects_to_neutral_route(
        app
    )


def test_neutral_assessment_report_route_registration_regression(app):
    assessment_download_tests.test_neutral_routes_are_registered_without_admin_prefix(
        app
    )
