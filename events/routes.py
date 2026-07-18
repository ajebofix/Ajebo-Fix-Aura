from __future__ import annotations

from datetime import datetime, timedelta
import hashlib

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from models import CarOwnership, EventAuditLog, VehicleEvent, db
from security.access import require_vehicle_access
from services.health_alert_service import HealthAlertService


# =====================================================
# TREATMENT RECORDS
# Aura — Private Automotive Health Portal
# =====================================================

treatments_bp = Blueprint("treatments", __name__)

_ALLOWED_SEVERITIES = {"low", "monitor", "attention", "high", "critical"}


def _json_body() -> dict:
    return request.get_json(silent=True) or {}


def _clean_text(value, *, maximum: int, required: bool = False) -> str | None:
    text = str(value or "").strip()

    if required and not text:
        return None

    return text[:maximum]


def _current_owner(car_id: int):
    return CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first()


# =====================================================
# RECORD TREATMENT
# =====================================================


@treatments_bp.post("/cars/<int:car_id>/records")
@login_required
def record_treatment(car_id):
    data = _json_body()

    ownership = _current_owner(car_id)
    if not ownership:
        return jsonify({"error": "Vehicle access denied"}), 403

    event_type = _clean_text(data.get("event_type"), maximum=80, required=True)
    title = _clean_text(data.get("title"), maximum=255, required=True)
    description = _clean_text(data.get("description"), maximum=5000)
    severity = str(data.get("severity") or "low").strip().lower()

    if not event_type or not title:
        return jsonify({"error": "event_type and title are required"}), 400

    if severity not in _ALLOWED_SEVERITIES:
        return jsonify({"error": "Invalid severity"}), 422

    mileage = data.get("mileage")
    if mileage is not None:
        try:
            mileage = int(mileage)
        except (TypeError, ValueError):
            return jsonify({"error": "Mileage must be a number"}), 422

        if mileage < 0 or mileage > 5_000_000:
            return jsonify({"error": "Mileage is outside the allowed range"}), 422

    raw = f"{car_id}:{title}:{event_type}:{severity}"
    fingerprint = hashlib.sha256(raw.lower().encode()).hexdigest()

    recent = VehicleEvent.query.filter(
        VehicleEvent.car_id == car_id,
        VehicleEvent.event_type == event_type,
        VehicleEvent.title == title,
        VehicleEvent.created_at >= datetime.utcnow() - timedelta(hours=24),
    ).first()

    if recent:
        return jsonify({"error": "A similar treatment record already exists"}), 409

    treatment = VehicleEvent(
        car_id=car_id,
        ownership_id=ownership.id,
        event_type=event_type,
        severity=severity,
        title=title,
        description=description,
        mileage=mileage,
        source="manual",
        data=data.get("data") if isinstance(data.get("data"), dict) else None,
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
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Duplicate treatment record blocked"}), 409
    except Exception:
        db.session.rollback()
        raise

    HealthAlertService.evaluate(car_id)

    return jsonify({"message": "Treatment record added", "record_id": treatment.id}), 201


# =====================================================
# UPDATE TREATMENT RECORD (AUDITED)
# =====================================================


@treatments_bp.patch("/records/<int:record_id>")
@login_required
def update_treatment_record(record_id):
    data = _json_body()
    treatment = VehicleEvent.query.get_or_404(record_id)

    require_vehicle_access(
        treatment.car_id,
        allow_owner=True,
        allow_driver=False,
        allow_advisor=True,
    )

    if not current_user.is_admin and treatment.created_by != current_user.id:
        return jsonify({"error": "Only the creator or an advisor may amend this record"}), 403

    previous_entry = {
        "event_type": treatment.event_type,
        "clinical_priority": treatment.severity,
        "title": treatment.title,
        "description": treatment.description,
        "mileage": treatment.mileage,
    }

    if "event_type" in data:
        value = _clean_text(data["event_type"], maximum=80, required=True)
        if not value:
            return jsonify({"error": "event_type cannot be empty"}), 422
        treatment.event_type = value

    if "severity" in data:
        severity = str(data["severity"] or "").strip().lower()
        if severity not in _ALLOWED_SEVERITIES:
            return jsonify({"error": "Invalid severity"}), 422
        treatment.severity = severity

    if "title" in data:
        value = _clean_text(data["title"], maximum=255, required=True)
        if not value:
            return jsonify({"error": "title cannot be empty"}), 422
        treatment.title = value

    if "description" in data:
        treatment.description = _clean_text(data["description"], maximum=5000)

    if "mileage" in data:
        try:
            mileage = int(data["mileage"])
        except (TypeError, ValueError):
            return jsonify({"error": "Mileage must be a number"}), 422
        if mileage < 0 or mileage > 5_000_000:
            return jsonify({"error": "Mileage is outside the allowed range"}), 422
        treatment.mileage = mileage

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

    try:
        db.session.add(history)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    HealthAlertService.evaluate(treatment.car_id)
    return jsonify({"message": "Treatment record updated"}), 200


# =====================================================
# ARCHIVE TREATMENT RECORD
# =====================================================


@treatments_bp.delete("/records/<int:record_id>")
@login_required
def archive_treatment_record(record_id):
    treatment = VehicleEvent.query.get_or_404(record_id)

    require_vehicle_access(
        treatment.car_id,
        allow_owner=True,
        allow_driver=False,
        allow_advisor=True,
    )

    if not current_user.is_admin and treatment.created_by != current_user.id:
        return jsonify({"error": "Only the creator or an advisor may archive this record"}), 403

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

    try:
        db.session.add(history)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    HealthAlertService.evaluate(treatment.car_id)
    return jsonify({"message": "Treatment record archived"}), 200


# =====================================================
# GET TREATMENT RECORDS FOR VEHICLE
# =====================================================


@treatments_bp.get("/cars/<int:car_id>/records")
@login_required
def get_treatment_records(car_id):
    require_vehicle_access(
        car_id,
        allow_owner=True,
        allow_driver=False,
        allow_advisor=True,
    )

    records = (
        VehicleEvent.query.filter_by(car_id=car_id, is_deleted=False)
        .order_by(VehicleEvent.created_at.desc())
        .all()
    )

    return (
        jsonify(
            [
                {
                    "record_id": record.id,
                    "record_type": record.event_type,
                    "clinical_priority": record.severity,
                    "title": record.title,
                    "summary": record.description,
                    "mileage": record.mileage,
                    "recorded_at": record.created_at.isoformat(),
                }
                for record in records
            ]
        ),
        200,
    )
