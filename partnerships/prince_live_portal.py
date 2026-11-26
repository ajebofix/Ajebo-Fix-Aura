"""Closed-by-default staff authentication foundation for the live Prince pilot.

No customer, workshop, financial or global Aura data endpoints are exposed.
The release remains blocked until a separately reviewed workflow is available.
"""
import os
from uuid import UUID

from flask import (
    Blueprint, abort, current_app, redirect, render_template, request, session, url_for,
)
from flask_login import current_user, login_fresh, login_user, logout_user
from sqlalchemy import func

from models import User
from partnerships.prince_models import PrinceMembership, PrinceTenant
from security.rate_limits import limiter


live_bp = Blueprint("prince_live", __name__, url_prefix="/prince-staff")
DEMO_PROJECT = "3ed38d6c-6411-4890-861b-70cea583add2"
DEMO_ENVIRONMENT = "aef601d6-38f3-4d16-99f0-aa908c87b3f2"


def _valid_id(value):
    try:
        return str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _dedicated_environment():
    project = os.getenv("PRINCE_LIVE_EXPECTED_PROJECT_ID")
    environment = os.getenv("PRINCE_LIVE_EXPECTED_ENVIRONMENT_ID")
    return (
        current_app.config.get("APP_ENV") == "prince-live-pilot"
        and not current_app.config.get("PRINCE_STAGING_LOCKDOWN", False)
        and _valid_id(project) and _valid_id(environment)
        and project != DEMO_PROJECT and environment != DEMO_ENVIRONMENT
        and project == os.getenv("RAILWAY_PROJECT_ID")
        and environment == os.getenv("RAILWAY_ENVIRONMENT_ID")
        and not current_app.config.get("AURA_PRINCE_PILOT_API_ENABLED", False)
        and os.getenv("PRINCE_STAGING_SYNTHETIC_SEED", "").strip().lower()
        not in {"true", "1", "yes", "on"}
    )


def _staff_member(user):
    if (user is None or not user.is_active or user.role != "user"
            or not user.email or user.email_verified_at is None
            or user.email.lower().startswith("prince-demo-")
            or user.email.lower().endswith(".invalid")):
        return None
    tenant = PrinceTenant.query.populate_existing().filter_by(
        slug="prince", active=True,
    ).one_or_none()
    if tenant is None:
        return None
    return PrinceMembership.query.populate_existing().filter(
        PrinceMembership.tenant_id == tenant.id,
        PrinceMembership.user_id == user.id,
        PrinceMembership.active.is_(True),
        PrinceMembership.role.in_(("principal", "operator")),
    ).one_or_none()


def _authorized_staff():
    if (not current_user.is_authenticated or not login_fresh()
            or session.get("prince_live_staff_id") != current_user.id):
        return False
    user = User.query.populate_existing().filter_by(id=current_user.id).one_or_none()
    return _staff_member(user) is not None


def register_prince_live_portal(app):
    app.register_blueprint(live_bp)

    @app.before_request
    def live_lockdown():
        if not app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False):
            return None
        if not _dedicated_environment():
            abort(503)
        if request.path == "/healthz" and request.method in {"GET", "HEAD"}:
            return None
        if request.endpoint == "prince_live.sign_in":
            return None
        if request.endpoint in {"prince_live.landing", "prince_live.sign_out"}:
            if _authorized_staff():
                return None
        abort(404)

    @app.after_request
    def live_private_headers(response):
        if app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False):
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response


@live_bp.route("", methods=["GET", "POST"])
@limiter.limit("5 per minute; 15 per hour", methods=["POST"])
def sign_in():
    if not current_app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False):
        abort(404)
    if not _dedicated_environment():
        abort(503)
    if _authorized_staff():
        return redirect(url_for("prince_live.landing"), code=303)
    if request.method == "GET":
        return render_template("partnerships/prince_live_login.html")
    if request.content_length and request.content_length > 8192:
        abort(413)
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    user = None
    if 0 < len(email) <= 120 and 0 < len(password) <= 512:
        candidates = User.query.filter(func.lower(User.email) == email).limit(2).all()
        if len(candidates) == 1:
            user = candidates[0]
    if _staff_member(user) is None or not user.check_password(password):
        return render_template("partnerships/prince_live_login.html",
                               error="Invalid staff access details."), 401
    session.clear()
    if not login_user(user, remember=False, fresh=True):
        abort(401)
    session["prince_live_staff_id"] = user.id
    session.permanent = False
    return redirect(url_for("prince_live.landing"), code=303)


@live_bp.get("/ready")
def landing():
    if not current_app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False):
        abort(404)
    if not _dedicated_environment() or not _authorized_staff():
        abort(404)
    return render_template("partnerships/prince_live_login.html", ready=True)


@live_bp.post("/sign-out")
def sign_out():
    if not current_app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False):
        abort(404)
    if not _dedicated_environment() or not _authorized_staff():
        abort(404)
    logout_user()
    session.clear()
    return redirect(url_for("prince_live.sign_in"), code=303)
