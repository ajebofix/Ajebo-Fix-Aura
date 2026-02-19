from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime

from models import db, CarOwnership, EventAuditLog
from services.intelligence_hooks import trigger_vehicle_intelligence
from services.vehicle_health_snapshot import create_health_snapshot


stewardship_bp = Blueprint("stewardship", __name__)


# =====================================================
# SAFE ADVISOR CHECK
# =====================================================


def require_advisor():
    if not current_user.is_authenticated:
        return False
    if not hasattr(current_user, "is_admin"):
        return False
    return current_user.is_admin()


# =====================================================
# GET CURRENT STEWARDSHIP
# =====================================================


@stewardship_bp.route("/cars/<int:car_id>/stewardship", methods=["GET"])
@login_required
def get_current_stewardship(car_id):
    """
    Returns the active stewardship record for a vehicle.
    """

    stewardship = CarOwnership.query.filter_by(
        car_id=car_id,
        is_active=True,
    ).first_or_404()

    return (
        jsonify(
            {
                "stewardship_id": stewardship.id,
                "vehicle_id": stewardship.car_id,
                "client_id": stewardship.user_id,
                "plate_number": stewardship.plate_number,
                "started_at": stewardship.start_date.isoformat(),
            }
        ),
        200,
    )


# =====================================================
# GET STEWARDSHIP HISTORY (CLIENT / ADVISOR)
# =====================================================


@stewardship_bp.route("/cars/<int:car_id>/stewardship/history", methods=["GET"])
@login_required
def get_stewardship_history(car_id):
    """
    Full stewardship timeline for a vehicle.
    """

    records = (
        CarOwnership.query.filter_by(car_id=car_id)
        .order_by(CarOwnership.start_date.asc())
        .all()
    )

    if not records:
        return jsonify({"message": "No stewardship history available"}), 200

    return (
        jsonify(
            [
                {
                    "stewardship_id": r.id,
                    "client_id": r.user_id,
                    "plate_number": r.plate_number,
                    "started_at": r.start_date.isoformat(),
                    "ended_at": r.end_date.isoformat() if r.end_date else None,
                    "mileage_at_transition": r.mileage_at_transfer,
                    "is_active": r.is_active,
                }
                for r in records
            ]
        ),
        200,
    )


# =====================================================
# CLIENT — REQUEST STEWARDSHIP TRANSFER
# =====================================================


@stewardship_bp.route("/cars/<int:car_id>/stewardship/transfer", methods=["POST"])
@login_required
def request_stewardship_transfer(car_id):
    """
    Client-initiated stewardship transfer.
    """

    data = request.get_json()

    new_client_id = data.get("new_client_id")
    mileage = data.get("mileage")
    plate_number = data.get("plate_number")

    if not new_client_id or mileage is None:
        return jsonify({"error": "new_client_id and mileage are required"}), 400

    current = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    if current.user_id == new_client_id:
        return jsonify({"error": "This client already holds stewardship"}), 400

    # Close current stewardship
    current.is_active = False
    current.end_date = datetime.utcnow()
    current.mileage_at_transfer = mileage

    # Establish new stewardship
    new_record = CarOwnership(
        car_id=car_id,
        user_id=new_client_id,
        plate_number=plate_number,
        mileage_at_transfer=mileage,
        start_date=datetime.utcnow(),
        is_active=True,
    )

    db.session.add(new_record)

    audit = EventAuditLog(
        event_id=None,
        action="stewardship_transfer",
        old_data={"previous_client_id": current.user_id, "vehicle_id": car_id},
        new_data={
            "new_client_id": new_client_id,
            "vehicle_id": car_id,
            "plate_number": plate_number,
        },
        user_id=current_user.id,
    )

    db.session.add(audit)
    db.session.commit()

    trigger_vehicle_intelligence(
        car_id=car_id,
        ownership_id=new_record.id,
        reason="stewardship_transferred",
    )

    create_health_snapshot(
        car_id=car_id,
        ownership_id=new_record.id,
        triggered_by="stewardship_transferred",
    )

    return (
        jsonify(
            {
                "message": "Stewardship successfully transferred",
                "new_client_id": new_client_id,
            }
        ),
        200,
    )


# =====================================================
# ADVISOR — FORCE STEWARDSHIP REASSIGNMENT
# =====================================================


@stewardship_bp.route(
    "/advisor/cars/<int:car_id>/stewardship/reassign",
    methods=["POST"],
)
@login_required
def advisor_reassign_stewardship(car_id):
    """
    Advisor-level override for stewardship reassignment.
    """

    if not require_advisor():
        return jsonify({"error": "Advisor access required"}), 403

    data = request.get_json()

    new_client_id = data.get("new_client_id")
    mileage = data.get("mileage")
    plate_number = data.get("plate_number")

    if not new_client_id or mileage is None:
        return jsonify({"error": "new_client_id and mileage are required"}), 400

    current = CarOwnership.query.filter_by(
        car_id=car_id,
        is_active=True,
    ).first()

    if current:
        current.is_active = False
        current.end_date = datetime.utcnow()
        current.mileage_at_transfer = mileage

    new_record = CarOwnership(
        car_id=car_id,
        user_id=new_client_id,
        plate_number=plate_number,
        mileage_at_transfer=mileage,
        start_date=datetime.utcnow(),
        is_active=True,
    )

    db.session.add(new_record)

    audit = EventAuditLog(
        event_id=None,
        action="advisor_stewardship_reassignment",
        old_data={
            "previous_client_id": current.user_id if current else None,
            "vehicle_id": car_id,
        },
        new_data={
            "new_client_id": new_client_id,
            "vehicle_id": car_id,
            "plate_number": plate_number,
        },
        user_id=current_user.id,
    )

    db.session.add(audit)
    db.session.commit()

    return (
        jsonify(
            {
                "message": "Stewardship reassigned by advisor",
                "new_client_id": new_client_id,
            }
        ),
        200,
    )
