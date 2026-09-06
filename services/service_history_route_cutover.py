"""Compatibility cutover for service-history odometer and monitoring semantics.

A service record stores the vehicle's main-odometer reading at the time the
service occurred. That historical reading may legitimately be below the
vehicle's present odometer and must never roll ``Car.current_mileage`` back.

Service-history persistence is also a monitoring-relevant vehicle event. After
a service record is durably saved, Aura re-evaluates deterministic care signals
with the canonical ``event_created`` trigger. Signal refresh failure never
rolls back or disguises an already-saved service record; the UI instead warns
that monitoring refresh needs attention.

This adapter keeps the existing advisor and owner URLs/templates/authority
contracts while delegating persistence to the corrected shared service-event
helper.
"""

from __future__ import annotations

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from admin.routes import CLINICAL_DISCLAIMER, admin_bp
from admin.utils import advisor_required
from cars.routes import cars_bp, create_service_event
from extensions import db
from models import Car, CarOwnership
from services.consultation_guard import require_active_consultation
from services.health_alert_service import CareSignalService


def _record_service_with_monitoring(
    *,
    car,
    ownership,
    service_type: str,
    mileage: int,
    description: str,
    service_date: str,
    performed_by: int,
    source: str,
) -> bool:
    """Persist a service record, then refresh deterministic care signals.

    ``create_service_event`` owns the durable service commit. The subsequent
    monitoring refresh is intentionally best-effort from the route perspective:
    if signal evaluation fails, the service remains truthfully recorded and the
    caller receives ``False`` so it can surface an operational warning.
    """

    create_service_event(
        car=car,
        ownership=ownership,
        service_type=service_type,
        mileage=mileage,
        description=description,
        service_date=service_date,
        performed_by=performed_by,
        source=source,
    )

    try:
        CareSignalService.evaluate(car.id, trigger="event_created")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Service record %s saved but care-signal refresh failed",
            car.id,
        )
        return False

    return True


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
            monitoring_refreshed = _record_service_with_monitoring(
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
        if not monitoring_refreshed:
            flash(
                "The service was saved, but monitoring signals could not be refreshed. "
                "Please retry the monitoring review.",
                "warning",
            )
        return redirect(url_for("admin.admin_vehicle_records", car_id=car.id))

    return render_template(
        "admin/add_service.html",
        car=car,
        ownership=ownership,
        disclaimer=CLINICAL_DISCLAIMER,
    )


@login_required
def client_add_service_cutover(ownership_id: int):
    ownership = CarOwnership.query.filter_by(
        id=ownership_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()
    car = ownership.car

    try:
        require_active_consultation(car.id)
    except PermissionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("cars.car_detail", car_id=car.id))

    if request.method == "POST":
        try:
            monitoring_refreshed = _record_service_with_monitoring(
                car=car,
                ownership=ownership,
                service_type=request.form["service_type"].strip(),
                mileage=int(request.form["mileage"]),
                description=request.form.get("description", "").strip(),
                service_date=request.form["service_date"].strip(),
                performed_by=current_user.id,
                source="client",
            )
        except (KeyError, TypeError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc) or "Please complete the service details.", "error")
            return redirect(request.referrer or request.url)
        except Exception:
            db.session.rollback()
            raise

        flash("Service record saved.", "success")
        if not monitoring_refreshed:
            flash(
                "The service was saved, but monitoring is still being reviewed.",
                "warning",
            )
        return redirect(url_for("cars.car_detail", car_id=car.id))

    return render_template("cars/add_service.html", car=car, ownership=ownership)


@admin_bp.record_once
def install_service_history_route_cutover(state):
    replacements = {
        "admin.admin_add_service": admin_add_service_cutover,
        "cars.add_service_record": client_add_service_cutover,
    }

    missing = [
        endpoint for endpoint in replacements if endpoint not in state.app.view_functions
    ]
    if missing:
        raise RuntimeError(
            "Service-history cutover could not find endpoint(s): " + ", ".join(missing)
        )

    for endpoint, view_func in replacements.items():
        state.app.view_functions[endpoint] = view_func
