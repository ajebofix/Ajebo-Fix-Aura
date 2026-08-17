"""HTTP and vehicle-record surfaces for the safe Wave 1.4 evidence timeline."""

from __future__ import annotations

from flask import Blueprint, before_render_template, current_app, jsonify
from flask_login import current_user, login_required

from services.evidence_interaction import (
    EvidenceInteractionAccessError,
    EvidenceInteractionNotFound,
    get_evidence_submission_context,
)
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


def _owner_submission_only_surface(*, car_id: int) -> dict[str, object] | None:
    """Return a safe owner submission entry when intake precedes timeline cutover."""

    if not current_app.config.get("EVIDENCE_IMAGE_INTAKE_ENABLED", False):
        return None

    try:
        submission = get_evidence_submission_context(
            car_id=car_id,
            viewer_user_id=current_user.id,
        )
    except (EvidenceInteractionAccessError, EvidenceInteractionNotFound):
        return None

    if submission.viewer_authority != "owner":
        return None

    return {
        "mode": "submission_only",
        "viewer_authority": "owner",
        "vehicle_label": submission.vehicle_label,
        "record_count": 0,
        "records": [],
        "safety_note": (
            "Submitted material remains private and pending professional review. "
            "Submission does not confirm a fault or diagnosis."
        ),
    }


@before_render_template.connect
def _provide_vehicle_evidence_record(sender, template, context, **extra):
    """Inject the safe evidence projection only into the shared vehicle record page."""

    if template is None or template.name != "car_detail.html":
        return

    context["evidence_record_surface"] = None

    if not current_user.is_authenticated:
        return
    if getattr(current_user, "email_verified_at", None) is None:
        return

    car = context.get("car")
    if car is None or getattr(car, "id", None) is None:
        return

    if not current_app.config.get("EVIDENCE_TIMELINE_ENABLED", False):
        if not context.get("is_admin_view"):
            context["evidence_record_surface"] = _owner_submission_only_surface(
                car_id=car.id,
            )
        return

    try:
        if context.get("is_admin_view"):
            projection = get_advisor_evidence_timeline(
                car_id=car.id,
                viewer_user_id=current_user.id,
            )
        else:
            projection = get_client_safe_evidence_timeline(
                car_id=car.id,
                viewer_user_id=current_user.id,
            )
    except (EvidenceTimelineAccessError, EvidenceTimelineNotFound):
        current_app.logger.warning(
            "evidence_record_surface_denied user_id=%s car_id=%s admin_view=%s",
            current_user.id,
            car.id,
            bool(context.get("is_admin_view")),
        )
        return

    payload = projection.to_dict()
    payload["mode"] = "reviewed_record"
    context["evidence_record_surface"] = payload


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
