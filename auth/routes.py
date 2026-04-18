# auth/routes.py

from datetime import datetime, timedelta

from flask import (
    Blueprint,
    request,
    redirect,
    url_for,
    render_template,
    flash,
)
from flask_login import (
    login_user,
    logout_user,
    login_required,
    current_user,
)
from urllib.parse import urlparse
from sqlalchemy import func

from models import AccessCode, CarDriver, db, User, CarOwnership, Car, VehicleEvent
from utils.auth_helpers import require_admin
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


# =====================================================
# ADVISOR ACCESS CODE
# =====================================================

ADVISOR_ACCESS_CODE = os.getenv("ADVISOR_ACCESS_CODE")
# DRIVER_ACCESS_CODE = "AJEBO-DRIVER-2026"


# =====================================================
# SIGN UP (CLIENT + ADVISOR)
# =====================================================


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.aura_home"))

    if request.method == "GET":
        return render_template("signup.html")

    data = request.form

    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    phone_number = data.get("phone_number", "").strip()
    password = data.get("password", "")
    access_code = data.get("access_code", "").strip()

    if not all([name, email, phone_number, password]):
        flash("All fields are required.", "error")
        return render_template("signup.html")

    if User.query.filter_by(email=email).first():
        flash("An account with this email already exists.", "error")
        return render_template("signup.html")

    if User.query.filter_by(phone_number=phone_number).first():
        flash("An account with this phone number already exists.", "error")
        return render_template("signup.html")

    # 🔑 ROLE DECISION (EXPLICIT & SAFE)
    role = "user"

    code_entry = None

    code_entry = AccessCode.query.filter(
        AccessCode.code == access_code,
        AccessCode.is_used == False,
        AccessCode.expires_at > datetime.utcnow(),
    ).first()

    if code_entry:
        role = code_entry.role
    else:
        role = "user"

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
        link = CarDriver(user_id=user.id, car_id=code_entry.car_id)

        db.session.add(link)
        code_entry.is_used = True

    db.session.commit()

    if role == "admin":
        msg = "Advisor account created successfully."
    elif role == "driver":
        msg = "Driver account created successfully."
    else:
        msg = "Your account has created."

    flash(msg, "success")

    return redirect(url_for("auth.login"))


# =====================================================
# LOGIN
# =====================================================


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for("advisor.admin_dashboard"))
        return redirect(url_for("dashboard.aura_home"))

    if request.method == "GET":
        return render_template("login.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        flash("Email and password are required.", "error")
        return render_template("login.html")

    user = User.query.filter_by(email=email).first()

    if not user or not user.check_password(password):
        flash("Invalid email or password.", "error")
        return render_template("login.html")

    login_user(user)

    flash("You’re signed in.", "success")

    # Safe redirect handling
    next_page = request.args.get("next")
    if next_page:
        parsed = urlparse(next_page)
        if parsed.netloc == "":
            return redirect(next_page)

    # 🔑 ROLE-BASED REDIRECT (FINAL)
    if user.is_admin:
        return redirect(url_for("advisor.admin_dashboard"))

    if user.is_driver:
        return redirect(url_for("driver.driver_dashboard"))

    return redirect(url_for("dashboard.aura_home"))


# =====================================================
# LOGOUT
# =====================================================


@auth_bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    logout_user()
    flash("You’ve been signed out.", "info")
    return redirect(url_for("auth.login"))


# =====================================================
# ADVISOR / ADMIN DASHBOARD (AUTHORITATIVE)
# =====================================================


@advisor_bp.route("/dashboard", methods=["GET"])
@login_required
def admin_dashboard():
    if not require_admin():
        flash("Advisor authority required.", "error")
        return redirect(url_for("dashboard.aura_home"))

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
