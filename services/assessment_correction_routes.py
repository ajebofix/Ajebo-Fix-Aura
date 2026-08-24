"""Advisor UI adapter for immutable finalized-assessment addenda."""

from __future__ import annotations

import uuid

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from admin.routes import admin_bp
from admin.utils import advisor_required
from extensions import db
from models import VehicleAssessment
from models_assessment_addendum import VehicleAssessmentAddendum
from services.assessment_lifecycle import (
    AssessmentLifecycleError,
    AssessmentLifecycleService,
)


@login_required
@advisor_required
def admin_assessment_addenda(assessment_id: int):
    assessment = VehicleAssessment.query.get_or_404(assessment_id)

    if not assessment.is_finalized or assessment.status != "finalized":
        flash("Assessment must be finalized before recording an addendum.", "error")
        return redirect(url_for("admin.view_vehicle", car_id=assessment.car_id))

    if request.method == "POST":
        try:
            AssessmentLifecycleService.add_correction(
                assessment_id=assessment.id,
                actor_user_id=current_user.id,
                category=request.form.get("category", ""),
                reason=request.form.get("reason", ""),
                visibility=request.form.get("visibility", ""),
                client_text=request.form.get("client_text"),
                internal_text=request.form.get("internal_text"),
                idempotency_key=request.form.get("idempotency_key", ""),
                source="admin.assessment_addendum",
            )
            db.session.commit()
        except AssessmentLifecycleError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        except Exception:
            db.session.rollback()
            raise
        else:
            flash(
                "Assessment addendum recorded. The finalized assessment remains unchanged.",
                "success",
            )
            return redirect(
                url_for(
                    "admin.admin_assessment_addenda",
                    assessment_id=assessment.id,
                )
            )

    addenda = (
        VehicleAssessmentAddendum.query.filter_by(assessment_id=assessment.id)
        .order_by(
            VehicleAssessmentAddendum.created_at.desc(),
            VehicleAssessmentAddendum.id.desc(),
        )
        .all()
    )

    return render_template(
        "admin/assessment_addenda.html",
        assessment=assessment,
        addenda=addenda,
        idempotency_key=uuid.uuid4().hex,
    )


@admin_bp.record_once
def install_assessment_correction_routes(state):
    endpoint = "admin.admin_assessment_addenda"
    if endpoint in state.app.view_functions:
        return

    state.app.add_url_rule(
        "/admin/assessments/<int:assessment_id>/addenda",
        endpoint=endpoint,
        view_func=admin_assessment_addenda,
        methods=["GET", "POST"],
    )
