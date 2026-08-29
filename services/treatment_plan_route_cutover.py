"""HTTP cutover for the Wave 2.3 Treatment Plan lifecycle.

Existing admin endpoint names remain stable for templates and bookmarks, but
their mutations are delegated to TreatmentPlanLifecycleService. Owner consent
is exposed as a separate object-scoped POST route and client-safe review page.
"""

from __future__ import annotations

from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from admin.routes import admin_bp
from admin.utils import advisor_required
from cars.routes import cars_bp
from extensions import db
from models import CarOwnership, TreatmentPlan
from services.treatment_plan_lifecycle import (
    TreatmentPlanAuthorityError,
    TreatmentPlanLifecycleError,
    TreatmentPlanLifecycleService,
)


def _admin_redirect(plan: TreatmentPlan):
    return redirect(url_for("admin.view_vehicle", car_id=plan.car_id))


def _owner_redirect(plan: TreatmentPlan):
    return redirect(url_for("cars.car_detail", car_id=plan.car_id))


@login_required
@advisor_required
def start_treatment_plan_cutover(plan_id: int):
    before = TreatmentPlan.query.get_or_404(plan_id).status
    try:
        plan = TreatmentPlanLifecycleService.start(
            plan_id=plan_id,
            actor_user_id=current_user.id,
            source="admin.treatment_start",
        )
        db.session.commit()
    except TreatmentPlanLifecycleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        plan = TreatmentPlan.query.get_or_404(plan_id)
        return _admin_redirect(plan)
    except Exception:
        db.session.rollback()
        raise

    flash(
        "Treatment resumed." if before == "monitoring" else "Treatment started.",
        "success",
    )
    return _admin_redirect(plan)


@login_required
@advisor_required
def complete_treatment_plan_cutover(plan_id: int):
    try:
        plan = TreatmentPlanLifecycleService.complete(
            plan_id=plan_id,
            actor_user_id=current_user.id,
            source="admin.treatment_complete",
        )
        db.session.commit()
    except TreatmentPlanLifecycleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        plan = TreatmentPlan.query.get_or_404(plan_id)
        return _admin_redirect(plan)
    except Exception:
        db.session.rollback()
        raise

    flash(
        "Treatment pathway recorded complete. "
        "Vehicle health remains independently monitored.",
        "success",
    )
    return _admin_redirect(plan)


@login_required
@advisor_required
def defer_treatment_plan_cutover(plan_id: int):
    current = TreatmentPlan.query.get_or_404(plan_id)
    try:
        if current.status == TreatmentPlanLifecycleService.IN_PROGRESS:
            plan = TreatmentPlanLifecycleService.start_monitoring(
                plan_id=plan_id,
                actor_user_id=current_user.id,
                source="admin.treatment_monitor",
            )
            message = "Treatment moved to monitoring."
        else:
            plan = TreatmentPlanLifecycleService.defer(
                plan_id=plan_id,
                actor_user_id=current_user.id,
                source="admin.treatment_defer",
            )
            message = "Treatment deferred."
        db.session.commit()
    except TreatmentPlanLifecycleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        plan = TreatmentPlan.query.get_or_404(plan_id)
        return _admin_redirect(plan)
    except Exception:
        db.session.rollback()
        raise

    flash(message, "success")
    return _admin_redirect(plan)


@cars_bp.get("/treatment-plans")
@login_required
def owner_treatment_plans():
    if getattr(current_user, "is_admin", False) or current_user.role == "driver":
        abort(403)

    ownerships = CarOwnership.query.filter_by(
        user_id=current_user.id,
        is_active=True,
    ).all()
    car_ids = [ownership.car_id for ownership in ownerships]
    plans = []
    if car_ids:
        plans = (
            TreatmentPlan.query.filter(TreatmentPlan.car_id.in_(car_ids))
            .order_by(TreatmentPlan.created_at.desc())
            .all()
        )

    return render_template(
        "treatment_plans/owner_index.html",
        treatment_plans=plans,
    )


@cars_bp.post("/treatment-plans/<int:plan_id>/authorize")
@login_required
def authorize_treatment_plan(plan_id: int):
    try:
        plan = TreatmentPlanLifecycleService.authorize(
            plan_id=plan_id,
            actor_user_id=current_user.id,
            source="owner.treatment_authorize",
        )
        db.session.commit()
    except TreatmentPlanAuthorityError:
        db.session.rollback()
        abort(403)
    except TreatmentPlanLifecycleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        plan = TreatmentPlan.query.get_or_404(plan_id)
        return _owner_redirect(plan)
    except Exception:
        db.session.rollback()
        raise

    flash("Treatment plan authorized for advisor coordination.", "success")
    return redirect(url_for("cars.owner_treatment_plans"))


@admin_bp.record_once
def install_treatment_plan_lifecycle_cutover(state):
    replacements = {
        "admin.start_treatment_plan": start_treatment_plan_cutover,
        "admin.complete_treatment_plan": complete_treatment_plan_cutover,
        "admin.defer_treatment_plan": defer_treatment_plan_cutover,
    }

    missing = [
        endpoint for endpoint in replacements if endpoint not in state.app.view_functions
    ]
    if missing:
        raise RuntimeError(
            "Treatment Plan lifecycle cutover could not find endpoint(s): "
            + ", ".join(sorted(missing))
        )

    state.app.view_functions.update(replacements)
