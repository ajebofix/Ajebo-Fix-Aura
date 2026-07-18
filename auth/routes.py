# auth/routes.py

from __future__ import annotations

import os
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func

from models import (
    AccessCode,
    Car,
    CarDriver,
    CarOwnership,
    User,
    VehicleEvent,
    db,
)


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
advisor_bp = Blueprint("advisor", __name__, url_prefix="/admin")

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
PASSWORD_RESET_MAX_AGE = 3600


def safe_next_url(target: str | None) -> str | None:
    """Allow redirects only to internal absolute paths."""

    if not target:
        return None

    parsed = urlparse(target)

    if parsed.scheme or parsed.netloc:
        return None

    if not target.startswith("/") or target.startswith("//"):
        return None

    return target


def redirect_by_role(user):
    if user.is_admin:
        return redirect(url_for("admin.admin_dashboard"))

    if user.is_driver:
        return redirect(url_for("driver.driver_dashboard"))

    return redirect(url_for("dashboard.aura_home"))


def is_locked_out() -> bool:
    locked_until = session.get("locked_until")

    if not locked_until:
        return False

    try:
        until = datetime.fromisoformat(locked_until)
    except (TypeError, ValueError):
        session.pop("locked_until", None)
        return False

    if datetime.utcnow() >= until:
        clear_failed_login()
        return False

    return True


def record_failed_login() -> None:
    attempts = int(session.get("login_attempts", 0)) + 1
    session["login_attempts"] = attempts

    if attempts >= MAX_LOGIN_ATTEMPTS:
        lock_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
        session["locked_until"] = lock_until.isoformat()


def clear_failed_login() -> None:
    session.pop("login_attempts", None)
    session.pop("locked_until", None)


def get_reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def generate_reset_token(user: User) -> str:
    """Include the current password hash so a token becomes single-state use."""

    payload = {
        "email": user.email,
        "password_hash": user.password_hash,
    }
    return get_reset_serializer().dumps(payload, salt="password-reset-salt")


def verify_reset_token(token: str, max_age: int = PASSWORD_RESET_MAX_AGE) -> User | None:
    try:
        payload = get_reset_serializer().loads(
            token,
            salt="password-reset-salt",
            max_age=max_age,
        )
    except (SignatureExpired, BadSignature):
        return None

    if not isinstance(payload, dict):
        return None

    email = str(payload.get("email", "")).strip().lower()
    password_hash = payload.get("password_hash")

    user = User.query.filter(func.lower(User.email) == email).first()

    if not user or not user.is_active:
        return None

    if password_hash != user.password_hash:
        return None

    return user


def send_password_reset_email(user: User) -> bool:
    sender = current_app.config.get("MAIL_USERNAME")
    password = current_app.config.get("MAIL_PASSWORD")

    if not sender or not password:
        current_app.logger.error("Password reset email configuration is incomplete")
        return False

    token = generate_reset_token(user)
    reset_link = url_for(
        "auth.reset_password",
        token=token,
        _external=True,
        _scheme=current_app.config.get("PREFERRED_URL_SCHEME", "https"),
    )

    msg = MIMEMultipart()
    msg["From"] = current_app.config.get(
        "MAIL_DEFAULT_SENDER",
        "Ajebo Fix Aura <ajebofix@gmail.com>",
    )
    msg["To"] = user.email
    msg["Reply-To"] = sender
    msg["Subject"] = "Aura Password Reset"

    body = f"""
Hello {user.name or 'there'},

We received a request to reset your Aura password.

Use this secure link:

{reset_link}

This link expires in 1 hour and becomes invalid after your password changes.

If you did not request this, ignore this email.

Ajebo Fix Aura
"""

    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(
            current_app.config.get("MAIL_SERVER", "smtp.gmail.com"),
            current_app.config.get("MAIL_PORT", 587),
            timeout=current_app.config.get("MAIL_TIMEOUT", 30),
        ) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, user.email, msg.as_string())
    except Exception:
        current_app.logger.exception("Password reset email delivery failed")
        return False

    return True


def password_is_acceptable(password: str) -> bool:
    return (
        len(password) >= 10
        and any(char.isalpha() for char in password)
        and any(char.isdigit() for char in password)
    )


# =====================================================
# SIGN UP (CLIENT + DRIVER INVITATION)
# =====================================================


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect_by_role(current_user)

    if request.method == "GET":
        return render_template("signup.html")

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone_number = request.form.get("phone_number", "").strip()
    password = request.form.get("password", "")
    access_code = request.form.get("access_code", "").strip()

    if not all([name, email, phone_number, password]):
        flash("All fields are required.", "error")
        return render_template("signup.html"), 400

    if len(name) > 120 or len(email) > 120 or len(phone_number) > 20:
        flash("One or more fields are too long.", "error")
        return render_template("signup.html"), 400

    if not password_is_acceptable(password):
        flash(
            "Password must be at least 10 characters and include letters and numbers.",
            "error",
        )
        return render_template("signup.html"), 400

    if User.query.filter(func.lower(User.email) == email).first():
        flash("An account with this email already exists.", "error")
        return render_template("signup.html"), 409

    if User.query.filter_by(phone_number=phone_number).first():
        flash("An account with this phone number already exists.", "error")
        return render_template("signup.html"), 409

    role = "user"
    code_entry = None

    if access_code:
        code_entry = AccessCode.query.filter(
            AccessCode.code == access_code,
            AccessCode.is_used.is_(False),
            AccessCode.expires_at > datetime.utcnow(),
        ).first()

        if not code_entry:
            flash("This invitation code is invalid or expired.", "error")
            return render_template("signup.html"), 400

        # Public signup must never mint administrator authority.
        if code_entry.role != "driver":
            current_app.logger.warning(
                "Blocked privileged public signup invitation redemption",
                extra={"access_code_id": code_entry.id, "role": code_entry.role},
            )
            flash("This invitation must be completed by an Aura administrator.", "error")
            return render_template("signup.html"), 403

        role = "driver"

    try:
        user = User(
            name=name,
            email=email,
            phone_number=phone_number,
            role=role,
        )
        user.set_password(password)

        db.session.add(user)
        db.session.flush()

        if code_entry and role == "driver":
            link = CarDriver(
                user_id=user.id,
                car_id=code_entry.car_id,
                is_active=True,
            )
            db.session.add(link)
            code_entry.is_used = True

        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Account creation failed")
        flash("The account could not be created. Please try again.", "error")
        return render_template("signup.html"), 500

    flash(
        "Driver account created successfully."
        if role == "driver"
        else "Account created successfully.",
        "success",
    )
    return redirect(url_for("auth.login"))


# =====================================================
# LOGIN / LOGOUT
# =====================================================


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect_by_role(current_user)

    if request.method == "GET":
        return render_template("login.html")

    if is_locked_out():
        flash("Too many failed attempts. Try again later.", "error")
        return render_template("login.html"), 429

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    remember = bool(request.form.get("remember"))

    if not email or not password:
        flash("Email and password are required.", "error")
        return render_template("login.html"), 400

    user = User.query.filter(func.lower(User.email) == email).first()

    if not user or not user.check_password(password):
        record_failed_login()
        flash("Invalid email or password.", "error")
        return render_template("login.html"), 401

    if not user.is_active:
        record_failed_login()
        flash("This account is not active.", "error")
        return render_template("login.html"), 403

    clear_failed_login()
    session.clear()

    login_user(
        user,
        remember=remember,
        duration=timedelta(days=30),
        fresh=True,
    )

    session["last_activity"] = datetime.utcnow().isoformat()
    session.permanent = True

    flash("Signed in successfully.", "success")

    next_page = safe_next_url(request.args.get("next"))
    if next_page:
        return redirect(next_page)

    return redirect_by_role(user)


@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    flash("You’ve been signed out.", "info")
    return redirect(url_for("auth.login"))


# =====================================================
# PASSWORD MANAGEMENT
# =====================================================


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("auth/forgot_password.html")

    email = request.form.get("email", "").strip().lower()
    user = User.query.filter(func.lower(User.email) == email).first()

    if user and user.is_active:
        send_password_reset_email(user)

    flash(
        "If that account exists, reset instructions will be sent.",
        "info",
    )
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "GET":
        return render_template("auth/change_password.html")

    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not current_user.check_password(current_password):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("auth.change_password"))

    if not password_is_acceptable(new_password):
        flash(
            "Password must be at least 10 characters and include letters and numbers.",
            "error",
        )
        return redirect(url_for("auth.change_password"))

    if new_password != confirm_password:
        flash("Passwords do not match.", "error")
        return redirect(url_for("auth.change_password"))

    current_user.set_password(new_password)
    db.session.commit()

    # Remove remembered/browser session state after a credential change.
    logout_user()
    session.clear()

    flash("Password changed. Please sign in again.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = verify_reset_token(token)

    if not user:
        flash("Reset link is invalid or expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "GET":
        return render_template(
            "auth/reset_password.html",
            token=token,
        )

    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not password_is_acceptable(new_password):
        flash(
            "Password must be at least 10 characters and include letters and numbers.",
            "error",
        )
        return redirect(request.url)

    if new_password != confirm_password:
        flash("Passwords do not match.", "error")
        return redirect(request.url)

    user.set_password(new_password)
    db.session.commit()
    session.clear()

    flash("Password reset successful. You can now sign in.", "success")
    return redirect(url_for("auth.login"))


# =====================================================
# LEGACY ADVISOR DASHBOARD ROUTE
# =====================================================


@advisor_bp.get("/dashboard")
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        flash("Advisor authority required.", "error")
        return redirect_by_role(current_user)

    total_events = db.session.query(func.count(VehicleEvent.id)).scalar() or 0
    archived_events = (
        db.session.query(func.count(VehicleEvent.id))
        .filter(VehicleEvent.is_deleted.is_(True))
        .scalar()
        or 0
    )

    stats = {
        "clients": (
            db.session.query(func.count(User.id))
            .filter(User.role == "user")
            .scalar()
            or 0
        ),
        "vehicles": db.session.query(func.count(Car.id)).scalar() or 0,
        "active_care_assignments": (
            db.session.query(func.count(CarOwnership.id))
            .filter(CarOwnership.is_active.is_(True))
            .scalar()
            or 0
        ),
        "events": {
            "total": total_events,
            "archived": archived_events,
            "active": total_events - archived_events,
        },
    }

    return render_template("admin/dashboard.html", stats=stats)
