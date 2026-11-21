"""Staging-only, deny-by-default Prince demo entry point.

This is intentionally not enabled in production Aura. Both a shared staging
access key and an individual synthetic account password are required. Existing
Aura owner/admin routes stay inaccessible to demo accounts.
"""
from __future__ import annotations

import hmac
import os

from flask import (
    Blueprint, abort, current_app, redirect, render_template,
    request, session, url_for,
)
from flask_login import current_user, login_user, logout_user

from models import User
from partnerships.prince_models import PrinceMembership, PrinceTenant
from security.rate_limits import limiter

prince_staging_bp = Blueprint("prince_staging", __name__, url_prefix="/prince-demo")
DEMO_EMAILS = {
    "principal": "prince-demo-principal@example.invalid",
    "operator-a": "prince-demo-operator-a@example.invalid",
    "operator-b": "prince-demo-operator-b@example.invalid",
}
DEMO_ROLE = {
    "principal": "principal",
    "operator-a": "operator",
    "operator-b": "operator",
}
ALLOWED_STATIC = frozenset({
    "/static/css/prince_pilot.css",
    "/static/js/prince_pilot_operator.js",
    "/static/img/prince-workshop-logo-pilot.jpg",
})


def _is_staging() -> bool:
    return (
        current_app.config.get("APP_ENV") == "staging"
        and os.getenv("RAILWAY_PROJECT_ID") == "3ed38d6c-6411-4890-861b-70cea583add2"
        and os.getenv("RAILWAY_ENVIRONMENT_ID") == "aef601d6-38f3-4d16-99f0-aa908c87b3f2"
    )


def _valid_member(user, actor_key: str | None = None) -> bool:
    if not user or not user.is_active:
        return False
    email = (user.email or "").lower()
    if actor_key is None:
        actor_key = next((k for k, value in DEMO_EMAILS.items()
                          if value == email), None)
    if not actor_key or DEMO_EMAILS.get(actor_key) != email:
        return False
    if user.role != "user":
        return False
    tenant = PrinceTenant.query.filter_by(slug="prince", active=True).first()
    if tenant is None:
        return False
    member = PrinceMembership.query.filter_by(
        tenant_id=tenant.id, user_id=user.id,
        role=DEMO_ROLE[actor_key], active=True,
    ).first()
    return member is not None


def register_prince_staging_portal(app) -> None:
    """Register the allowlist guard before serving the staging demonstration."""
    app.register_blueprint(prince_staging_bp)

    @app.before_request
    def prince_staging_lockdown():
        if not app.config.get("PRINCE_STAGING_LOCKDOWN", False):
            return None
        # A mistakenly enabled lock outside the dedicated staging instance
        # must fail closed rather than make new demo routes available.
        if not _is_staging():
            abort(503)
        if request.path == "/healthz" and request.method in {"GET", "HEAD"}:
            return None
        if request.endpoint == "prince_staging.sign_in":
            return None
        if request.endpoint == "static" and request.path in ALLOWED_STATIC:
            return None
        if request.endpoint == "prince_staging.sign_out":
            if current_user.is_authenticated and session.get("prince_demo_authorized"):
                return None
            abort(404)
        if (not current_user.is_authenticated
                or not session.get("prince_demo_authorized")
                or not _valid_member(current_user)):
            abort(404)
        if (
            (request.endpoint or "").startswith("prince_pilot.")
            and request.view_args
            and request.view_args.get("slug") == "prince"
            and app.config.get("AURA_PRINCE_PILOT_API_ENABLED", False)
        ):
            return None
        abort(404)

    @app.after_request
    def staging_no_referrer(response):
        if app.config.get("PRINCE_STAGING_LOCKDOWN", False):
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response


@prince_staging_bp.route("", methods=["GET", "POST"])
@limiter.limit("5 per minute; 15 per hour", methods=["POST"])
def sign_in():
    if not current_app.config.get("PRINCE_STAGING_LOCKDOWN", False) or not _is_staging():
        abort(404)
    if current_user.is_authenticated and session.get("prince_demo_authorized"):
        if _valid_member(current_user):
            return redirect(url_for("prince_pilot.operator_workspace", slug="prince"))
        logout_user()
        session.clear()
    if request.method == "GET":
        return render_template("partnerships/prince_staging_login.html")
    if request.content_length and request.content_length > 8192:
        abort(413)
    selector = request.form.get("role", "")
    user_pass = request.form.get("password", "")
    gate = request.form.get("portal_code", "")
    expected_gate = os.getenv("PRINCE_STAGING_GATE_TOKEN", "")
    if (
        selector not in DEMO_EMAILS
        or not expected_gate
        or len(expected_gate) < 32
        or not hmac.compare_digest(gate, expected_gate)
        or not user_pass
    ):
        return render_template("partnerships/prince_staging_login.html",
                               error="Invalid demo access details."), 401
    user = User.query.filter_by(email=DEMO_EMAILS[selector]).first()
    if not _valid_member(user, selector) or not user.check_password(user_pass):
        return render_template("partnerships/prince_staging_login.html",
                               error="Invalid demo access details."), 401

    session.clear()
    login_user(user, remember=False, fresh=True)
    session["prince_demo_authorized"] = True
    session.permanent = False
    return redirect(url_for("prince_pilot.operator_workspace", slug="prince"), code=303)


@prince_staging_bp.post("/sign-out")
def sign_out():
    if not current_app.config.get("PRINCE_STAGING_LOCKDOWN", False):
        abort(404)
    if current_user.is_authenticated:
        logout_user()
    session.clear()
    return redirect(url_for("prince_staging.sign_in"), code=303)
