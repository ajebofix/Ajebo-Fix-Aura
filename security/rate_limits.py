"""Centralized rate limiting for Aura's sensitive endpoints.

The limiter uses Redis when ``RATE_LIMIT_STORAGE_URI`` or ``REDIS_URL`` is
configured. Development and tests fall back to in-memory storage so a missing
Redis service does not take the application offline.
"""

from __future__ import annotations

import hashlib
import os

from flask import Flask, jsonify, render_template, request
from flask_login import current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from limits.errors import ConfigurationError


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    headers_enabled=True,
)


def _storage_uri() -> str:
    return (
        os.getenv("RATE_LIMIT_STORAGE_URI")
        or os.getenv("REDIS_URL")
        or "memory://"
    )


def _normalized_identity() -> str:
    """Return a privacy-preserving account key or the request IP.

    Raw email addresses are never used as Redis keys. Authenticated requests
    use the stable user ID; anonymous authentication requests use a SHA-256
    digest of the submitted email address.
    """

    if current_user.is_authenticated:
        return f"user:{current_user.get_id()}"

    payload = request.get_json(silent=True) if request.is_json else None
    email = request.form.get("email", "") or (payload or {}).get("email", "")
    normalized_email = str(email).strip().lower()

    if normalized_email:
        digest = hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()
        return f"account:{digest}"

    return f"ip:{get_remote_address()}"


def _endpoint_key() -> str:
    return f"{request.endpoint}:{_normalized_identity()}"


def _apply_limit(
    app: Flask,
    endpoint: str,
    limit_value: str,
    *,
    key_func=_endpoint_key,
) -> None:
    """Wrap one registered endpoint without modifying its route module."""

    view = app.view_functions.get(endpoint)
    if view is None:
        app.logger.info("Rate-limit endpoint not present: %s", endpoint)
        return

    app.view_functions[endpoint] = limiter.limit(
        limit_value,
        key_func=key_func,
        methods=["POST"],
        per_method=True,
    )(view)


def register_rate_limits(app: Flask) -> None:
    """Apply explicit limits after all blueprints have been registered."""

    endpoint_limits = {
        # Authentication and invitation-code redemption.
        "auth.login": "5 per minute; 20 per hour",
        "auth.signup": "3 per hour",
        "auth.forgot_password": "3 per hour",
        "auth.reset_password": "5 per hour",
        "auth.change_password": "5 per hour",
        "email_verification.resend_verification": "3 per hour",
        # AI and commercial actions.
        "chat.chat": "30 per minute; 300 per day",
        "cars.book_consultation": "5 per hour",
        "cars.request_priority_scheduling": "3 per hour",
        "cars.request_emergency_review": "3 per hour",
        # High-risk ownership changes.
        "stewardship.request_stewardship_transfer": "3 per hour",
        "stewardship.advisor_reassign_stewardship": "10 per hour",
        # Vehicle-intelligence provider calls.
        "admin.decode_vehicle_vin": "20 per hour",
        "admin.add_vehicle_dtc": "30 per hour",
    }

    for endpoint, limit_value in endpoint_limits.items():
        _apply_limit(app, endpoint, limit_value)


def init_rate_limiting(app: Flask) -> None:
    storage_uri = app.config.get("RATELIMIT_STORAGE_URI") or _storage_uri()

    app.config.setdefault("RATELIMIT_ENABLED", True)
    app.config.setdefault("RATELIMIT_HEADERS_ENABLED", True)
    app.config.setdefault("RATELIMIT_STRATEGY", "fixed-window")
    app.config["RATELIMIT_STORAGE_URI"] = storage_uri

    if storage_uri == "memory://" and app.config.get("APP_ENV") == "production":
        app.logger.warning(
            "Aura rate limiting is using in-memory storage in production. "
            "Configure RATE_LIMIT_STORAGE_URI or REDIS_URL for shared limits."
        )

    try:
        limiter.init_app(app)
    except ConfigurationError:
        app.logger.exception("Invalid rate-limit storage configuration")
        raise

    @app.errorhandler(429)
    def rate_limit_exceeded(_error):
        message = (
            "Aura has temporarily paused this action to protect your account. "
            "Please wait a moment before trying again."
        )

        if request.is_json or request.accept_mimetypes.best == "application/json":
            response = jsonify({"error": "rate_limit_exceeded", "message": message})
            response.status_code = 429
            response.headers["Retry-After"] = "60"
            return response

        return render_template("errors/429.html", message=message), 429
