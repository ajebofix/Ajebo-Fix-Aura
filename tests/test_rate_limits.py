from __future__ import annotations


def _csrf_token(client) -> str:
    client.get("/auth/signup")
    with client.session_transaction() as session:
        return session["_csrf_token"]


def _invalid_signup(client, token: str):
    return client.post(
        "/auth/signup",
        data={
            "csrf_token": token,
            "name": "Rate Limit Test",
            "email": "rate-limit@example.com",
            "phone_number": "",
            "password": "Password123",
        },
    )


def test_signup_is_rate_limited_by_account_identity(client):
    token = _csrf_token(client)

    for _ in range(3):
        response = _invalid_signup(client, token)
        assert response.status_code == 400

    blocked = _invalid_signup(client, token)

    assert blocked.status_code == 429
    assert b"temporarily paused" in blocked.data


def test_rate_limit_headers_are_exposed(client):
    token = _csrf_token(client)
    response = _invalid_signup(client, token)

    assert response.status_code == 400
    assert response.headers.get("X-RateLimit-Limit") == "3"
    assert response.headers.get("X-RateLimit-Remaining") == "2"


def test_sensitive_endpoints_are_registered_with_limits(app):
    expected = {
        "auth.login",
        "auth.signup",
        "auth.forgot_password",
        "auth.reset_password",
        "chat.chat",
        "cars.book_consultation",
        "stewardship.request_stewardship_transfer",
        "stewardship.advisor_reassign_stewardship",
    }

    assert expected.issubset(app.view_functions)
