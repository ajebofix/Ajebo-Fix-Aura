"""Owner-facing driver invitation and assignment management.

Aura's vehicle record template historically posted driver actions to advisor
endpoints. The advisor guards correctly rejected client owners with HTTP 403.
This module keeps those advisor guards intact while dispatching verified vehicle
owners through ownership-scoped implementations for the two shared actions.

A future route-map cleanup can move the HTML forms to dedicated client URLs. The
compatibility dispatch here is deliberately narrow, tested, and fail-closed.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import quote

from flask import (
    Flask,
    abort,
    before_render_template,
    current_app,
    flash,
    redirect,
    request,
    url_for,
)
from flask_login import current_user, login_required

from extensions import db
from models import AccessCode, CarDriver, CarOwnership

_DRIVER_INVITE_LIFETIME = timedelta(hours=24)


def _active_owner(car_id: int) -> CarOwnership | None:
    return CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first()


def _require_verified_owner(car_id: int) -> CarOwnership:
    ownership = _active_owner(car_id)
    if ownership is None:
        abort(404)

    if getattr(current_user, "email_verified_at", None) is None:
        flash(
            "Please verify your email address before managing vehicle drivers.",
            "info",
        )
        return redirect(url_for("email_verification.verification_required"))

    return ownership


def _new_driver_code() -> str:
    for _ in range(5):
        code = secrets.token_hex(6).upper()
        if AccessCode.query.filter_by(code=code).first() is None:
            return code

    raise RuntimeError("Aura could not allocate a unique driver invitation code.")


def _create_owner_driver_invite(car_id: int):
    ownership = _require_verified_owner(car_id)
    if not isinstance(ownership, CarOwnership):
        return ownership

    active_driver = CarDriver.query.filter_by(
        car_id=car_id,
        is_active=True,
    ).first()
    if active_driver is not None:
        flash(
            f"{active_driver.user.name or active_driver.user.email} is already assigned to this vehicle.",
            "info",
        )
        return redirect(url_for("cars.car_detail", car_id=car_id))

    # One live invitation per owner/vehicle. Older unredeemed codes are revoked
    # before issuing a fresh one so a copied historical code cannot be replayed.
    AccessCode.query.filter(
        AccessCode.role == "driver",
        AccessCode.car_id == car_id,
        AccessCode.owner_id == current_user.id,
        AccessCode.is_used.is_(False),
    ).update({AccessCode.is_used: True}, synchronize_session=False)

    code = _new_driver_code()
    invitation = AccessCode(
        code=code,
        role="driver",
        car_id=car_id,
        owner_id=current_user.id,
        expires_at=datetime.utcnow() + _DRIVER_INVITE_LIFETIME,
    )
    db.session.add(invitation)
    db.session.commit()

    signup_url = url_for(
        "auth.signup",
        access_code=code,
        _external=True,
        _scheme=current_app.config.get("PREFERRED_URL_SCHEME", "https"),
    )
    message = (
        "You have been invited to drive a vehicle managed through Aura by Ajebo Fix.\n\n"
        f"Driver access code: {code}\n"
        f"Complete enrollment: {signup_url}\n\n"
        "This invitation expires in 24 hours and can be used once."
    )
    whatsapp_link = f"https://wa.me/?text={quote(message, safe='')}"

    flash(f"Driver invitation created. Access code: {code}", "success")
    flash("Share the secure invitation with the intended driver.", "info")
    return redirect(
        url_for(
            "cars.car_detail",
            car_id=car_id,
            whatsapp_link=whatsapp_link,
        )
    )


def _remove_owner_driver(driver_id: int):
    assignment = CarDriver.query.filter_by(
        id=driver_id,
        is_active=True,
    ).first_or_404()

    ownership = _require_verified_owner(assignment.car_id)
    if not isinstance(ownership, CarOwnership):
        return ownership

    assignment.is_active = False
    db.session.commit()

    flash("Driver access has been removed from this vehicle.", "success")
    return redirect(url_for("cars.car_detail", car_id=assignment.car_id))


def init_owner_driver_management(app: Flask) -> None:
    """Install narrow owner dispatch around the existing advisor endpoints."""

    advisor_invite = app.view_functions.get("admin.invite_driver")
    advisor_remove = app.view_functions.get("admin.remove_driver")

    if advisor_invite is None or advisor_remove is None:
        app.logger.warning(
            "Owner driver management could not find the advisor endpoints"
        )
        return

    @login_required
    @wraps(advisor_invite)
    def invite_dispatch(car_id: int):
        if current_user.is_admin:
            return advisor_invite(car_id)
        return _create_owner_driver_invite(car_id)

    @login_required
    @wraps(advisor_remove)
    def remove_dispatch(driver_id: int):
        if current_user.is_admin:
            return advisor_remove(driver_id)
        return _remove_owner_driver(driver_id)

    app.view_functions["admin.invite_driver"] = invite_dispatch
    app.view_functions["admin.remove_driver"] = remove_dispatch

    @before_render_template.connect_via(app, weak=False)
    def provide_owner_driver_context(sender, template, context, **extra):
        if template is None or template.name != "car_detail.html":
            return

        car = context.get("car")
        if car is None:
            return

        if context.get("active_driver") is None:
            context["active_driver"] = CarDriver.query.filter_by(
                car_id=car.id,
                is_active=True,
            ).first()

        if not context.get("whatsapp_link"):
            context["whatsapp_link"] = request.args.get("whatsapp_link")
