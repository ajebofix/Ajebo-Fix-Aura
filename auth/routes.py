# auth/routes.py

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from urllib.parse import urlparse
from itsdangerous import (
    URLSafeTimedSerializer,
    SignatureExpired,
    BadSignature,
    serializer,
)

from flask import (
    Blueprint,
    request,
    redirect,
    url_for,
    render_template,
    flash,
    session,
    current_app,
)
from flask_login import (
    login_user,
    logout_user,
    login_required,
    current_user,
)
from sqlalchemy import func
from werkzeug.datastructures import auth

from models import (
    AccessCode,
    CarDriver,
    db,
    User,
    CarOwnership,
    Car,
    VehicleEvent,
)

import os


# =====================================================
# BLUEPRINTS
# =====================================================

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

advisor_bp = Blueprint(
    "advisor",
    __name__,
    url_prefix="/admin",
)  # Advisor / Admin console

# ====================================================
# CONFIG
# ====================================================

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


# =====================================================
# ADVISOR ACCESS CODE
# =====================================================

ADVISOR_ACCESS_CODE = os.getenv("ADVISOR_ACCESS_CODE")


# =====================================================
# HELPERS
# =====================================================


def safe_next_url(target):
    """
    Prevent open redirect attacks.
    Allows only internal relative paths.
    """

    if not target:
        return None

    parsed = urlparse(target)

    if parsed.scheme != "":
        return None

    if parsed.netloc != "":
        return None

    return target


def redirect_by_role(user):
    """
    Central role redirect logic.
    """
    if user.is_admin:
        return redirect(url_for("admin.admin_dashboard"))

    if user.is_driver:
        return redirect(url_for("driver.driver_dashboard"))

    return redirect(url_for("dashboard.aura_home"))


def is_locked_out():
    """
    Session-based brute force guard,
    """

    locked_until = session.get("locked_until")

    if not locked_until:
        return False

    try:
        until = datetime.fromisoformat(locked_until)
        return datetime.utcnow() < until
    except Exception:
        return False


def record_failed_login():
    attempts = session.get("login_attempts", 0) + 1
    session["login_attempts"] = attempts

    if attempts >= MAX_LOGIN_ATTEMPTS:
        lock_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
        session["locked_until"] = lock_until.isoformat()


def clear_failed_login():
    session.pop("login_attempts", None)
    session.pop("locked_until", None)


def get_reset_serializer():
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
    )


def generate_reset_token(email):
    serializer = get_reset_serializer()
    return serializer.dumps(email, salt="password-reset-salt")


def verify_reset_token(token, max_age=3600):
    """
    max_age = 1 hour
    """
    serializer = get_reset_serializer()

    try:
        email = serializer.loads(
            token,
            salt="password-reset-salt",
            max_age=max_age,
        )

    except SignatureExpired:
        return None

    except BadSignature:
        return None

    return email


# =====================================================
# SEND RESET EMAIL
# =====================================================


def send_password_reset_email(user):
    try:

        token = generate_reset_token(user.email)

        reset_link = url_for(
            "auth.reset_password",
            token=token,
            _external=True,
        )

        sender = os.getenv("MAIL_USERNAME")
        password = os.getenv("MAIL_PASSWORD")

        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = user.email
        msg["Reply-To"] = sender
        msg["Subject"] = "Aura Password Reset"

        body = f"""
Hello {user.name},

We received a request to reset your Aura password.

Use this secure link:

{reset_link}

This link expires in 1 hour.

If you did not request this, ignore this email.

Ajebo Fix Aura
"""

        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, user.email, msg.as_string())
        server.quit()

        print("EMAIL SENT TO:", user.email)

    except Exception as e:
        print("EMAIL ERROR:", str(e))


# =====================================================
# SIGN UP (CLIENT + ADVISOR + DRIVER)
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
        return render_template("signup.html")

    # Existing user checks
    if User.query.filter(func.lower(User.email) == email).first():
        flash("An account with this email already exists.", "error")
        return render_template("signup.html")

    if User.query.filter_by(phone_number=phone_number).first():
        flash("An account with this phone number already exists.", "error")
        return render_template("signup.html")

    # 🔑 ROLE DECISION (EXPLICIT & SAFE)
    role = "user"
    code_entry = None

    # Optional access code system
    if access_code:
        code_entry = AccessCode.query.filter(
            AccessCode.code == access_code,
            AccessCode.is_used == False,
            AccessCode.expires_at > datetime.utcnow(),
        ).first()

        if code_entry:
            role = code_entry.role

    # Create user
    user = User(
        name=name,
        email=email,
        phone_number=phone_number,
        role=role,
    )
    user.set_password(password)

    db.session.add(user)
    db.session.flush()  # get user.id BEFORE commit

    # LINK DRIVER TO CAR
    if code_entry and role == "driver":
        link = CarDriver(
            user_id=user.id,
            car_id=code_entry.car_id,
            is_active=True,
        )
        db.session.add(link)
        code_entry.is_used = True

    db.session.commit()

    if role == "admin":
        flash("Advisor account created successfully.", "success")
    elif role == "driver":
        flash("Driver account created successfully.", "success")
    else:
        flash("Account created successfully.", "success")

    return redirect(url_for("auth.login"))


# =====================================================
# LOGIN
# =====================================================


@auth_bp.route("/login", methods=["GET", "POST"])
def login():

    if current_user.is_authenticated:
        return redirect_by_role(current_user)

    if request.method == "GET":
        return render_template("login.html")

    # Brute force lock
    if is_locked_out():
        flash("Too many failed attempts. Try again later.", "error")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "").strip()
    remember = bool(request.form.get("remember"))

    if not email or not password:
        flash("Email and password are required.", "error")
        return render_template("login.html")

    user = User.query.filter(func.lower(User.email) == email).first()

    if not user or not user.check_password(password):
        flash("Invalid email or password.", "error")
        return render_template("login.html")

    # Success
    clear_failed_login()

    login_user(
        user,
        remember=remember,
        duration=timedelta(days=30),
    )

    session["last_activity"] = datetime.utcnow().isoformat()

    flash("Signed in successfully.", "success")

    # Safe next redirect
    next_page = safe_next_url(request.args.get("next"))

    if next_page:
        return redirect(next_page)

    return redirect_by_role(user)


# =====================================================
# LOGOUT
# =====================================================


@auth_bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout():

    logout_user()
    session.clear()

    flash("You’ve been signed out.", "info")
    return redirect(url_for("auth.login"))


# ====================================================
# FORGOT PASSWORD
# ====================================================


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():

    if request.method == "GET":
        return render_template("auth/forgot_password.html")

    email = request.form.get("email", "").strip().lower()

    user = User.query.filter(func.lower(User.email) == email).first()

    # Quiet response for privacy
    if user:
        send_password_reset_email(user)

    flash(
        "If that account exists, reset instructions will be sent.",
        "info",
    )

    return redirect(url_for("auth.login"))


# =====================================================
# CHANGE PASSWORD (LOGGED IN)
# ====================================================


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

    if len(new_password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("auth.change_password"))

    if new_password != confirm_password:
        flash("Passwords do not match.", "error")
        return redirect(url_for("auth.change_password"))

    current_user.set_password(new_password)
    db.session.commit()

    flash("Password changed successfully.", "success")
    return redirect_by_role(current_user)


# =====================================================
# RESET PASSWORD WITH TOKEN
# =====================================================


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):

    email = verify_reset_token(token)

    if not email:
        flash("Reset link is invalid or expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    user = User.query.filter(func.lower(User.email) == email).first()

    if not user:
        flash("Invalid reset request.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "GET":
        return render_template(
            "auth/reset_password.html",
            token=token,
        )

    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if len(new_password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(request.url)

    if new_password != confirm_password:
        flash("Passwords do not match.", "error")
        return redirect(request.url)

    user.set_password(new_password)
    db.session.commit()

    flash("Password reset successful. You can now sign in.", "success")
    return redirect(url_for("auth.login"))


# =====================================================
# ADVISOR / ADMIN DASHBOARD (AUTHORITATIVE)
# =====================================================


@advisor_bp.route("/dashboard", methods=["GET"])
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        flash("Advisor authority required.", "error")
        return redirect_by_role(current_user)

    total_events = db.session.query(func.count(VehicleEvent.id)).scalar()

    archived_events = (
        db.session.query(func.count(VehicleEvent.id))
        .filter(VehicleEvent.is_deleted.is_(True))
        .scalar()
    )

    stats = {
        "clients": db.session.query(func.count(User.id))
        .filter(User.role == "user")
        .scalar(),
        "vehicles": db.session.query(func.count(Car.id)).scalar(),
        "active_care_assignments": db.session.query(func.count(CarOwnership.id))
        .filter(CarOwnership.is_active.is_(True))
        .scalar(),
        # 🔑 TEMPLATE-COMPATIBLE STRUCTURE
        "events": {
            "total": total_events,
            "archived": archived_events,
            "active": total_events - archived_events,
        },
    }

    return render_template(
        "admin/dashboard.html",
        stats=stats,
    )
