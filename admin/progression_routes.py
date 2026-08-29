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
# These modules register compatibility cutovers and professional routes on
# admin_bp/cars_bp before those blueprints are registered with Flask.
# Importing treatment.models also registers Wave 2.3C tables with SQLAlchemy
# metadata before Flask-Migrate evaluates the application model graph.
import treatment.models  # noqa: E402,F401
import services.consultation_route_cutover  # noqa: E402,F401
import services.assessment_route_cutover  # noqa: E402,F401
import services.assessment_correction_routes  # noqa: E402,F401
import services.treatment_plan_route_cutover  # noqa: E402,F401
