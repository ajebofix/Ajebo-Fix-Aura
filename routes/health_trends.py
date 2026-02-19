# =====================================================
# VEHICLE HEALTH TRAJECTORY ROUTES (AURA)
# =====================================================

from flask import Blueprint, jsonify
from flask_login import login_required, current_user

from models import CarOwnership
from services.health_trend_service import (
    VehicleCareTrajectoryService as HealthTrendService,
)

health_trajectory_bp = Blueprint("health_trajectory", __name__)


# =====================================================
# GET VEHICLE HEALTH TRAJECTORY (CLIENT VIEW)
# =====================================================


@health_trajectory_bp.route("/cars/<int:car_id>/health/trajectory", methods=["GET"])
@login_required
def vehicle_health_trajectory(car_id):
    """
    Returns a calm, interpreted view of how a vehicle’s
    health status is evolving over time.

    This is observational guidance — not a diagnosis.
    """

    # --------------------------------
    # Ensure client has active ownership
    # --------------------------------
    CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    trajectory = HealthTrendService.analyze_car_trend(car_id)

    return (
        jsonify(
            {
                "car_id": car_id,
                "trajectory": trajectory,
                "disclaimer": (
                    "This overview reflects monitored patterns over time. "
                    "It does not represent a diagnosis or repair instruction."
                ),
            }
        ),
        200,
    )
