"""Advisor UI for reviewing provenance on existing historical service records."""

from __future__ import annotations

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from admin.routes import admin_bp
from admin.utils import advisor_required
from extensions import db
from maintenance.reevaluation import MaintenanceReevaluationService
from maintenance.service_history import ServiceHistoryNormalizationService
from models import Car, VehicleEvent
from services.health_alert_service import CareSignalService
from services.historical_service_verification import (
    HISTORICAL_SERVICE_INFORMATION_SOURCES,
    HISTORICAL_SERVICE_VERIFICATION_STATUSES,
    HistoricalServiceVerificationAuthorityError,
    HistoricalServiceVerificationError,
    HistoricalServiceVerificationService,
)


def _historical_services(car_id: int) -> list[VehicleEvent]:
    events = (
        VehicleEvent.query.filter_by(
            car_id=car_id,
            event_type="service",
            is_deleted=False,
        )
        .order_by(VehicleEvent.created_at.desc(), VehicleEvent.id.desc())
        .all()
    )
    return [event for event in events if (event.data or {}).get("record_mode") == "historical"]


@admin_bp.get(
    "/cars/<int:car_id>/historical-service-verification",
    endpoint="historical_service_verification",
)
@login_required
@advisor_required
def historical_service_verification(car_id: int):
    car = Car.query.get_or_404(car_id)
    services = _historical_services(car.id)
    classifications = {
        event.id: ServiceHistoryNormalizationService.active_for_event(event.id)
        for event in services
    }
    return render_template(
        "maintenance/historical_service_review.html",
        car=car,
        services=services,
        classifications=classifications,
        information_sources=HISTORICAL_SERVICE_INFORMATION_SOURCES,
        verification_statuses=HISTORICAL_SERVICE_VERIFICATION_STATUSES,
    )


@admin_bp.post(
    "/cars/<int:car_id>/service-events/<int:event_id>/verification-review",
    endpoint="review_historical_service_verification",
)
@login_required
@advisor_required
def review_historical_service_verification(car_id: int, event_id: int):
    car = Car.query.get_or_404(car_id)
    event = VehicleEvent.query.filter_by(
        id=event_id,
        car_id=car.id,
        event_type="service",
        is_deleted=False,
    ).first_or_404()

    information_source = (request.form.get("information_source") or "").strip()
    verification_status = (request.form.get("verification_status") or "").strip()

    try:
        _event, changed = HistoricalServiceVerificationService.review(
            service_event_id=event.id,
            actor_user_id=current_user.id,
            information_source=information_source,
            verification_status=verification_status,
        )
        db.session.commit()
    except (
        HistoricalServiceVerificationAuthorityError,
        HistoricalServiceVerificationError,
    ) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("admin.historical_service_verification", car_id=car.id))

    if not changed:
        flash("Historical service evidence already reflects this review.", "info")
        return redirect(url_for("admin.historical_service_verification", car_id=car.id))

    MaintenanceReevaluationService.safe_evaluate_car(
        car_id=car.id,
        trigger="service_verification_changed",
    )

    try:
        CareSignalService.evaluate(
            car.id,
            trigger="service_verification_changed",
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Historical service verification saved but maintenance care-signal refresh failed car_id=%s",
            car.id,
        )
        flash(
            "The evidence review was saved, but maintenance monitoring could not be refreshed.",
            "warning",
        )

    flash(
        "Historical service evidence reviewed. Maintenance Intelligence was re-evaluated.",
        "success",
    )
    return redirect(url_for("admin.historical_service_verification", car_id=car.id))
