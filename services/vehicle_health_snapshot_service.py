from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from datetime import datetime
from sqlalchemy import func

from models import db, User, Car, CarOwnership, VehicleEvent, EventAuditLog
from services.vehicle_intelligence import calculate_vehicle_health
from services.rina_explainability_engine import RinaExplainabilityEngine

admin_bp = Blueprint("admin", __name__)


# =====================================================
# SAFE ADMIN CHECK — EXPLICIT, QUIET
# =====================================================


def require_admin():
    if not current_user.is_authenticated:
        return False
    if not hasattr(current_user, "is_admin"):
        return False
    return current_user.is_admin()


# =====================================================
# ADMIN — SYSTEM OVERVIEW (AURA)
# =====================================================


@admin_bp.route("/admin/overview", methods=["GET"])
@login_required
def admin_system_overview():

    if not require_admin():
        return jsonify({"error": "Administrative access required"}), 403

    total_users = db.session.query(func.count(User.id)).scalar()
    total_vehicles = db.session.query(func.count(Car.id)).scalar()
    total_records = db.session.query(func.count(VehicleEvent.id)).scalar()
    archived_records = (
        db.session.query(func.count(VehicleEvent.id))
        .filter_by(is_deleted=True)
        .scalar()
    )

    active_care_assignments = (
        db.session.query(func.count(CarOwnership.id)).filter_by(is_active=True).scalar()
    )

    return (
        jsonify(
            {
                "users": total_users,
                "vehicles": total_vehicles,
                "care_assignments": active_care_assignments,
                "records": {
                    "total": total_records,
                    "active": total_records - archived_records,
                    "archived": archived_records,
                },
            }
        ),
        200,
    )


# =====================================================
# ADMIN — VIEW ALL VEHICLE RECORDS
# =====================================================


@admin_bp.route("/admin/records", methods=["GET"])
@login_required
def admin_view_all_records():

    if not require_admin():
        return jsonify({"error": "Administrative access required"}), 403

    records = VehicleEvent.query.order_by(VehicleEvent.created_at.desc()).all()

    return (
        jsonify(
            [
                {
                    "record_id": r.id,
                    "vehicle_id": r.car_id,
                    "care_assignment_id": r.ownership_id,
                    "record_type": r.event_type,
                    "severity": r.severity,
                    "title": r.title,
                    "notes": r.description,
                    "mileage": r.mileage,
                    "is_archived": r.is_deleted,
                    "created_by": r.created_by,
                    "created_at": r.created_at.isoformat(),
                    "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                }
                for r in records
            ]
        ),
        200,
    )


# =====================================================
# ADMIN — VIEW SINGLE RECORD (FULL CONTEXT)
# =====================================================


@admin_bp.route("/admin/records/<int:record_id>", methods=["GET"])
@login_required
def admin_view_record(record_id):

    if not require_admin():
        return jsonify({"error": "Administrative access required"}), 403

    record = VehicleEvent.query.get_or_404(record_id)

    return (
        jsonify(
            {
                "record_id": record.id,
                "vehicle_id": record.car_id,
                "care_assignment_id": record.ownership_id,
                "record_type": record.event_type,
                "severity": record.severity,
                "title": record.title,
                "notes": record.description,
                "context_data": record.data,
                "fingerprint": record.fingerprint,
                "is_archived": record.is_deleted,
                "created_by": record.created_by,
                "created_at": record.created_at.isoformat(),
                "resolved_at": (
                    record.resolved_at.isoformat() if record.resolved_at else None
                ),
            }
        ),
        200,
    )


# =====================================================
# ADMIN — RECORD CHANGE LOG
# =====================================================


@admin_bp.route("/admin/records/<int:record_id>/history", methods=["GET"])
@login_required
def admin_record_history(record_id):

    if not require_admin():
        return jsonify({"error": "Administrative access required"}), 403

    logs = (
        EventAuditLog.query.filter_by(event_id=record_id)
        .order_by(EventAuditLog.created_at.asc())
        .all()
    )

    if not logs:
        return jsonify({"message": "No historical changes recorded"}), 200

    return (
        jsonify(
            [
                {
                    "log_id": l.id,
                    "action": l.action,
                    "previous_state": l.old_data,
                    "new_state": l.new_data,
                    "performed_by": l.user_id,
                    "timestamp": l.created_at.isoformat(),
                }
                for l in logs
            ]
        ),
        200,
    )


# =====================================================
# ADMIN — CARE ASSIGNMENTS OVERVIEW
# =====================================================


@admin_bp.route("/admin/care-assignments", methods=["GET"])
@login_required
def admin_view_care_assignments():

    if not require_admin():
        return jsonify({"error": "Administrative access required"}), 403

    assignments = CarOwnership.query.order_by(CarOwnership.start_date.desc()).all()

    return (
        jsonify(
            [
                {
                    "assignment_id": a.id,
                    "vehicle_id": a.car_id,
                    "client_id": a.user_id,
                    "plate_number": a.plate_number,
                    "start_date": a.start_date.isoformat(),
                    "end_date": a.end_date.isoformat() if a.end_date else None,
                    "is_active": a.is_active,
                    "mileage_at_assignment": a.mileage_at_transfer,
                }
                for a in assignments
            ]
        ),
        200,
    )


# =====================================================
# ADMIN — FLEET OVERVIEW (HEALTH STATUS)
# =====================================================


@admin_bp.route("/admin/fleet/overview", methods=["GET"])
@login_required
def admin_fleet_overview():

    if not require_admin():
        return jsonify({"error": "Administrative access required"}), 403

    status_filter = request.args.get("status")
    minimum_score = request.args.get("min_score", type=int)

    vehicles = Car.query.all()
    overview = []

    for vehicle in vehicles:

        assignment = CarOwnership.query.filter_by(
            car_id=vehicle.id, is_active=True
        ).first()

        if not assignment:
            continue

        intelligence = calculate_vehicle_health(vehicle, assignment)

        score = intelligence["health_score"]
        status = intelligence["health_status"]

        if status_filter and status != status_filter:
            continue
        if minimum_score is not None and score < minimum_score:
            continue

        overview.append(
            {
                "vehicle_id": vehicle.id,
                "vehicle": f"{vehicle.brand} {vehicle.model} {vehicle.year}",
                "current_mileage": vehicle.current_mileage or 0,
                "health_status": status,
                "health_score": score,
                "observations": intelligence["risk_reasons"],
                "client_id": assignment.user_id,
            }
        )

    overview.sort(key=lambda x: x["health_score"])

    return (
        jsonify(
            {
                "generated_at": datetime.utcnow().isoformat(),
                "vehicles_under_care": len(overview),
                "fleet": overview,
            }
        ),
        200,
    )


# =====================================================
# ADMIN — CLINICAL EXPLANATION (AURA)
# =====================================================


@admin_bp.route("/admin/vehicles/<int:car_id>/explain", methods=["GET"])
@login_required
def admin_explain_vehicle(car_id):

    if not require_admin():
        return jsonify({"error": "Administrative access required"}), 403

    vehicle = Car.query.get_or_404(car_id)

    explanation = RinaExplainabilityEngine.explain_car(vehicle)

    return (
        jsonify(
            {
                "vehicle_id": car_id,
                "clinical_summary": explanation,
            }
        ),
        200,
    )
