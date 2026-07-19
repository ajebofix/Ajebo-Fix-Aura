from __future__ import annotations

from datetime import datetime

from extensions import db
from models import User
from security.email_verification import (
    generate_email_verification_token,
    verify_email_token,
)


def _create_user(*, verified: bool = False) -> User:
    user = User(
        name="Verification Test",
        email="verification@example.com",
        phone_number="08000000001",
        role="user",
        email_verified_at=datetime.utcnow() if verified else None,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.commit()
    return user


def _csrf_token_for(client, path: str) -> str:
    client.get(path)
    with client.session_transaction() as session:
        return session["_csrf_token"]


def _sign_in(client, user: User) -> None:
    token = _csrf_token_for(client, "/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "csrf_token": token,
            "email": user.email,
            "password": "Password123",
        },
    )
    assert response.status_code == 302


def test_verification_token_marks_account_verified(app, client):
    with app.app_context():
        user = _create_user()
        token = generate_email_verification_token(user)
        user_id = user.id

    response = client.get(f"/auth/verify-email?token={token}")

    assert response.status_code == 302
    with app.app_context():
        refreshed = db.session.get(User, user_id)
        assert refreshed.email_verified_at is not None


def test_verification_token_is_bound_to_password_state(app):
    with app.app_context():
        user = _create_user()
        token = generate_email_verification_token(user)
        user.set_password("DifferentPassword456")
        db.session.commit()

        assert verify_email_token(token) is None


def test_unverified_account_is_blocked_from_booking(app, client):
    with app.app_context():
        user = _create_user()
        _sign_in(client, user)

    response = client.get("/cars/999/consultations/book")

    assert response.status_code == 302
    assert "/auth/verification-required" in response.headers["Location"]


def test_verified_account_passes_email_gate(app, client):
    with app.app_context():
        user = _create_user(verified=True)
        _sign_in(client, user)

    response = client.get("/cars/999/consultations/book")

    assert response.status_code == 404


def test_resend_verification_is_rate_limited(app, client):
    with app.app_context():
        user = _create_user()
        _sign_in(client, user)

    token = _csrf_token_for(client, "/auth/verification-required")

    for _ in range(3):
        response = client.post(
            "/auth/resend-verification",
            data={"csrf_token": token},
        )
        assert response.status_code == 302

    blocked = client.post(
        "/auth/resend-verification",
        data={"csrf_token": token},
    )
    assert blocked.status_code == 429
