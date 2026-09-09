"""Wave 2.4D compatibility cutover for legacy owner priority URLs."""

from __future__ import annotations

from flask import current_app, flash, redirect, request, url_for
from flask_login import current_user, login_required

from admin.routes import admin_bp
from cars.routes import cars_bp
from extensions import db
from priority.lifecycle import PriorityRequestError, PriorityRequestLifecycleService


def _submit_owner_request(car_id: int, request_kind: str):
    try:
        row = PriorityRequestLifecycleService.create_request(
            car_id=car_id,
            actor_user_id=current_user.id,
            request_kind=request_kind,
            request_source="owner",
            reason_summary=request.form.get("reason_summary"),
        )
        db.session.commit()
    except PriorityRequestError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("cars.car_detail", car_id=car_id))
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Owner priority request failed car_id=%s kind=%s user_id=%s",
            car_id,
            request_kind,
            current_user.id,
        )
        flash("Unable to record the priority request right now.", "error")
        return redirect(url_for("cars.car_detail", car_id=car_id))

    if row.request_kind == "emergency_review":
        flash(
            "Emergency review request recorded. An advisor will review the request; this does not itself confirm a diagnosis or appointment.",
            "success",
        )
    else:
        flash(
            "Priority request recorded. An advisor will review and coordinate the next step.",
            "success",
        )
    return redirect(url_for("priority.client_priority_status", car_id=car_id))


@login_required
def request_priority_scheduling_cutover(car_id: int):
    if request.method == "GET":
        return redirect(url_for("priority.client_priority_status", car_id=car_id))
    return _submit_owner_request(car_id, "priority")


@login_required
def request_emergency_review_cutover(car_id: int):
    return _submit_owner_request(car_id, "emergency_review")


@admin_bp.record_once
def install_priority_route_cutover(state):
    """Replace legacy consultation/flash-only priority routes after 2.2 cutover."""
    replacements = {
        "cars.request_priority_scheduling": request_priority_scheduling_cutover,
        "cars.request_emergency_review": request_emergency_review_cutover,
    }
    missing = [
        endpoint for endpoint in replacements if endpoint not in state.app.view_functions
    ]
    if missing:
        raise RuntimeError(
            "Priority route cutover could not find endpoints: " + ", ".join(missing)
        )
    state.app.view_functions.update(replacements)
