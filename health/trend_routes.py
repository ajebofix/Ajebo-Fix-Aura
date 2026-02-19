from flask import Blueprint, jsonify
from flask_login import login_required, current_user

from models import CarOwnership
from services.health_trend_service import (
    VehicleCareTrajectoryService as HealthTrendService,
)

# =====================================================
# HEALTH TRAJECTORY
# Aura — Private Automotive Health Portal
# =====================================================

trajectory_bp = Blueprint("health_trajectory", __name__)


# =====================================================
# SAFE ADVISOR CHECK
# =====================================================


def is_advisor(user):
    return hasattr(user, "is_admin") and user.is_admin()


# =====================================================
# CLIENT — VIEW HEALTH TRAJECTORY (OWN VEHICLE)
# =====================================================


@trajectory_bp.route("/cars/<int:car_id>/health/trajectory", methods=["GET"])
@login_required
def client_health_trajectory(car_id):
    """
    Returns a directional view of vehicle health over time.
    Informational context only — not diagnostic.
    """

    # Ensure active stewardship
    CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    trajectory = HealthTrendService.analyze(car_id)

    return (
        jsonify(
            {
                "vehicle_id": car_id,
                "health_trajectory": trajectory,
                "disclaimer": (
                    "Health trajectory reflects observed patterns over time "
                    "and does not constitute a diagnosis or repair instruction."
                ),
            }
        ),
        200,
    )


# =====================================================
# ADVISOR — VIEW HEALTH TRAJECTORY (ANY VEHICLE)
# =====================================================


@trajectory_bp.route("/advisor/cars/<int:car_id>/health/trajectory", methods=["GET"])
@login_required
def advisor_health_trajectory(car_id):
    """
    Advisor-level access to full health trajectory review.
    """

    if not is_advisor(current_user):
        return jsonify({"error": "Advisor access required"}), 403

    trajectory = HealthTrendService.analyze(car_id)

    return (
        jsonify(
            {
                "vehicle_id": car_id,
                "health_trajectory": trajectory,
            }
        ),
        200,
    )
