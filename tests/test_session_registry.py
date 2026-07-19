from __future__ import annotations

from datetime import datetime

from extensions import db
from models import User
from security.session_registry import UserSession


def _create_user() -> User:
    user = User(
        name="Session Test",
        email="sessions@example.com",
        phone_number="08000000002",
        role="user",
        email_verified_at=datetime.utcnow(),
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.commit()
    return user


def _csrf_token(client, path: str = "/auth/login") -> str:
    client.get(path)
    with client.session_transaction() as browser_session:
        return browser_session["_csrf_token"]


def _login(client, email: str = "sessions@example.com") -> None:
    token = _csrf_token(client)
    response = client.post(
        "/auth/login",
        data={
            "csrf_token": token,
            "email": email,
            "password": "Password123",
        },
        headers={"User-Agent": "Mozilla/5.0 iPhone Safari"},
    )
    assert response.status_code == 302


def test_login_creates_hashed_session_record(app, client):
    with app.app_context():
        user = _create_user()
        user_id = user.id

    _login(client)

    with client.session_transaction() as browser_session:
        assert browser_session.get("session_token")
        token_hash = browser_session.get("session_token_hash")

    with app.app_context():
        record = UserSession.query.filter_by(user_id=user_id).one()
        assert record.token_hash == token_hash
        assert record.token_hash != browser_session.get("session_token")
        assert record.device_label == "iPhone · Safari"
        assert record.ip_hash is not None


def test_user_can_revoke_all_other_sessions(app):
    first_client = app.test_client()
    second_client = app.test_client()

    with app.app_context():
        _create_user()

    _login(first_client)
    _login(second_client)

    token = _csrf_token(first_client, "/auth/sessions")
    response = first_client.post(
        "/auth/sessions/revoke-others",
        data={"csrf_token": token},
    )
    assert response.status_code == 302

    blocked = second_client.get("/dashboard/")
    assert blocked.status_code == 302
    assert "/auth/login" in blocked.headers["Location"]

    allowed = first_client.get("/auth/sessions")
    assert allowed.status_code == 200


def test_password_change_invalidates_existing_session(app, client):
    with app.app_context():
        user = _create_user()
        user_id = user.id

    _login(client)

    with app.app_context():
        user = db.session.get(User, user_id)
        user.set_password("DifferentPassword456")
        db.session.commit()

    response = client.get("/dashboard/")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]

    with app.app_context():
        record = UserSession.query.filter_by(user_id=user_id).one()
        assert record.revoked_at is not None
        assert record.revoked_reason == "password_changed"


def test_logout_revokes_current_session(app, client):
    with app.app_context():
        user = _create_user()
        user_id = user.id

    _login(client)
    token = _csrf_token(client, "/auth/sessions")
    response = client.post("/auth/logout", data={"csrf_token": token})
    assert response.status_code == 302

    with app.app_context():
        record = UserSession.query.filter_by(user_id=user_id).one()
        assert record.revoked_at is not None
        assert record.revoked_reason == "logout"
