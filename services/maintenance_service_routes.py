"""Advisor routes and template helpers for maintenance service normalization."""

from __future__ import annotations

from flask import flash, redirect, request, url_for
from flask_login import current_user, login_required

from admin.routes import admin_bp
from admin.utils import advisor_required
from extensions import db
from maintenance.reevaluation import MaintenanceReevaluationService
from maintenance.service_history import (
    MaintenanceServiceClassificationError,
    ServiceHistoryNormalizationService,
)
from models import Car, VehicleEvent


@admin_bp.app_context_processor
def inject_maintenance_service_template_helpers():
    """Expose bounded read-only classification helpers to shared timelines."""

    return {
        "maintenance_classification_options_for": (
            ServiceHistoryNormalizationService.classification_options
        ),
        "maintenance_active_classification_for": (
            ServiceHistoryNormalizationService.active_for_event
        ),
    }


@admin_bp.post(
    "/cars/<int:car_id>/service-events/<int:event_id>/maintenance-classification",
    endpoint="classify_service_maintenance_item",
)
@login_required
@advisor_required
def classify_service_maintenance_item(car_id: int, event_id: int):
    """Assign, correct, or clear one advisor-verified maintenance item identity."""

    car = Car.query.get_or_404(car_id)
    event = VehicleEvent.query.filter_by(
        id=event_id,
        car_id=car.id,
        event_type="service",
        is_deleted=False,
    ).first_or_404()
    item_key = (request.form.get("maintenance_item_key") or "").strip().lower()

    try:
        if item_key:
            classification = ServiceHistoryNormalizationService.classify(
                service_event_id=event.id,
                actor_user_id=current_user.id,
                maintenance_item_key=item_key,
                classification_source="advisor_review",
            )
            message = (
                "Service record classified as "
                f"{classification.maintenance_item_key.replace('_', ' ')}."
            )
        else:
            ServiceHistoryNormalizationService.clear(
                service_event_id=event.id,
                actor_user_id=current_user.id,
            )
            message = "Maintenance classification removed from this service record."
        db.session.commit()
    except MaintenanceServiceClassificationError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            request.referrer or url_for("admin.admin_vehicle_records", car_id=car.id)
        )

    MaintenanceReevaluationService.safe_evaluate_car(
        car_id=car.id,
        trigger="service_classification_changed",
    )
    flash(message, "success")
    return redirect(
        request.referrer or url_for("admin.admin_vehicle_records", car_id=car.id)
    )
