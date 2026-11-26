"""Closed-by-default staff authentication foundation for the live Prince pilot.

Only an explicitly approved, minimal workshop surface is available.
Finance requires separate release approval. Private contacts, clinical and unrelated Aura routes remain closed.
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
from partnerships.prince_staging_portal import ALLOWED_STATIC


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


# Explicit route names rather than a blueprint wildcard.
WORKSHOP_ENDPOINTS = frozenset({
    "prince_pilot.operator_workspace", "prince_pilot.principal_management",
    "prince_pilot.intake", "prince_pilot.assigned_jobs", "prince_pilot.job_detail",
    "prince_pilot.job_entries", "prince_pilot.record_job_entry",
    "prince_pilot.principal_review_entry", "prince_pilot.principal_assign_job",
    "prince_pilot.estimate_workspace", "prince_pilot.estimate_history",
    "prince_pilot.create_estimate_version", "prince_pilot.approve_estimate",
})


AUTHORIZATION_ENDPOINTS = frozenset({
    "prince_pilot.customer_authorization_history",
    "prince_pilot.record_customer_authorization",
    "prince_pilot.principal_job_lifecycle",
    "prince_pilot.principal_transition_job",
})


FINANCE_ENDPOINTS = frozenset({
    "prince_pilot.print_estimate", "prince_pilot.invoice_workspace",
    "prince_pilot.invoice_detail", "prince_pilot.issue_invoice",
    "prince_pilot.payment_history", "prince_pilot.report_payment",
    "prince_pilot.review_payment", "prince_pilot.issue_receipt",
    "prince_pilot.reverse_payment_allocation", "prince_pilot.print_receipt",
    "prince_pilot.principal_finance", "prince_pilot.principal_finance_data",
})


def workshop_release_ready():
    """A deployment flag alone never authorizes customer intake."""
    if (not current_app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False)
            or not current_app.config.get("PRINCE_LIVE_WORKSHOP_APPROVED", False)
            or not _dedicated_environment()):
        return False
    approved = os.getenv("PRINCE_LIVE_PRIVACY_NOTICE_VERSION", "").strip()
    if not approved or len(approved) > 48 or approved.startswith(("demo", "synthetic")):
        return False
    tenant = PrinceTenant.query.populate_existing().filter_by(slug="prince", active=True).one_or_none()
    return bool(tenant and tenant.approved_privacy_notice_version == approved)


def live_authorization_ready():
    return bool(workshop_release_ready()
                and current_app.config.get("PRINCE_LIVE_AUTHORIZATION_APPROVED", False))


def live_finance_ready():
    return bool(live_authorization_ready()
                and current_app.config.get("PRINCE_LIVE_FINANCE_APPROVED", False))


def live_workshop_request_allowed():
    return bool(
        (request.endpoint in WORKSHOP_ENDPOINTS
         or (request.endpoint in AUTHORIZATION_ENDPOINTS and live_authorization_ready())
         or (request.endpoint in FINANCE_ENDPOINTS and live_finance_ready()))
        and request.view_args and request.view_args.get("slug") == "prince"
        and _authorized_staff() and workshop_release_ready()
    )


def register_prince_live_portal(app):
    app.register_blueprint(live_bp)

    @app.before_request
    def live_lockdown():
        if not app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False):
            if app.config.get("APP_ENV") == "prince-live-pilot":
                abort(503)
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
        if live_workshop_request_allowed():
            return None
        if (request.endpoint == "static" and request.path in ALLOWED_STATIC
                and _authorized_staff() and workshop_release_ready()):
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
    member = _staff_member(current_user)
    if workshop_release_ready() and member is not None:
        endpoint = ("prince_pilot.principal_management" if member.role == "principal"
                    else "prince_pilot.operator_workspace")
        return redirect(url_for(endpoint, slug="prince"), code=303)
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
