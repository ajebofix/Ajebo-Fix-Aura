# alert/routes.py

from flask import Blueprint, jsonify
from flask_login import login_required, current_user
from datetime import datetime

from models import db, VehicleHealthAlert, CarOwnership


alerts_bp = Blueprint("alerts", __name__)


# =====================================================
# AURA — ADVISOR AUTHORITY CHECK
# =====================================================


def require_advisor():
    """
    Confirms the request is handled by an authorized Aura advisor.
    This represents care authority, not system privilege.
    """
    if not current_user.is_authenticated:
        return False

    # V1 compatibility: advisor role is still 'admin'
    return getattr(current_user, "role", None) == "admin"


# =====================================================
# CLIENT — VIEW ACTIVE CARE SIGNALS FOR OWN VEHICLE
# =====================================================


@alerts_bp.route("/cars/<int:car_id>/signals", methods=["GET"])
@login_required
def client_vehicle_signals(car_id):
    """
    Returns active care signals for a vehicle under the client's care.
    """

    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    signals = (
        VehicleHealthAlert.query.filter_by(
            car_id=car_id,
            ownership_id=ownership.id,
            is_active=True,
        )
        .order_by(VehicleHealthAlert.created_at.desc())
        .all()
    )

    return (
        jsonify(
            [
                {
                    "signal_id": s.id,
                    "signal_type": s.alert_type,
                    "priority": s.severity,
                    "message": s.message,
                    "issued_at": s.created_at.isoformat(),
                }
                for s in signals
            ]
        ),
        200,
    )


# =====================================================
# CLIENT — VIEW CARE SIGNAL HISTORY
# =====================================================


@alerts_bp.route("/cars/<int:car_id>/signals/history", methods=["GET"])
@login_required
def client_vehicle_signal_history(car_id):
    """
    Returns full historical record of care signals for a vehicle.
    """

    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
    ).first_or_404()

    signals = (
        VehicleHealthAlert.query.filter_by(
            car_id=car_id,
            ownership_id=ownership.id,
        )
        .order_by(VehicleHealthAlert.created_at.desc())
        .all()
    )

    return (
        jsonify(
            [
                {
                    "signal_id": s.id,
                    "signal_type": s.alert_type,
                    "priority": s.severity,
                    "message": s.message,
                    "is_active": s.is_active,
                    "issued_at": s.created_at.isoformat(),
                    "closed_at": s.resolved_at.isoformat() if s.resolved_at else None,
                }
                for s in signals
            ]
        ),
        200,
    )


# =====================================================
# ADVISOR — VIEW ACTIVE CARE SIGNALS (SYSTEM-WIDE)
# =====================================================


@alerts_bp.route("/advisor/signals", methods=["GET"])
@login_required
def advisor_active_signals():
    """
    Advisor overview of all active care signals.
    """

    if not require_advisor():
        return jsonify({"error": "Advisor authority required"}), 403

    signals = (
        VehicleHealthAlert.query.filter_by(is_active=True)
        .order_by(VehicleHealthAlert.created_at.desc())
        .all()
    )

    return (
        jsonify(
            [
                {
                    "signal_id": s.id,
                    "car_id": s.car_id,
                    "ownership_id": s.ownership_id,
                    "signal_type": s.alert_type,
                    "priority": s.severity,
                    "message": s.message,
                    "issued_at": s.created_at.isoformat(),
                }
                for s in signals
            ]
        ),
        200,
    )


# =====================================================
# ADVISOR — CLOSE CARE SIGNAL
# =====================================================


@alerts_bp.route("/advisor/signals/<int:signal_id>/close", methods=["POST"])
@login_required
def advisor_close_signal(signal_id):
    """
    Marks a care signal as concluded by an advisor.
    """

    if not require_advisor():
        return jsonify({"error": "Advisor authority required"}), 403

    signal = VehicleHealthAlert.query.get_or_404(signal_id)

    if not signal.is_active:
        return jsonify({"message": "Signal already closed"}), 200

    signal.is_active = False
    signal.resolved_at = datetime.utcnow()

    db.session.commit()

    return (
        jsonify(
            {
                "message": "Care signal closed",
                "signal_id": signal.id,
            }
        ),
        200,
    )
