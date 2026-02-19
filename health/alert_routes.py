from flask import Blueprint, jsonify
from flask_login import login_required, current_user

from models import VehicleHealthAlert, CarOwnership

# =====================================================
# CLINICAL NOTICES
# Aura — Private Automotive Health Portal
# =====================================================

notices_bp = Blueprint("clinical_notices", __name__)


# =====================================================
# SAFE ADVISOR CHECK
# =====================================================


def is_advisor(user):
    return hasattr(user, "is_admin") and user.is_admin()


# =====================================================
# CLIENT — VIEW ACTIVE CLINICAL NOTICES
# =====================================================


@notices_bp.route("/cars/<int:car_id>/health/notices", methods=["GET"])
@login_required
def client_vehicle_notices(car_id):
    """
    Returns active clinical notices for a vehicle.
    Informational only — not diagnostic.
    """

    # Ensure client has active stewardship
    CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    notices = (
        VehicleHealthAlert.query.filter_by(
            car_id=car_id,
            is_active=True,
        )
        .order_by(VehicleHealthAlert.created_at.desc())
        .all()
    )

    return (
        jsonify(
            [
                {
                    "notice_type": n.alert_type,
                    "priority_level": n.severity,
                    "advisory_note": n.message,
                    "issued_at": n.created_at.isoformat(),
                }
                for n in notices
            ]
        ),
        200,
    )


# =====================================================
# ADVISOR — VIEW ALL ACTIVE CLINICAL NOTICES
# =====================================================


@notices_bp.route("/advisor/health/notices", methods=["GET"])
@login_required
def advisor_all_notices():
    """
    Advisor-wide view of active clinical notices.
    """

    if not is_advisor(current_user):
        return jsonify({"error": "Advisor access required"}), 403

    notices = (
        VehicleHealthAlert.query.filter_by(is_active=True)
        .order_by(VehicleHealthAlert.created_at.desc())
        .all()
    )

    return (
        jsonify(
            [
                {
                    "vehicle_id": n.car_id,
                    "ownership_id": n.ownership_id,
                    "notice_type": n.alert_type,
                    "priority_level": n.severity,
                    "advisory_note": n.message,
                    "issued_at": n.created_at.isoformat(),
                }
                for n in notices
            ]
        ),
        200,
    )
