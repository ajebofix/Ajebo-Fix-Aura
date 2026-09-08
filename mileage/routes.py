"""Owner and driver mileage-report intake routes.

Reported readings are evidence only. They do not change the authoritative
``Car.current_mileage`` until an advisor accepts the report.
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models import Car, CarDriver, CarOwnership
from services.mileage_observations import (
    MileageObservationError,
    MileageObservationService,
)


mileage_bp = Blueprint("mileage", __name__, url_prefix="/mileage")


def _reporting_context(car_id: int):
    car = Car.query.get_or_404(car_id)

    owner_ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        user_id=current_user.id,
        is_active=True,
    ).first()
    if owner_ownership is not None:
        return car, owner_ownership, "client"

    driver_assignment = CarDriver.query.filter_by(
        car_id=car.id,
        user_id=current_user.id,
        is_active=True,
    ).first()
    if driver_assignment is not None:
        ownership = CarOwnership.query.filter_by(
            car_id=car.id,
            is_active=True,
        ).first()
        return car, ownership, "driver"

    abort(404)


def _return_url(actor_type: str, car_id: int) -> str:
    if actor_type == "driver":
        return url_for("driver.driver_car_view", car_id=car_id)
    return url_for("cars.car_detail", car_id=car_id)


@mileage_bp.route("/cars/<int:car_id>/report", methods=["GET", "POST"])
@login_required
def report_odometer(car_id: int):
    """Submit a current odometer reading for advisor review."""

    car, ownership, actor_type = _reporting_context(car_id)
    pending = MileageObservationService.pending_report_for_actor(
        car.id,
        current_user.id,
    )

    if request.method == "POST":
        odometer_km = request.form.get("odometer_km", type=int)
        note = (request.form.get("note") or "").strip()

        if odometer_km is None:
            flash("Enter the vehicle's current main-odometer reading.", "error")
            return redirect(request.url)

        if len(note) > 1000:
            flash("Mileage note must be 1,000 characters or fewer.", "error")
            return redirect(request.url)

        try:
            MileageObservationService.record_reported_observation(
                car=car,
                odometer_km=odometer_km,
                actor_user_id=current_user.id,
                actor_type=actor_type,
                ownership=ownership,
                note=note,
            )
        except MileageObservationError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(request.url)

        flash(
            "Odometer reading submitted for advisor review. The latest recorded odometer will not change until the report is accepted.",
            "success",
        )
        return redirect(_return_url(actor_type, car.id))

    return render_template(
        "mileage/report_odometer.html",
        car=car,
        ownership=ownership,
        actor_type=actor_type,
        mileage_snapshot=MileageObservationService.snapshot(car),
        pending_report=pending,
        return_url=_return_url(actor_type, car.id),
    )
