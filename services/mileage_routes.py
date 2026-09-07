"""Advisor mileage observation routes and template helpers."""

from __future__ import annotations

from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from admin.routes import CLINICAL_DISCLAIMER, admin_bp
from admin.utils import advisor_required
from extensions import db
from models import Car, CarOwnership
from services.health_alert_service import CareSignalService
from services.mileage_observations import (
    MileageObservationError,
    MileageObservationService,
    mileage_source_label,
    mileage_verification_label,
)


@admin_bp.app_context_processor
def inject_mileage_template_helpers():
    """Expose read-only mileage provenance helpers to shared vehicle templates."""

    return {
        "mileage_snapshot_for": MileageObservationService.snapshot,
        "mileage_history_for": MileageObservationService.history,
        "mileage_source_label": mileage_source_label,
        "mileage_verification_label": mileage_verification_label,
    }


@admin_bp.route(
    "/cars/<int:car_id>/odometer",
    methods=["GET", "POST"],
    endpoint="update_odometer",
)
@login_required
@advisor_required
def update_odometer(car_id: int):
    """Record a current advisor-observed main-odometer reading."""

    car = Car.query.get_or_404(car_id)
    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        is_active=True,
    ).first()

    if request.method == "POST":
        mileage = request.form.get("odometer_km", type=int)
        observed_at_raw = (request.form.get("observed_at") or "").strip()
        evidence_reference = (request.form.get("evidence_reference") or "").strip()
        note = (request.form.get("note") or "").strip()

        if mileage is None:
            flash("Enter the vehicle's current main-odometer reading.", "error")
            return redirect(request.referrer or request.url)

        try:
            observed_at = (
                datetime.fromisoformat(observed_at_raw)
                if observed_at_raw
                else datetime.utcnow()
            )
        except ValueError:
            flash("Observation date and time are invalid.", "error")
            return redirect(request.referrer or request.url)

        try:
            MileageObservationService.record_advisor_observation(
                car=car,
                odometer_km=mileage,
                advisor_user_id=current_user.id,
                ownership=ownership,
                observed_at=observed_at,
                evidence_reference=evidence_reference,
                note=note,
            )
        except MileageObservationError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(request.referrer or request.url)

        try:
            CareSignalService.evaluate(car.id, trigger="mileage_observed")
        except Exception:
            db.session.rollback()
            flash(
                "Odometer observation was saved, but monitoring signals could not be refreshed.",
                "warning",
            )
        else:
            flash("Latest recorded odometer updated.", "success")

        return redirect(url_for("admin.view_vehicle", car_id=car.id))

    return render_template(
        "admin/update_odometer.html",
        car=car,
        ownership=ownership,
        mileage_snapshot=MileageObservationService.snapshot(car),
        mileage_history=MileageObservationService.history(car.id, limit=12),
        disclaimer=CLINICAL_DISCLAIMER,
    )
