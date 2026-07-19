"""Email verification for Aura accounts.

New accounts receive a signed, time-limited verification link. Existing
accounts are grandfathered by the accompanying migration so deployment does
not unexpectedly lock current clients or advisors out of established flows.
"""

from __future__ import annotations

import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func

from extensions import db
from models import User


email_verification_bp = Blueprint(
    "email_verification",
    __name__,
    url_prefix="/auth",
)

EMAIL_VERIFICATION_MAX_AGE = 24 * 60 * 60
_EMAIL_VERIFICATION_SALT = "aura-email-verification-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def generate_email_verification_token(user: User) -> str:
    """Create a token tied to the user's current identity and password state."""

    payload = {
        "user_id": user.id,
        "email": user.email.strip().lower(),
        "password_hash": user.password_hash,
    }
    return _serializer().dumps(payload, salt=_EMAIL_VERIFICATION_SALT)


def verify_email_token(
    token: str,
    *,
    max_age: int = EMAIL_VERIFICATION_MAX_AGE,
) -> User | None:
    try:
        payload = _serializer().loads(
            token,
            salt=_EMAIL_VERIFICATION_SALT,
            max_age=max_age,
        )
    except (SignatureExpired, BadSignature):
        return None

    if not isinstance(payload, dict):
        return None

    try:
        user_id = int(payload.get("user_id"))
    except (TypeError, ValueError):
        return None

    user = db.session.get(User, user_id)
    if not user or not user.is_active:
        return None

    expected_email = user.email.strip().lower()
    if payload.get("email") != expected_email:
        return None

    if payload.get("password_hash") != user.password_hash:
        return None

    return user


def _safe_next_url(target: str | None) -> str | None:
    if not target:
        return None

    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


def send_email_verification(user: User) -> bool:
    sender = current_app.config.get("MAIL_USERNAME")
    password = current_app.config.get("MAIL_PASSWORD")

    if current_app.config.get("MAIL_SUPPRESS_SEND"):
        current_app.logger.info(
            "Email verification delivery suppressed",
            extra={"user_id": user.id},
        )
        return True

    if not sender or not password:
        current_app.logger.warning(
            "Email verification configuration is incomplete",
            extra={"user_id": user.id},
        )
        return False

    token = generate_email_verification_token(user)
    verification_link = url_for(
        "email_verification.verify_email",
        token=token,
        _external=True,
        _scheme=current_app.config.get("PREFERRED_URL_SCHEME", "https"),
    )

    body = f"""Hello {user.name or 'there'},

Please confirm your email address to activate protected Aura actions.

{verification_link}

This link expires in 24 hours and can be used only for your current account.
If you did not create this account, you can ignore this message.

Ajebo Fix Aura
"""

    message = MIMEText(body, "plain", "utf-8")
    message["From"] = current_app.config.get(
        "MAIL_DEFAULT_SENDER",
        "Ajebo Fix Aura <ajebofix@gmail.com>",
    )
    message["To"] = user.email
    message["Reply-To"] = sender
    message["Subject"] = "Confirm your Aura email address"

    try:
        with smtplib.SMTP(
            current_app.config.get("MAIL_SERVER", "smtp.gmail.com"),
            current_app.config.get("MAIL_PORT", 587),
            timeout=current_app.config.get("MAIL_TIMEOUT", 30),
        ) as server:
            if current_app.config.get("MAIL_USE_TLS", True):
                server.starttls()
            server.login(sender, password)
            server.sendmail(sender, user.email, message.as_string())
    except Exception:
        current_app.logger.exception(
            "Email verification delivery failed",
            extra={"user_id": user.id},
        )
        return False

    return True


@email_verification_bp.get("/verify-email")
def verify_email():
    token = request.args.get("token", "").strip()
    user = verify_email_token(token) if token else None

    if not user:
        flash(
            "This verification link is invalid or has expired. Please request a new one.",
            "error",
        )
        return redirect(url_for("auth.login"))

    if user.email_verified_at is None:
        user.email_verified_at = datetime.utcnow()
        db.session.commit()
        flash("Your email address has been verified.", "success")
    else:
        flash("Your email address is already verified.", "info")

    if current_user.is_authenticated and current_user.id == user.id:
        next_page = _safe_next_url(request.args.get("next"))
        if next_page:
            return redirect(next_page)

    return redirect(url_for("auth.login"))


@email_verification_bp.get("/verification-required")
@login_required
def verification_required():
    if current_user.email_verified_at is not None:
        return redirect(url_for("dashboard.aura_home"))

    return render_template(
        "auth/verification_required.html",
        email=current_user.email,
    )


@email_verification_bp.post("/resend-verification")
@login_required
def resend_verification():
    if current_user.email_verified_at is not None:
        flash("Your email address is already verified.", "info")
        return redirect(url_for("dashboard.aura_home"))

    delivered = send_email_verification(current_user)
    if delivered:
        flash("A fresh verification link has been sent.", "success")
    else:
        flash(
            "Aura could not send the verification email right now. Please try again shortly.",
            "error",
        )

    return redirect(url_for("email_verification.verification_required"))


def _verification_response():
    message = (
        "Please verify your email address before continuing with this protected action."
    )

    if request.is_json or request.accept_mimetypes.best == "application/json":
        return (
            jsonify(
                {
                    "error": "email_verification_required",
                    "message": message,
                }
            ),
            403,
        )

    flash(message, "info")
    return redirect(url_for("email_verification.verification_required"))


def _apply_verification_gate(app: Flask, endpoint: str) -> None:
    view = app.view_functions.get(endpoint)
    if view is None:
        app.logger.info("Email-verification endpoint not present: %s", endpoint)
        return

    @wraps(view)
    def protected_view(*args, **kwargs):
        if (
            current_user.is_authenticated
            and current_user.email_verified_at is None
        ):
            return _verification_response()
        return view(*args, **kwargs)

    app.view_functions[endpoint] = protected_view


def register_email_verification_gates(app: Flask) -> None:
    """Require verification for commercial and sensitive data actions."""

    protected_endpoints = {
        "cars.book_consultation",
        "cars.request_priority_scheduling",
        "cars.request_emergency_review",
        "cars.vehicle_report",
        "cars.vehicle_records_pdf",
        "cars.assessment_report",
    }

    for endpoint in protected_endpoints:
        _apply_verification_gate(app, endpoint)


def init_email_verification(app: Flask) -> None:
    """Send the first verification email after a successful signup."""

    @app.after_request
    def send_signup_verification(response):
        if (
            request.endpoint == "auth.signup"
            and request.method == "POST"
            and 300 <= response.status_code < 400
        ):
            email = request.form.get("email", "").strip().lower()
            user = User.query.filter(func.lower(User.email) == email).first()
            if user and user.email_verified_at is None:
                send_email_verification(user)

        return response
