"""Email verification for Aura accounts.

New accounts receive a signed, time-limited verification link. Existing
accounts are grandfathered by the accompanying migration so deployment does
not unexpectedly lock current clients or advisors out of established flows.
"""

from __future__ import annotations

from datetime import datetime
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
from services.email_delivery import (
    build_email_idempotency_key,
    send_transactional_email,
)


# SQLAlchemy declarative models support adding mapped columns after class
# declaration. Keeping this compatibility mapping here avoids a high-risk,
# unrelated rewrite of the current monolithic models.py; a future model-split
# migration can move the declaration beside the rest of User's fields.
if not hasattr(User, "email_verified_at"):
    User.email_verified_at = db.Column(db.DateTime, nullable=True)


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

    # Once email_verified_at is set, every previously issued token is consumed.
    if user.email_verified_at is not None:
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
    if current_app.config.get("MAIL_SUPPRESS_SEND"):
        current_app.logger.info(
            "Email verification delivery suppressed",
            extra={"user_id": user.id},
        )
        return True

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

    result = send_transactional_email(
        to=user.email,
        subject="Confirm your Aura email address",
        text=body,
        idempotency_key=build_email_idempotency_key(
            "email-verification",
            user.id,
            user.email.strip().lower(),
            user.password_hash,
            token,
        ),
    )

    if not result.success:
        current_app.logger.warning(
            "Email verification delivery failed",
            extra={
                "user_id": user.id,
                "provider": "resend",
                "error_code": result.error_code,
            },
        )

    return result.success


@email_verification_bp.get("/verify-email")
def verify_email():
    token = request.args.get("token", "").strip()
    user = verify_email_token(token) if token else None

    if not user:
        flash(
            "This verification link is invalid, expired, or already used.",
            "error",
        )
        return redirect(url_for("auth.login"))

    user.email_verified_at = datetime.utcnow()
    db.session.commit()
    flash("Your email address has been verified.", "success")

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
        if current_user.is_authenticated and current_user.email_verified_at is None:
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
