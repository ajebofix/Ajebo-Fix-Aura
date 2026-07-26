from __future__ import annotations

from datetime import datetime

from extensions import db
from models import User
from security.email_verification import (
    generate_email_verification_token,
    send_email_verification,
    verify_email_token,
)
from services.email_delivery import EmailDeliveryResult, send_transactional_email


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


def test_verification_token_marks_account_verified_and_is_consumed(app, client):
    with app.app_context():
        user = _create_user()
        token = generate_email_verification_token(user)
        user_id = user.id

    response = client.get(f"/auth/verify-email?token={token}")

    assert response.status_code == 302
    with app.app_context():
        refreshed = db.session.get(User, user_id)
        assert refreshed.email_verified_at is not None
        assert verify_email_token(token) is None


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


def test_resend_delivery_posts_over_https(app, monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "email_123"}

    def fake_post(url, *, json, headers, timeout):
        captured.update(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeResponse()

    monkeypatch.setattr("services.email_delivery.requests.post", fake_post)

    with app.app_context():
        app.config.update(
            RESEND_API_KEY="re_test_key",
            RESEND_FROM_EMAIL="Aura by Ajebo Fix <verification@aura.test>",
            RESEND_REPLY_TO="support@aura.test",
            RESEND_TIMEOUT=7,
            MAIL_SUPPRESS_SEND=False,
        )
        result = send_transactional_email(
            to="delivered@resend.dev",
            subject="Confirm your Aura email address",
            text="Verification body",
            idempotency_key="aura-test-verification",
        )

    assert result.success is True
    assert result.provider_message_id == "email_123"
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["timeout"] == 7
    assert captured["headers"]["Authorization"] == "Bearer re_test_key"
    assert captured["headers"]["Idempotency-Key"] == "aura-test-verification"
    assert captured["json"] == {
        "from": "Aura by Ajebo Fix <verification@aura.test>",
        "to": ["delivered@resend.dev"],
        "subject": "Confirm your Aura email address",
        "text": "Verification body",
        "reply_to": "support@aura.test",
    }


def test_email_verification_uses_transactional_delivery(app, monkeypatch):
    captured = {}

    def fake_delivery(**kwargs):
        captured.update(kwargs)
        return EmailDeliveryResult(
            success=True,
            provider_message_id="email_verification_123",
        )

    monkeypatch.setattr(
        "security.email_verification.send_transactional_email",
        fake_delivery,
    )

    with app.app_context():
        app.config.update(
            SERVER_NAME="aura.example",
            PREFERRED_URL_SCHEME="https",
            MAIL_SUPPRESS_SEND=False,
        )
        user = _create_user()
        delivered = send_email_verification(user)

    assert delivered is True
    assert captured["to"] == "verification@example.com"
    assert captured["subject"] == "Confirm your Aura email address"
    assert "https://aura.example/auth/verify-email?token=" in captured["text"]
    assert captured["idempotency_key"].startswith("aura-email-verification-")


def test_resend_delivery_fails_closed_without_configuration(app):
    with app.app_context():
        app.config.update(
            RESEND_API_KEY=None,
            RESEND_FROM_EMAIL=None,
            MAIL_DEFAULT_SENDER=None,
            MAIL_SUPPRESS_SEND=False,
        )
        result = send_transactional_email(
            to="delivered@resend.dev",
            subject="Configuration test",
            text="No provider call should be attempted.",
        )

    assert result.success is False
    assert result.error_code == "configuration_incomplete"
