"""Compatibility health-notice routes.

Wave 2.4E keeps the client-safe per-vehicle notice projection, but consolidates
the duplicate advisor-wide read surface into the canonical Alert Center.  The
underlying durable record remains VehicleHealthAlert / care-signal lifecycle.
"""

from flask import Blueprint, jsonify, redirect, url_for
from flask_login import current_user, login_required

from models import CarOwnership, VehicleHealthAlert
from security.access import require_advisor


notices_bp = Blueprint("clinical_notices", __name__)


@notices_bp.route("/cars/<int:car_id>/health/notices", methods=["GET"])
@login_required
def client_vehicle_notices(car_id):
    """Return active client-safe care notices for an actively owned vehicle."""

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
                    "notice_type": notice.alert_type,
                    "priority_level": notice.severity,
                    "status": notice.status,
                    "advisory_note": notice.message,
                    "issued_at": notice.created_at.isoformat(),
                    "record_kind": "care_signal",
                }
                for notice in notices
            ]
        ),
        200,
    )


@notices_bp.route("/advisor/health/notices", methods=["GET"])
@login_required
def advisor_all_notices():
    """Retire the duplicate advisor notice list in favour of Alert Center."""

    require_advisor()
    return redirect(url_for("admin.admin_alert_center"), code=302)
