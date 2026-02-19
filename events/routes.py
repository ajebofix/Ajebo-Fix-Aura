from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timedelta
import hashlib

from sqlalchemy.exc import IntegrityError

from services.health_alert_service import HealthAlertService

from models import db, VehicleEvent, EventAuditLog, CarOwnership

# =====================================================
# TREATMENT RECORDS
# Aura — Private Automotive Health Portal
# =====================================================

treatments_bp = Blueprint("treatments", __name__)


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
# RECORD TREATMENT (MANUAL ENTRY)
# =====================================================


@treatments_bp.route("/cars/<int:car_id>/records", methods=["POST"])
@login_required
def record_treatment(car_id):
    """
    Create a treatment record for a vehicle.
    This does not imply diagnosis.
    """

    data = request.get_json()

    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    # ----------------------------------
    # Deterministic fingerprint
    # ----------------------------------
    raw = (
        f"{car_id}:{data.get('title')}:{data.get('event_type')}:{data.get('severity')}"
    )
    fingerprint = hashlib.sha256(raw.lower().encode()).hexdigest()

    # ----------------------------------
    # 24-hour duplicate protection
    # ----------------------------------
    recent = VehicleEvent.query.filter(
        VehicleEvent.car_id == car_id,
        VehicleEvent.event_type == data.get("event_type"),
        VehicleEvent.title == data.get("title"),
        VehicleEvent.created_at >= datetime.utcnow() - timedelta(hours=24),
    ).first()

    if recent:
        return jsonify({"error": "A similar treatment record already exists"}), 409

    treatment = VehicleEvent(
        car_id=car_id,
        ownership_id=ownership.id,
        event_type=data.get("event_type"),
        severity=data.get("severity", "low"),
        title=data.get("title"),
        description=data.get("description"),
        mileage=data.get("mileage"),
        source="manual",
        data=data.get("data"),
        fingerprint=fingerprint,
        created_by=current_user.id,
    )

    try:
        db.session.add(treatment)
        db.session.flush()

        history = EventAuditLog(
            event_id=treatment.id,
            action="create",
            old_data=None,
            new_data={
                "event_type": treatment.event_type,
                "clinical_priority": treatment.severity,
                "title": treatment.title,
                "description": treatment.description,
                "mileage": treatment.mileage,
            },
            user_id=current_user.id,
        )

        db.session.add(history)
        db.session.commit()

        # ----------------------------------
        # Post-record intelligence hooks
        # ----------------------------------
        HealthAlertService.evaluate(car_id)

    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Duplicate treatment record blocked"}), 409

    return (
        jsonify(
            {
                "message": "Treatment record added",
                "record_id": treatment.id,
            }
        ),
        201,
    )


# =====================================================
# UPDATE TREATMENT RECORD (AUDITED)
# =====================================================


@treatments_bp.route("/records/<int:record_id>", methods=["PATCH"])
@login_required
def update_treatment_record(record_id):
    """
    Update an existing treatment record.
    Changes are tracked immutably.
    """

    data = request.get_json()
    treatment = VehicleEvent.query.get_or_404(record_id)

    # Permission: creator or advisor
    if treatment.created_by != current_user.id and not require_advisor():
        return jsonify({"error": "Access denied"}), 403

    previous_entry = {
        "event_type": treatment.event_type,
        "clinical_priority": treatment.severity,
        "title": treatment.title,
        "description": treatment.description,
        "mileage": treatment.mileage,
    }

    if "event_type" in data:
        treatment.event_type = data["event_type"]
    if "severity" in data:
        treatment.severity = data["severity"]
    if "title" in data:
        treatment.title = data["title"]
    if "description" in data:
        treatment.description = data["description"]
    if "mileage" in data:
        treatment.mileage = data["mileage"]

    history = EventAuditLog(
        event_id=treatment.id,
        action="edit",
        old_data=previous_entry,
        new_data={
            "event_type": treatment.event_type,
            "clinical_priority": treatment.severity,
            "title": treatment.title,
            "description": treatment.description,
            "mileage": treatment.mileage,
        },
        user_id=current_user.id,
    )

    db.session.add(history)
    db.session.commit()

    HealthAlertService.evaluate(treatment.car_id)

    return jsonify({"message": "Treatment record updated"}), 200


# =====================================================
# ARCHIVE TREATMENT RECORD (SOFT DELETE)
# =====================================================


@treatments_bp.route("/records/<int:record_id>", methods=["DELETE"])
@login_required
def archive_treatment_record(record_id):
    """
    Archive a treatment record.
    Record remains in history.
    """

    treatment = VehicleEvent.query.get_or_404(record_id)

    if treatment.created_by != current_user.id and not require_advisor():
        return jsonify({"error": "Access denied"}), 403

    if treatment.is_deleted:
        return jsonify({"message": "Record already archived"}), 200

    treatment.is_deleted = True

    history = EventAuditLog(
        event_id=treatment.id,
        action="archive",
        old_data={"is_deleted": False},
        new_data={"is_deleted": True},
        user_id=current_user.id,
    )

    db.session.add(history)
    db.session.commit()

    HealthAlertService.evaluate(treatment.car_id)

    return jsonify({"message": "Treatment record archived"}), 200


# =====================================================
# GET TREATMENT RECORDS FOR VEHICLE
# =====================================================


@treatments_bp.route("/cars/<int:car_id>/records", methods=["GET"])
@login_required
def get_treatment_records(car_id):
    """
    View non-archived treatment records
    for an actively owned vehicle.
    """

    CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    records = (
        VehicleEvent.query.filter_by(car_id=car_id, is_deleted=False)
        .order_by(VehicleEvent.created_at.desc())
        .all()
    )

    return (
        jsonify(
            [
                {
                    "record_id": r.id,
                    "record_type": r.event_type,
                    "clinical_priority": r.severity,
                    "title": r.title,
                    "summary": r.description,
                    "mileage": r.mileage,
                    "recorded_at": r.created_at.isoformat(),
                }
                for r in records
            ]
        ),
        200,
    )
