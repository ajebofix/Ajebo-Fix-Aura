# driver/routes.py

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from models import CarDriver, CarFault, CarOwnership, DriverCheckIn, db
from security.access import require_vehicle_access


driver_bp = Blueprint("driver", __name__, url_prefix="/driver")


def _require_driver_role():
    if not current_user.is_driver:
        flash("Driver access only.", "error")
        return redirect(url_for("dashboard.aura_home"))
    return None


# ==============================
# DRIVER DASHBOARD
# ==============================


@driver_bp.get("/dashboard")
@login_required
def driver_dashboard():
    denied = _require_driver_role()
    if denied:
        return denied

    assignments = CarDriver.query.filter_by(
        user_id=current_user.id,
        is_active=True,
    ).all()

    today = datetime.utcnow().date()
    checkins_today = {
        checkin.car_id: checkin
        for checkin in DriverCheckIn.query.filter(
            DriverCheckIn.driver_id == current_user.id,
            db.func.date(DriverCheckIn.created_at) == today,
        ).all()
    }

    vehicles = []

    for assignment in assignments:
        car = assignment.car
        if not car:
            continue

        ownership = CarOwnership.query.filter_by(
            car_id=car.id,
            is_active=True,
        ).first()

        vehicles.append(
            {
                "car": car,
                "owner": ownership.user if ownership else None,
                "today_checkin": checkins_today.get(car.id),
            }
        )

    return render_template("driver/dashboard.html", vehicles=vehicles)


# ====================================
# DRIVER VEHICLE VIEW
# ====================================


@driver_bp.get("/cars/<int:car_id>")
@login_required
def driver_car_view(car_id):
    denied = _require_driver_role()
    if denied:
        return denied

    car = require_vehicle_access(
        car_id,
        allow_owner=False,
        allow_driver=True,
        allow_advisor=False,
    )

    return render_template("driver/car_detail.html", car=car)


# ========================================
# DRIVER REPORTS ISSUE
# ========================================


@driver_bp.post("/cars/<int:car_id>/report")
@login_required
def driver_report_issue(car_id):
    denied = _require_driver_role()
    if denied:
        return denied

    require_vehicle_access(
        car_id,
        allow_owner=False,
        allow_driver=True,
        allow_advisor=False,
    )

    description = request.form.get("description", "").strip()

    if not description:
        flash("Describe the issue.", "error")
        return redirect(url_for("driver.driver_car_view", car_id=car_id))

    if len(description) > 5000:
        flash("The issue description is too long.", "error")
        return redirect(url_for("driver.driver_car_view", car_id=car_id))

    fault = CarFault(
        car_id=car_id,
        description=description,
        status="reported",
        reported_by=current_user.id,
        source="driver",
        reported_at=datetime.utcnow(),
    )

    try:
        db.session.add(fault)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    flash("Issue reported successfully.", "success")
    return redirect(url_for("driver.driver_car_view", car_id=car_id))


# ==========================================
# DRIVER DAILY CHECK-IN
# ==========================================


@driver_bp.route("/cars/<int:car_id>/check-in", methods=["GET", "POST"])
@login_required
def driver_daily_checkin(car_id):
    denied = _require_driver_role()
    if denied:
        return denied

    car = require_vehicle_access(
        car_id,
        allow_owner=False,
        allow_driver=True,
        allow_advisor=False,
    )

    today = datetime.utcnow().date()
    existing = DriverCheckIn.query.filter(
        DriverCheckIn.car_id == car.id,
        DriverCheckIn.driver_id == current_user.id,
        db.func.date(DriverCheckIn.created_at) == today,
    ).first()

    if request.method == "POST" and existing:
        flash("Today's check-in has already been submitted for this vehicle.", "info")
        return redirect(url_for("driver.driver_dashboard"))

    if request.method == "POST":
        notes = request.form.get("notes", "").strip()
        if len(notes) > 2000:
            flash("Check-in notes are too long.", "error")
            return render_template("driver/checkin.html", car=car)

        checkin = DriverCheckIn(
            car_id=car.id,
            driver_id=current_user.id,
            tyre_warning=bool(request.form.get("tyre_warning")),
            fuel_low=bool(request.form.get("fuel_low")),
            dashboard_light=bool(request.form.get("dashboard_light")),
            vibration=bool(request.form.get("vibration")),
            unusual_sound=bool(request.form.get("unusual_sound")),
            notes=notes,
        )

        if current_user.driver_score is None:
            current_user.driver_score = 100

        current_user.driver_score += 2

        if checkin.dashboard_light:
            current_user.driver_score -= 3
        if checkin.vibration:
            current_user.driver_score -= 3
        if checkin.unusual_sound:
            current_user.driver_score -= 3

        current_user.driver_score = max(0, min(100, current_user.driver_score))

        try:
            db.session.add(checkin)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        flash("Daily vehicle check-in submitted.", "success")
        return redirect(url_for("driver.driver_dashboard"))

    return render_template(
        "driver/checkin.html",
        car=car,
        existing_checkin=existing,
    )
