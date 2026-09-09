"""Client-safe and advisor priority-request surfaces for Aura Wave 2.4D.

Routes are attached to Aura's existing cars/admin blueprints so the workflow is
available through normal application navigation without adding another top-level
blueprint registration dependency.
"""

from __future__ import annotations

from datetime import datetime

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from admin.routes import admin_bp
from cars.routes import cars_bp
from extensions import db
from models import Car, CarOwnership
from priority.lifecycle import PriorityRequestError, PriorityRequestLifecycleService
from priority.models import PriorityRequest
from security.access import require_advisor, resolve_vehicle_authority
from services.consultation_lifecycle import ConsultationLifecycleError, ConsultationLifecycleService


def _owner_ownership(car_id: int) -> CarOwnership:
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first()
    if ownership is None:
        abort(403)
    return ownership


def _transition(request_id: int, operation: str):
    require_advisor()
    note = request.form.get("advisor_review_note")
    operations = {
        "review": lambda: PriorityRequestLifecycleService.start_review(
            request_id=request_id,
            actor_user_id=current_user.id,
            note=note,
        ),
        "accept": lambda: PriorityRequestLifecycleService.accept(
            request_id=request_id,
            actor_user_id=current_user.id,
        ),
        "defer": lambda: PriorityRequestLifecycleService.defer(
            request_id=request_id,
            actor_user_id=current_user.id,
            note=note,
        ),
        "resolve": lambda: PriorityRequestLifecycleService.resolve(
            request_id=request_id,
            actor_user_id=current_user.id,
        ),
        "cancel": lambda: PriorityRequestLifecycleService.cancel(
            request_id=request_id,
            actor_user_id=current_user.id,
        ),
    }
    try:
        row = operations[operation]()
        db.session.commit()
    except PriorityRequestError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_priority_queue"))
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Priority transition failed request_id=%s operation=%s advisor_id=%s",
            request_id,
            operation,
            current_user.id,
        )
        flash("Unable to update the priority request right now.", "error")
        return redirect(url_for("admin.admin_priority_queue"))

    flash(f"Priority request updated: {row.status.replace('_', ' ')}.", "success")
    return redirect(url_for("admin.admin_priority_queue"))


@cars_bp.get("/<int:car_id>/priority-status")
@login_required
def client_priority_status(car_id: int):
    car = Car.query.get_or_404(car_id)
    ownership = _owner_ownership(car.id)
    rows = (
        PriorityRequest.query.filter_by(
            car_id=car.id,
            ownership_id=ownership.id,
        )
        .order_by(PriorityRequest.requested_at.desc(), PriorityRequest.id.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "priority/client_status.html",
        car=car,
        ownership=ownership,
        priority_requests=rows,
    )


@cars_bp.post("/<int:car_id>/priority-requests/<int:request_id>/cancel")
@login_required
def client_cancel_priority_request(car_id: int, request_id: int):
    _owner_ownership(car_id)
    row = PriorityRequest.query.filter_by(id=request_id, car_id=car_id).first_or_404()
    if row.requested_by_user_id != current_user.id:
        abort(403)
    try:
        PriorityRequestLifecycleService.cancel(
            request_id=row.id,
            actor_user_id=current_user.id,
        )
        db.session.commit()
    except PriorityRequestError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Owner priority cancellation failed request_id=%s user_id=%s",
            row.id,
            current_user.id,
        )
        flash("Unable to cancel the priority request right now.", "error")
    else:
        flash("Priority request cancelled.", "success")
    return redirect(url_for("cars.client_priority_status", car_id=car_id))


@admin_bp.get("/priority-requests")
@login_required
def admin_priority_queue():
    require_advisor()
    active = (
        PriorityRequest.query.filter(
            PriorityRequest.status.in_(
                ("requested", "under_review", "accepted", "deferred")
            )
        )
        .order_by(PriorityRequest.requested_at.asc(), PriorityRequest.id.asc())
        .all()
    )
    recent = (
        PriorityRequest.query.filter(
            PriorityRequest.status.in_(("resolved", "cancelled"))
        )
        .order_by(PriorityRequest.updated_at.desc(), PriorityRequest.id.desc())
        .limit(25)
        .all()
    )
    return render_template(
        "priority/admin_queue.html",
        active_requests=active,
        recent_requests=recent,
    )


@admin_bp.post("/cars/<int:car_id>/priority-request")
@login_required
def admin_create_priority_request(car_id: int):
    require_advisor()
    kind = (request.form.get("request_kind") or "priority").strip()
    reason = request.form.get("reason_summary")
    try:
        PriorityRequestLifecycleService.create_request(
            car_id=car_id,
            actor_user_id=current_user.id,
            request_kind=kind,
            request_source="advisor",
            reason_summary=reason,
        )
        db.session.commit()
    except PriorityRequestError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Advisor priority creation failed car_id=%s advisor_id=%s",
            car_id,
            current_user.id,
        )
        flash("Unable to create the priority request right now.", "error")
    else:
        flash("Priority request created.", "success")
    return redirect(url_for("admin.admin_priority_queue"))


@admin_bp.post("/priority-requests/<int:request_id>/review")
@login_required
def admin_review_priority_request(request_id: int):
    return _transition(request_id, "review")


@admin_bp.post("/priority-requests/<int:request_id>/accept")
@login_required
def admin_accept_priority_request(request_id: int):
    return _transition(request_id, "accept")


@admin_bp.post("/priority-requests/<int:request_id>/defer")
@login_required
def admin_defer_priority_request(request_id: int):
    return _transition(request_id, "defer")


@admin_bp.post("/priority-requests/<int:request_id>/resolve")
@login_required
def admin_resolve_priority_request(request_id: int):
    return _transition(request_id, "resolve")


@admin_bp.post("/priority-requests/<int:request_id>/cancel")
@login_required
def admin_cancel_priority_request(request_id: int):
    return _transition(request_id, "cancel")


@admin_bp.route(
    "/priority-requests/<int:request_id>/consultation",
    methods=["GET", "POST"],
)
@login_required
def admin_link_priority_consultation(request_id: int):
    require_advisor()
    row = PriorityRequest.query.get_or_404(request_id)
    if row.status != "accepted":
        flash("Accept the priority request before scheduling its consultation.", "error")
        return redirect(url_for("admin.admin_priority_queue"))

    if row.consultation_id is not None:
        flash("This priority request is already linked to a consultation.", "info")
        return redirect(url_for("admin.admin_priority_queue"))

    if request.method == "GET":
        return render_template("priority/link_consultation.html", priority_request=row)

    raw = (request.form.get("scheduled_for") or "").strip()
    try:
        scheduled_for = datetime.fromisoformat(raw)
    except ValueError:
        flash("Please provide a valid consultation date and time.", "error")
        return redirect(
            url_for("admin.admin_link_priority_consultation", request_id=row.id)
        )

    try:
        consultation = ConsultationLifecycleService.create_scheduled(
            car_id=row.car_id,
            actor_user_id=current_user.id,
            scheduled_for=scheduled_for,
            notes=f"Linked from priority request #{row.id}.",
            source="priority.accepted",
        )
        PriorityRequestLifecycleService.link_consultation(
            request_id=row.id,
            consultation_id=consultation.id,
            actor_user_id=current_user.id,
        )
        db.session.commit()
    except (PriorityRequestError, ConsultationLifecycleError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_priority_queue"))
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Priority consultation linkage failed request_id=%s advisor_id=%s",
            row.id,
            current_user.id,
        )
        flash("Unable to schedule the linked consultation right now.", "error")
        return redirect(url_for("admin.admin_priority_queue"))

    flash("Priority request linked to a scheduled consultation.", "success")
    return redirect(url_for("admin.admin_priority_queue"))


def user_can_view_priority_request(row: PriorityRequest, user_id: int) -> bool:
    """Small helper used by tests and future Rina/client-safe projections."""
    authority = resolve_vehicle_authority(user_id, row.car_id)
    if authority in {"advisor", "administrator"}:
        return True
    return authority == "owner" and row.ownership_id == getattr(row.ownership, "id", None)
