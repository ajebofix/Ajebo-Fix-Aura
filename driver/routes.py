# driver/routes.py


from flask import Blueprint, render_template, redirect, request, url_for, flash
from flask_login import login_required, current_user

from models import CarDriver, Car, CarOwnership, CarFault, db, DriverCheckIn

from datetime import datetime


driver_bp = Blueprint("driver", __name__, url_prefix="/driver")


# ==============================
# DRIVERS DASHBOARD
# ==============================


@driver_bp.route("/dashboard")
@login_required
def driver_dashboard():

    if not current_user.is_driver:
        flash("Driver access only.", "error")
        return redirect(url_for("dashboard.aura_home"))

    assignments = CarDriver.query.filter_by(
        user_id=current_user.id, is_active=True
    ).all()

    today = datetime.utcnow().date()

    today_checkin = DriverCheckIn.query.filter(
        DriverCheckIn.driver_id == current_user.id,
        db.func.date(DriverCheckIn.created_at) == today,
    ).first()

    vehicles = []

    for a in assignments:
        car = Car.query.get(a.car_id)

        ownership = CarOwnership.query.filter_by(car_id=car.id, is_active=True).first()

        vehicles.append({"car": car, "owner": ownership.user if ownership else None})

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

    return redirect(url_for("driver.driver_car_view", car_id=car_id))

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


# ==========================================
# DRIVER DAILY CHECKIN ROUTE
# ==========================================
@driver_bp.route("/cars/<int:car_id>/check-in", methods=["GET", "POST"])
@login_required
def driver_daily_checkin(car_id):

    assignment = CarDriver.query.filter_by(
        car_id=car_id, user_id=current_user.id, is_active=True
    ).first_or_404()

    car = assignment.car

    if request.method == "POST":

        checkin = DriverCheckIn(
            car_id=car.id,
            driver_id=current_user.id,
            tyre_warning=bool(request.form.get("tyre_warning")),
            fuel_low=bool(request.form.get("fuel_low")),
            dashboard_light=bool(request.form.get("dashboard_light")),
            vibration=bool(request.form.get("vibration")),
            unusual_sound=bool(request.form.get("unusual_sound")),
            notes=request.form.get("notes", "").strip(),
        )

        # generate alerts
        alerts = []

        if checkin.dashboard_light:
            alerts.append("Dashboard warning reported")

        if checkin.vibration:
            alerts.append("Vibration reported")

        if checkin.unusual_sound:
            alerts.append("Unusual sound reported")

        # update driver score
        if current_user.driver_score is None:
            current_user.driver_score = 100

        current_user.driver_score += 2

        if checkin.dashboard_light:
            current_user.driver_score -= 3

        if checkin.vibration:
            current_user.driver_score -= 3

        if checkin.unusual_sound:
            current_user.driver_score -= 3

        db.session.add(checkin)
        db.session.commit()

        # send alerts
        if alerts:

            ownership = CarOwnership.query.filter_by(
                car_id=car.id, is_active=True
            ).first()

            if ownership and ownership.user:

                owner_name = ownership.user.name

                flash(
                    f"Driver {owner_name} has been notified of reported concerns.",
                    "info",
                )

        flash("Daily vehicle check-in submitted.", "success")
        return redirect(url_for("driver.driver_dashboard"))

    return render_template("driver/checkin.html", car=car)
