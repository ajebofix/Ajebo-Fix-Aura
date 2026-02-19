from flask import Blueprint, jsonify
from flask_login import login_required, current_user

from models import db, VehicleHealthSnapshot, CarOwnership

# =====================================================
# HEALTH RECORDS
# Aura — Private Automotive Health Portal
# =====================================================

health_bp = Blueprint("health_records", __name__)


# =====================================================
# SAFE ADVISOR CHECK
# =====================================================


def is_advisor(user):
    return hasattr(user, "is_admin") and user.is_admin()


# =====================================================
# CLIENT — HEALTH RECORD HISTORY (OWN VEHICLE)
# =====================================================


@health_bp.route("/cars/<int:car_id>/health/records", methods=["GET"])
@login_required
def client_vehicle_health_records(car_id):
    """
    Returns historical health records for a vehicle.
    Informational context only — not diagnostic.
    """

    # Ensure active stewardship
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    records = (
        VehicleHealthSnapshot.query.filter_by(
            car_id=car_id,
            ownership_id=ownership.id,
        )
        .order_by(VehicleHealthSnapshot.created_at.asc())
        .all()
    )

    return (
        jsonify(
            {
                "vehicle_id": car_id,
                "stewardship_id": ownership.id,
                "record_count": len(records),
                "records": [
                    {
                        "health_status_index": r.health_score,
                        "health_status": r.health_status,
                        "observations": r.reasons,
                        "record_source": r.triggered_by,
                        "recorded_at": r.created_at.isoformat(),
                    }
                    for r in records
                ],
                "disclaimer": (
                    "Health records provide contextual insight and do not "
                    "constitute a mechanical diagnosis."
                ),
            }
        ),
        200,
    )


# =====================================================
# ADVISOR — HEALTH RECORD HISTORY (ANY VEHICLE)
# =====================================================


@health_bp.route("/advisor/cars/<int:car_id>/health/records", methods=["GET"])
@login_required
def advisor_vehicle_health_records(car_id):
    """
    Advisor-level access to full vehicle health record history.
    """

    if not is_advisor(current_user):
        return jsonify({"error": "Advisor access required"}), 403

    records = (
        VehicleHealthSnapshot.query.filter_by(car_id=car_id)
        .order_by(VehicleHealthSnapshot.created_at.asc())
        .all()
    )

    return (
        jsonify(
            {
                "vehicle_id": car_id,
                "record_count": len(records),
                "records": [
                    {
                        "stewardship_id": r.ownership_id,
                        "health_status_index": r.health_score,
                        "health_status": r.health_status,
                        "observations": r.reasons,
                        "record_source": r.triggered_by,
                        "recorded_at": r.created_at.isoformat(),
                    }
                    for r in records
                ],
            }
        ),
        200,
    )
