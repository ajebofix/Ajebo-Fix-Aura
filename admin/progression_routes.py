"""Minimum advisor review surface for Wave 1.2 concern progression."""

from __future__ import annotations

from flask import Blueprint, jsonify
from flask_login import current_user, login_required

from models import CarFault
from security.access import require_advisor
from services.concern_progression import get_reported_concern_progression


concern_progression_bp = Blueprint(
    "concern_progression",
    __name__,
    url_prefix="/admin",
)


@concern_progression_bp.get("/concerns/<int:concern_id>/progression")
@login_required
def concern_progression(concern_id: int):
    """Return an evidence-backed advisor summary for one Reported Concern."""

    require_advisor()

    concern = CarFault.query.get_or_404(concern_id)
    summary = get_reported_concern_progression(
        car_id=concern.car_id,
        concern_id=concern.id,
        viewer_user_id=current_user.id,
    )
    return jsonify(summary.to_dict()), 200


# Import after both cars.routes and admin.routes have been loaded by app.py.
# These modules register compatibility cutovers on admin_bp before that blueprint
# is registered with the Flask application.
import services.consultation_route_cutover  # noqa: E402,F401
import services.assessment_draft_cutover  # noqa: E402,F401
