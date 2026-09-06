"""Compatibility cutover for historical service-record odometer semantics.

A service record stores the vehicle's main-odometer reading at the time the
service occurred. That historical reading may legitimately be below the
vehicle's present odometer and must never roll ``Car.current_mileage`` back.

The legacy advisor route rejected historical readings and then assigned the
service mileage to ``Car.current_mileage`` unconditionally. This adapter keeps
the existing URL/template/authority contract while delegating persistence to
the corrected shared service-event helper used by the owner flow.
"""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from admin.routes import CLINICAL_DISCLAIMER, admin_bp
from admin.utils import advisor_required
from cars.routes import create_service_event
from extensions import db
from models import Car, CarOwnership
from services.consultation_guard import require_active_consultation


@login_required
@advisor_required
def admin_add_service_cutover(car_id: int):
    car = Car.query.get_or_404(car_id)
    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        is_active=True,
    ).first_or_404()

    try:
        require_active_consultation(car_id)
    except PermissionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_view_vehicle", car_id=car.id))

    if request.method == "POST":
        service_type = request.form.get("service_type", "").strip()
        mileage = request.form.get("mileage", type=int)
        description = request.form.get("description", "").strip()
        service_date = request.form.get("service_date", "").strip()

        if not service_type or mileage is None or not service_date:
            flash("All required fields must be completed.", "error")
            return redirect(request.referrer or request.url)

        try:
            create_service_event(
                car=car,
                ownership=ownership,
                service_type=service_type,
                mileage=mileage,
                description=description,
                service_date=service_date,
                performed_by=current_user.id,
                source="admin",
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(request.referrer or request.url)
        except Exception:
            db.session.rollback()
            raise

        flash("Service record added.", "success")
        return redirect(url_for("admin.admin_vehicle_records", car_id=car.id))

    return render_template(
        "admin/add_service.html",
        car=car,
        ownership=ownership,
        disclaimer=CLINICAL_DISCLAIMER,
    )


@admin_bp.record_once
def install_service_history_route_cutover(state):
    endpoint = "admin.admin_add_service"
    if endpoint not in state.app.view_functions:
        raise RuntimeError(
            "Service-history cutover could not find endpoint: " + endpoint
        )

    state.app.view_functions[endpoint] = admin_add_service_cutover
