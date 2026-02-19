from flask import Blueprint, jsonify
from flask_login import login_required, current_user

from models import db, EventAuditLog, VehicleEvent

# =====================================================
# CLINICAL RECORD HISTORY
# Aura — Private Automotive Health Portal
# =====================================================

audit_bp = Blueprint("clinical_records", __name__)


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
# CLIENT — VIEW RECORD HISTORY FOR OWN TREATMENT
# =====================================================


@audit_bp.route("/records/<int:event_id>/history", methods=["GET"])
@login_required
def view_record_history(event_id):
    """
    View clinical record history for a treatment
    initiated by the current client.

    Read-only.
    Immutable.
    """

    treatment = VehicleEvent.query.get_or_404(event_id)

    # Client may only view records they initiated
    if treatment.created_by != current_user.id and not require_advisor():
        return jsonify({"error": "This record is private"}), 403

    records = (
        EventAuditLog.query.filter_by(event_id=event_id)
        .order_by(EventAuditLog.created_at.asc())
        .all()
    )

    history = []

    for record in records:
        history.append(
            {
                "record_id": record.id,
                "change_type": record.action,
                "previous_entry": record.old_data,
                "updated_entry": record.new_data,
                "recorded_by": record.user_id,
                "recorded_at": record.created_at.isoformat(),
            }
        )

    return (
        jsonify(
            {
                "treatment_id": event_id,
                "record_count": len(history),
                "clinical_history": history,
            }
        ),
        200,
    )


# =====================================================
# ADVISOR — VIEW FULL RECORD HISTORY
# =====================================================


@audit_bp.route("/advisor/records/<int:event_id>/history", methods=["GET"])
@login_required
def advisor_view_record_history(event_id):
    """
    Advisor-only access to full clinical record history.
    """

    if not require_advisor():
        return jsonify({"error": "Advisor access required"}), 403

    records = (
        EventAuditLog.query.filter_by(event_id=event_id)
        .order_by(EventAuditLog.created_at.asc())
        .all()
    )

    if not records:
        return (
            jsonify(
                {
                    "treatment_id": event_id,
                    "message": "No clinical records available",
                }
            ),
            200,
        )

    history = []

    for record in records:
        history.append(
            {
                "record_id": record.id,
                "change_type": record.action,
                "previous_entry": record.old_data,
                "updated_entry": record.new_data,
                "recorded_by": record.user_id,
                "recorded_at": record.created_at.isoformat(),
            }
        )

    return (
        jsonify(
            {
                "treatment_id": event_id,
                "record_count": len(history),
                "clinical_history": history,
            }
        ),
        200,
    )
