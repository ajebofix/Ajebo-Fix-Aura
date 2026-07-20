from __future__ import annotations

from datetime import datetime, timedelta

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
        token = browser_session.get("_csrf_token")
        if not token:
            token = "session-registry-test-csrf-token"
            browser_session["_csrf_token"] = token
        return token


def _login(client, email: str = "sessions@example.com") -> None:
    token = _csrf_token(client)
    response = client.post(
        "/auth/login",
        data={
            "csrf_token": token,
            "email": email,
            "password": "Password123",
        },
    )
    assert response.status_code == 302


def test_login_creates_hashed_session_record(app, client):
    with app.app_context():
        user = _create_user()
        user_id = user.id

    _login(client)

    with client.session_transaction() as browser_session:
        raw_token = browser_session.get("session_token")
        token_hash = browser_session.get("session_token_hash")
        assert raw_token

    with app.app_context():
        record = UserSession.query.filter_by(user_id=user_id).one()
        assert record.token_hash == token_hash
        assert record.token_hash != raw_token
        assert record.device_label == "Unknown device · browser"
        assert record.ip_hash is not None


def test_user_can_revoke_all_other_sessions(app, client):
    with app.app_context():
        user = _create_user()
        user_id = user.id

    _login(client)

    with client.session_transaction() as browser_session:
        current_hash = browser_session["session_token_hash"]

    other_hash = "b" * 64

    with app.app_context():
        current_record = UserSession.query.filter_by(
            user_id=user_id,
            token_hash=current_hash,
        ).one()
        other_record = UserSession(
            user_id=user_id,
            token_hash=other_hash,
            password_fingerprint=current_record.password_fingerprint,
            device_label="Second test device",
            user_agent="Test Browser",
            ip_hash="c" * 64,
            created_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        db.session.add(other_record)
        db.session.commit()

    token = _csrf_token(client, "/auth/sessions")
    response = client.post(
        "/auth/sessions/revoke-others",
        data={"csrf_token": token},
    )
    assert response.status_code == 302

    with app.app_context():
        current_record = UserSession.query.filter_by(
            user_id=user_id,
            token_hash=current_hash,
        ).one()
        revoked_record = UserSession.query.filter_by(
            user_id=user_id,
            token_hash=other_hash,
        ).one()

        assert current_record.revoked_at is None
        assert revoked_record.revoked_at is not None
        assert revoked_record.revoked_reason == "user_revoked_others"


def test_password_change_invalidates_other_sessions(app):
    first_client = app.test_client()
    second_client = app.test_client()

    with app.app_context():
        user = _create_user()
        user_id = user.id

    _login(first_client)
    _login(second_client)

    token = _csrf_token(first_client, "/auth/change-password")
    changed = first_client.post(
        "/auth/change-password",
        data={
            "csrf_token": token,
            "current_password": "Password123",
            "new_password": "DifferentPassword456",
            "confirm_password": "DifferentPassword456",
        },
    )
    assert changed.status_code == 302

    response = second_client.get("/dashboard/")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]

    with app.app_context():
        records = UserSession.query.filter_by(user_id=user_id).all()
        assert records
        assert all(record.revoked_at is not None for record in records)
        assert {record.revoked_reason for record in records} == {"password_changed"}


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
