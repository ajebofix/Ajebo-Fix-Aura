from __future__ import annotations


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

    assert "/auth/auth/login" not in rules
    assert "/cars/cars/" not in rules
    assert "/dashboard/dashboard/" not in rules


def test_root_does_not_disclose_internal_route_inventory(client):
    response = client.get("/")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload == {"status": "ok", "service": "Ajebo Fix Aura"}
