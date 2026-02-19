from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from datetime import datetime

from extensions import db
from models import Car, CarOwnership, CarFault

# -----------------------------------------------------
# Blueprint name intentionally preserved for stability
# -----------------------------------------------------
concerns_bp = Blueprint("faults", __name__)
# ^ Routes remain /faults to avoid breaking existing links


# =====================================================
# LIST REPORTED CONCERNS FOR A VEHICLE
# =====================================================
@concerns_bp.route("/cars/<int:car_id>/faults", methods=["GET"])
@login_required
def list_faults(car_id):
    """
    Displays all reported concerns for a vehicle.
    Observational only — not diagnostic.
    """

    car = Car.query.get_or_404(car_id)

    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    concerns = (
        CarFault.query.filter_by(car_id=car.id)
        .order_by(CarFault.created_at.desc())
        .all()
    )

    return render_template(
        "cars/faults_list.html",  # template rename later
        car=car,
        ownership=ownership,
        faults=concerns,  # key preserved for Jinja compatibility
        disclaimer=(
            "Reported concerns reflect observed behavior or symptoms only. "
            "They do not confirm mechanical faults or diagnoses."
        ),
    )


# =====================================================
# REPORT A CONCERN (GET + POST)
# =====================================================
@concerns_bp.route("/cars/<int:car_id>/faults/add", methods=["GET", "POST"])
@login_required
def add_fault(car_id):
    """
    Allows a client to report an observed concern.
    This does NOT represent a diagnosis or repair decision.
    """

    car = Car.query.get_or_404(car_id)

    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    if request.method == "POST":
        category = request.form.get("category", "other").strip()
        description = request.form.get("description", "").strip()
        observed_at_raw = request.form.get("observed_at")

        # ---------------------------------
        # Calm validation
        # ---------------------------------
        if not description:
            flash(
                "Please describe what you observed so our advisors can review it.",
                "error",
            )
            return redirect(url_for("faults.add_fault", car_id=car.id))

        observed_at = (
            datetime.fromisoformat(observed_at_raw) if observed_at_raw else None
        )

        # ---------------------------------
        # Auto-generated observational title
        # ---------------------------------
        title = f"{category.replace('_', ' ').title()} observation"

        # ---------------------------------
        # Create reported concern
        # ---------------------------------
        concern = CarFault(
            car_id=car.id,
            title=title,
            category=category,
            description=description,
            status="reported",  # Aura: under review
            reported_by=current_user.id,
            reported_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            resolved_at=None,
        )

        # Optional field if your model includes it
        if hasattr(concern, "observed_at"):
            concern.observed_at = observed_at

        db.session.add(concern)
        db.session.commit()

        flash(
            "Your observation has been recorded. "
            "An Ajebo Fix advisor will review it shortly. "
            "This does not indicate confirmed damage.",
            "success",
        )

        return redirect(url_for("faults.list_faults", car_id=car.id))

    return render_template(
        "cars/fault_add.html",  # renamed later
        car=car,
        ownership=ownership,
        disclaimer=(
            "Please note: reported concerns are observations only and "
            "do not confirm mechanical failure."
        ),
    )
