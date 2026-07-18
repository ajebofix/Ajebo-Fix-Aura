# ownership/routes.py

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from models import CarOwnership, EventAuditLog, User, db
from security.access import require_advisor, require_vehicle_access
from services.intelligence_hooks import trigger_vehicle_intelligence
from services.vehicle_health_snapshot import create_health_snapshot


stewardship_bp = Blueprint("stewardship", __name__)


def _json_body() -> dict:
    return request.get_json(silent=True) or {}


def _valid_mileage(value) -> int | None:
    try:
        mileage = int(value)
    except (TypeError, ValueError):
        return None

    if mileage < 0 or mileage > 5_000_000:
        return None

    return mileage


def _eligible_client(user_id) -> User | None:
    try:
        normalized_id = int(user_id)
    except (TypeError, ValueError):
        return None

    return User.query.filter_by(
        id=normalized_id,
        role="user",
        is_active=True,
    ).first()


# =====================================================
# GET CURRENT STEWARDSHIP
# =====================================================


@stewardship_bp.get("/cars/<int:car_id>/stewardship")
@login_required
def get_current_stewardship(car_id):
    require_vehicle_access(
        car_id,
        allow_owner=True,
        allow_driver=True,
        allow_advisor=True,
    )

    stewardship = CarOwnership.query.filter_by(
        car_id=car_id,
        is_active=True,
    ).first_or_404()

    payload = {
        "stewardship_id": stewardship.id,
        "vehicle_id": stewardship.car_id,
        "plate_number": stewardship.plate_number,
        "started_at": (
            stewardship.start_date.isoformat() if stewardship.start_date else None
        ),
    }

    # Driver views do not expose client identity.
    if current_user.is_admin or stewardship.user_id == current_user.id:
        payload["client_id"] = stewardship.user_id

    return jsonify(payload), 200


# =====================================================
# GET STEWARDSHIP HISTORY (CURRENT OWNER / ADVISOR)
# =====================================================


@stewardship_bp.get("/cars/<int:car_id>/stewardship/history")
@login_required
def get_stewardship_history(car_id):
    require_vehicle_access(
        car_id,
        allow_owner=True,
        allow_driver=False,
        allow_advisor=True,
    )

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
                    "stewardship_id": record.id,
                    "client_id": record.user_id,
                    "plate_number": record.plate_number,
                    "started_at": (
                        record.start_date.isoformat() if record.start_date else None
                    ),
                    "ended_at": record.end_date.isoformat() if record.end_date else None,
                    "mileage_at_transition": record.mileage_at_transfer,
                    "is_active": record.is_active,
                }
                for record in records
            ]
        ),
        200,
    )


# =====================================================
# CLIENT — CONFIRMED STEWARDSHIP TRANSFER
# =====================================================


@stewardship_bp.post("/cars/<int:car_id>/stewardship/transfer")
@login_required
def request_stewardship_transfer(car_id):
    """Transfer stewardship after explicit credential confirmation.

    A future workflow should split this into request/accept/finalize states.
    Until then, password confirmation and strict target validation prevent a
    forged or accidental reassignment.
    """

    data = _json_body()
    new_client = _eligible_client(data.get("new_client_id"))
    mileage = _valid_mileage(data.get("mileage"))
    plate_number = str(data.get("plate_number") or "").strip().upper()
    password = str(data.get("password") or "")
    confirmation = data.get("confirm_transfer") is True

    if not new_client or mileage is None:
        return jsonify({"error": "A valid client and mileage are required"}), 400

    if not confirmation:
        return jsonify({"error": "Explicit transfer confirmation is required"}), 400

    if not current_user.check_password(password):
        return jsonify({"error": "Password confirmation failed"}), 403

    current = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    if current.user_id == new_client.id:
        return jsonify({"error": "This client already holds stewardship"}), 400

    if new_client.id == current_user.id:
        return jsonify({"error": "The target must be a different client"}), 400

    try:
        current.is_active = False
        current.end_date = datetime.utcnow()
        current.mileage_at_transfer = mileage

        new_record = CarOwnership(
            car_id=car_id,
            user_id=new_client.id,
            plate_number=plate_number or current.plate_number,
            mileage_at_transfer=mileage,
            start_date=datetime.utcnow(),
            is_active=True,
        )
        db.session.add(new_record)
        db.session.flush()

        audit = EventAuditLog(
            event_id=None,
            action="stewardship_transfer",
            old_data={
                "previous_client_id": current.user_id,
                "vehicle_id": car_id,
            },
            new_data={
                "new_client_id": new_client.id,
                "vehicle_id": car_id,
                "plate_number": new_record.plate_number,
            },
            user_id=current_user.id,
        )
        db.session.add(audit)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

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
                "new_client_id": new_client.id,
            }
        ),
        200,
    )


# =====================================================
# ADVISOR — FORCE STEWARDSHIP REASSIGNMENT
# =====================================================


@stewardship_bp.post("/advisor/cars/<int:car_id>/stewardship/reassign")
@login_required
def advisor_reassign_stewardship(car_id):
    require_advisor()

    data = _json_body()
    new_client = _eligible_client(data.get("new_client_id"))
    mileage = _valid_mileage(data.get("mileage"))
    plate_number = str(data.get("plate_number") or "").strip().upper()
    reason = str(data.get("reason") or "").strip()

    if not new_client or mileage is None:
        return jsonify({"error": "A valid client and mileage are required"}), 400

    if len(reason) < 10:
        return jsonify({"error": "A reassignment reason is required"}), 400

    current = CarOwnership.query.filter_by(
        car_id=car_id,
        is_active=True,
    ).first()

    if current and current.user_id == new_client.id:
        return jsonify({"error": "This client already holds stewardship"}), 409

    try:
        if current:
            current.is_active = False
            current.end_date = datetime.utcnow()
            current.mileage_at_transfer = mileage

        new_record = CarOwnership(
            car_id=car_id,
            user_id=new_client.id,
            plate_number=plate_number or (current.plate_number if current else None),
            mileage_at_transfer=mileage,
            start_date=datetime.utcnow(),
            is_active=True,
        )
        db.session.add(new_record)
        db.session.flush()

        audit = EventAuditLog(
            event_id=None,
            action="advisor_stewardship_reassignment",
            old_data={
                "previous_client_id": current.user_id if current else None,
                "vehicle_id": car_id,
            },
            new_data={
                "new_client_id": new_client.id,
                "vehicle_id": car_id,
                "plate_number": new_record.plate_number,
                "reason": reason,
            },
            user_id=current_user.id,
        )
        db.session.add(audit)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return (
        jsonify(
            {
                "message": "Stewardship reassigned by advisor",
                "new_client_id": new_client.id,
            }
        ),
        200,
    )
