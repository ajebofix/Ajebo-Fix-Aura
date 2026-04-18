# driver/routes.py


from flask import Blueprint, render_template, redirect, request, url_for, flash
from flask_login import login_required, current_user

from models import (
    CarDriver,
    Car,
    CarOwnership,
    CarFault,
    db,
)

from datetime import datetime


driver_bp = Blueprint("driver", __name__, url_prefix="/driver")


# ==============================
# DRIVERS DASHBOARD
# ==============================


@driver_bp.route("/dashboard")
@login_required
def driver_dashboard():

    if not getattr(current_user, "is_driver", False):
        flash("Driver access only.", "error")
        return redirect(url_for("dashboard.aura_home"))

    assignments = CarDriver.query.filter_by(
        user_id=current_user.id, is_active=True
    ).all()

    vehicles = []

    for a in assignments:
        car = Car.query.get(a.car_id)

        ownership = CarOwnership.query.filter_by(car_id=car.id, is_active=True).first()

        vehicles.append({"car": car, "owner": ownership.user if ownership else None})

        flash("Access denied.", "error")
        return redirect(url_for("driver.driver_dashboard"))

    return render_template("driver/dashboard.html", vehicles=vehicles)


# ====================================
# DRIVERS CAR VIEW
# ====================================


@driver_bp.route("/cars/<int:car_id>")
@login_required
def driver_car_view(car_id):

    assignment = CarDriver.query.filter_by(
        car_id=car_id, user_id=current_user.id, is_active=True
    ).first_or_404()

    car = assignment.car

    try:
        require_driver_access(car_id)
    except PermissionError:
        flash("Access denied.", "error")
        return redirect(url_for("driver.driver_dashboard"))

    return render_template("driver/car_detail.html", car=car)


# ========================================
# DRIVERS REPORTS ISSUE ROUTE
# ========================================


@driver_bp.route("/cars/<int:car_id>/report", methods=["POST"])
@login_required
def driver_report_issue(car_id):

    assignment = CarDriver.query.filter_by(
        car_id=car_id, user_id=current_user.id, is_active=True
    ).first_or_404()

    description = request.form.get("description")

    if not description:
        flash("Describe the issue.", "error")
        return redirect(request.referrer)

    fault = CarFault(
        car_id=car_id,
        description=description,
        status="reported",
        reported_by=current_user.id,
        source="driver",
        reported_at=datetime.utcnow(),
    )

    db.session.add(fault)
    db.session.commit()

    flash("Issue reported successfully.", "success")
    try:
        require_driver_access(car_id)
    except PermissionError:
        flash("Access denied.", "error")
        return redirect(url_for("driver.driver_dashboard"))


# ===========================================
# MIDDLEWARE GUARD
# ============================================


def require_driver_access(car_id):

    assignment = CarDriver.query.filter_by(
        car_id=car_id, user_id=current_user.id, is_active=True
    ).first()

    if not assignment:
        raise PermissionError("Driver access not allowed for this vehicle")

    return assignment
