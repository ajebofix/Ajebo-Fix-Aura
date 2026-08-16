"""Authenticated web routes for controlled vehicle evidence intake."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from evidence.image_sanitizer import EvidenceImageValidationError
from evidence.intake import (
    EvidenceIntakeAccessError,
    EvidenceIntakeConfigurationError,
    EvidenceIntakeError,
    create_image_evidence,
)


evidence_bp = Blueprint("evidence", __name__, url_prefix="/evidence")


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@evidence_bp.post("/vehicles/<int:car_id>/images")
@login_required
def upload_vehicle_image(car_id: int):
    """Accept one server-mediated, sanitized raster image for advisor review."""

    if getattr(current_user, "email_verified_at", None) is None:
        return (
            jsonify(
                {
                    "error": "email_verification_required",
                    "message": (
                        "Please verify your email address before uploading vehicle evidence."
                    ),
                }
            ),
            403,
        )

    if not current_app.config.get("EVIDENCE_IMAGE_INTAKE_ENABLED", False):
        return (
            jsonify(
                {
                    "error": "evidence_intake_unavailable",
                    "message": "Vehicle evidence intake is not enabled yet.",
                }
            ),
            503,
        )

    uploaded = request.files.get("image")
    if uploaded is None:
        return (
            jsonify(
                {
                    "error": "image_required",
                    "message": "Select one JPEG, PNG or WebP image.",
                }
            ),
            400,
        )

    purpose = request.form.get("purpose", "").strip().lower()
    requested_visibility = request.form.get("visibility", "").strip().lower() or None
    consent_confirmed = _truthy(request.form.get("consent_confirmed"))

    injected_provider = current_app.extensions.get("evidence_storage_provider")

    try:
        result = create_image_evidence(
            user_id=current_user.id,
            car_id=car_id,
            file_stream=uploaded.stream,
            declared_content_type=uploaded.content_type or "",
            purpose=purpose,
            consent_confirmed=consent_confirmed,
            requested_visibility=requested_visibility,
            storage_provider=injected_provider,
            storage_config=current_app.config,
        )
    except EvidenceImageValidationError as exc:
        return (
            jsonify(
                {
                    "error": "image_rejected",
                    "message": str(exc),
                }
            ),
            400,
        )
    except EvidenceIntakeAccessError:
        return (
            jsonify(
                {
                    "error": "evidence_access_denied",
                    "message": "This evidence action is not available for the selected vehicle.",
                }
            ),
            403,
        )
    except EvidenceIntakeConfigurationError:
        current_app.logger.warning(
            "evidence_intake_storage_unconfigured user_id=%s car_id=%s",
            current_user.id,
            car_id,
        )
        return (
            jsonify(
                {
                    "error": "evidence_storage_unavailable",
                    "message": "Private evidence storage is not available yet.",
                }
            ),
            503,
        )
    except EvidenceIntakeError:
        current_app.logger.exception(
            "evidence_image_intake_failed user_id=%s car_id=%s",
            current_user.id,
            car_id,
        )
        return (
            jsonify(
                {
                    "error": "evidence_intake_failed",
                    "message": "Aura could not accept this evidence for review.",
                }
            ),
            503,
        )

    # The client receives only workflow metadata. Storage identifiers, bucket
    # details and content checksums remain server-side operational data.
    return (
        jsonify(
            {
                "evidence": {
                    "id": result.evidence_id,
                    "car_id": result.car_id,
                    "type": result.evidence_type,
                    "purpose": result.purpose,
                    "visibility": result.visibility,
                    "review_status": result.review_status,
                    "storage_state": result.storage_state,
                    "content_type": result.content_type,
                    "byte_size": result.byte_size,
                    "width": result.width,
                    "height": result.height,
                },
                "message": (
                    "Evidence was stored privately and is pending advisor review. "
                    "It is not a diagnosis."
                ),
            }
        ),
        201,
    )