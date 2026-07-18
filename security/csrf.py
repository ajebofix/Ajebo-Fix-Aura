"""Application-wide CSRF protection for Aura.

The implementation supports regular HTML forms and same-origin JSON requests
without coupling route modules to Flask-WTF. Tokens are stored in Flask's
signed session and compared using constant-time equality.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable

from flask import Flask, abort, request, session

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_SESSION_KEY = "_csrf_token"
_HEADER_NAME = "X-CSRF-Token"


def generate_csrf_token() -> str:
    token = session.get(_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_SESSION_KEY] = token
    return token


def _submitted_token() -> str:
    return (
        request.headers.get(_HEADER_NAME, "")
        or request.form.get("csrf_token", "")
    )


def _is_exempt(endpoint: str | None, exemptions: set[str]) -> bool:
    return bool(endpoint and endpoint in exemptions)


def init_csrf(app: Flask, *, exemptions: set[str] | None = None) -> None:
    exempt_endpoints = exemptions or set()

    app.jinja_env.globals["csrf_token"] = generate_csrf_token

    @app.before_request
    def protect_unsafe_requests():
        if request.method in _SAFE_METHODS:
            return None

        if _is_exempt(request.endpoint, exempt_endpoints):
            return None

        expected = session.get(_SESSION_KEY, "")
        supplied = _submitted_token()

        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            abort(400, description="Invalid or missing CSRF token.")

        return None


def csrf_exempt(view: Callable) -> Callable:
    """Marker retained for future webhook-specific exemption wiring."""
    setattr(view, "_csrf_exempt", True)
    return view
