"""HTTP surfaces for the authority-filtered Wave 1.4 evidence timeline."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify
from flask_login import current_user, login_required

from services.evidence_timeline import (
    EvidenceTimelineAccessError,
    EvidenceTimelineNotFound,
    get_advisor_evidence_timeline,
    get_client_safe_evidence_timeline,
)


evidence_timeline_bp = Blueprint(
    "evidence_timeline",
    __name__,
    url_prefix="/evidence",
)
advisor_evidence_timeline_bp = Blueprint(
    "advisor_evidence_timeline",
    __name__,
    url_prefix="/admin/evidence",
)


def _feature_unavailable():
    return (
        jsonify(
            {
                "error": "evidence_timeline_unavailable",
                "message": "Reviewed evidence timeline access is not enabled yet.",
            }
        ),
        503,
    )


@evidence_timeline_bp.get("/vehicles/<int:car_id>/timeline")
@login_required
def client_evidence_timeline(car_id: int):
    if not current_app.config.get("EVIDENCE_TIMELINE_ENABLED", False):
        return _feature_unavailable()

    try:
        projection = get_client_safe_evidence_timeline(
            car_id=car_id,
            viewer_user_id=current_user.id,
        )
    except EvidenceTimelineNotFound:
        return jsonify({"error": "vehicle_not_found"}), 404
    except EvidenceTimelineAccessError:
        return jsonify({"error": "evidence_timeline_access_denied"}), 403

    return jsonify(projection.to_dict()), 200


@advisor_evidence_timeline_bp.get("/vehicles/<int:car_id>/timeline")
@login_required
def advisor_evidence_timeline(car_id: int):
    if not current_app.config.get("EVIDENCE_TIMELINE_ENABLED", False):
        return _feature_unavailable()

    try:
        projection = get_advisor_evidence_timeline(
            car_id=car_id,
            viewer_user_id=current_user.id,
        )
    except EvidenceTimelineNotFound:
        return jsonify({"error": "vehicle_not_found"}), 404
    except EvidenceTimelineAccessError:
        return jsonify({"error": "evidence_timeline_access_denied"}), 403

    return jsonify(projection.to_dict()), 200
