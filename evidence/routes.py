"""Authenticated web routes for controlled vehicle evidence workflows."""

from __future__ import annotations

from io import BytesIO

from flask import Blueprint, current_app, jsonify, request, send_file, url_for
from flask_login import current_user, login_required

from evidence.image_sanitizer import EvidenceImageValidationError
from evidence.intake import (
    EvidenceIntakeAccessError,
    EvidenceIntakeConfigurationError,
    EvidenceIntakeError,
    create_image_evidence,
)
from evidence.retrieval import (
    EvidenceDeletionConflict,
    EvidenceNotAvailableError,
    EvidenceRetrievalAccessError,
    EvidenceRetrievalConfigurationError,
    EvidenceRetrievalError,
    create_retrieval_grant,
    delete_evidence,
    retrieve_private_content,
)


evidence_bp = Blueprint("evidence", __name__, url_prefix="/evidence")


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _verified_identity_error():
    if getattr(current_user, "email_verified_at", None) is not None:
        return None
    return (
        jsonify(
            {
                "error": "email_verification_required",
                "message": (
                    "Please verify your email address before using vehicle evidence."
                ),
            }
        ),
        403,
    )


def _request_value(name: str) -> str:
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        return str(payload.get(name) or "").strip()
    return str(request.form.get(name) or "").strip()


@evidence_bp.post("/vehicles/<int:car_id>/images")
@login_required
def upload_vehicle_image(car_id: int):
    """Accept one server-mediated, sanitized raster image for advisor review."""

    identity_error = _verified_identity_error()
    if identity_error is not None:
        return identity_error

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
            retention_days=current_app.config.get("EVIDENCE_RETENTION_DAYS"),
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
            "evidence_intake_configuration_unavailable user_id=%s car_id=%s",
            current_user.id,
            car_id,
        )
        return (
            jsonify(
                {
                    "error": "evidence_configuration_unavailable",
                    "message": (
                        "Vehicle evidence intake policy or private storage is not ready yet."
                    ),
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


@evidence_bp.post("/<int:evidence_id>/grant")
@login_required
def create_private_retrieval_grant(evidence_id: int):
    """Create a short-lived signed grant without exposing the private object key."""

    identity_error = _verified_identity_error()
    if identity_error is not None:
        return identity_error
    if not current_app.config.get("EVIDENCE_RETRIEVAL_ENABLED", False):
        return jsonify({"error": "evidence_retrieval_unavailable"}), 503

    try:
        grant = create_retrieval_grant(
            user_id=current_user.id,
            evidence_id=evidence_id,
            secret_key=current_app.config.get("SECRET_KEY"),
            grant_seconds=current_app.config.get("EVIDENCE_RETRIEVAL_GRANT_SECONDS"),
        )
    except EvidenceRetrievalAccessError:
        return jsonify({"error": "evidence_access_denied"}), 403
    except EvidenceNotAvailableError:
        return jsonify({"error": "evidence_not_available"}), 404
    except EvidenceRetrievalConfigurationError:
        return jsonify({"error": "evidence_retrieval_configuration_unavailable"}), 503

    return jsonify(
        {
            "evidence_id": grant.evidence_id,
            "grant_token": grant.token,
            "expires_in_seconds": grant.expires_in_seconds,
            "content_endpoint": url_for(
                "evidence.retrieve_evidence_content",
                evidence_id=evidence_id,
            ),
        }
    ), 201


@evidence_bp.post("/<int:evidence_id>/content")
@login_required
def retrieve_evidence_content(evidence_id: int):
    """Return verified private bytes through Aura; no public/presigned URL is exposed."""

    identity_error = _verified_identity_error()
    if identity_error is not None:
        return identity_error
    if not current_app.config.get("EVIDENCE_RETRIEVAL_ENABLED", False):
        return jsonify({"error": "evidence_retrieval_unavailable"}), 503

    token = _request_value("grant_token")
    if not token:
        return jsonify({"error": "evidence_grant_required"}), 400

    injected_provider = current_app.extensions.get("evidence_storage_provider")
    try:
        content = retrieve_private_content(
            user_id=current_user.id,
            evidence_id=evidence_id,
            token=token,
            secret_key=current_app.config.get("SECRET_KEY"),
            grant_seconds=current_app.config.get("EVIDENCE_RETRIEVAL_GRANT_SECONDS"),
            storage_provider=injected_provider,
            storage_config=current_app.config,
        )
    except EvidenceRetrievalAccessError:
        return jsonify({"error": "evidence_access_denied"}), 403
    except EvidenceNotAvailableError:
        return jsonify({"error": "evidence_not_available"}), 404
    except EvidenceRetrievalConfigurationError:
        return jsonify({"error": "evidence_retrieval_configuration_unavailable"}), 503
    except EvidenceRetrievalError:
        current_app.logger.exception(
            "evidence_private_retrieval_failed evidence_id=%s user_id=%s",
            evidence_id,
            current_user.id,
        )
        return jsonify({"error": "evidence_retrieval_failed"}), 503

    response = send_file(
        BytesIO(content.payload),
        mimetype=content.content_type,
        as_attachment=True,
        download_name=content.download_name,
        max_age=0,
        conditional=False,
        etag=False,
    )
    response.headers["X-Aura-Evidence-Review-Status"] = content.review_status
    response.headers["X-Aura-Evidence-Unreviewed"] = (
        "true" if content.unreviewed else "false"
    )
    return response


@evidence_bp.post("/<int:evidence_id>/delete")
@login_required
def delete_vehicle_evidence(evidence_id: int):
    """Advisor-governed logical/object deletion with retryable storage intent."""

    identity_error = _verified_identity_error()
    if identity_error is not None:
        return identity_error
    if not current_app.config.get("EVIDENCE_ADVISOR_DELETION_ENABLED", False):
        return jsonify({"error": "evidence_deletion_unavailable"}), 503

    reason_code = _request_value("reason_code")
    injected_provider = current_app.extensions.get("evidence_storage_provider")

    try:
        result = delete_evidence(
            user_id=current_user.id,
            evidence_id=evidence_id,
            reason_code=reason_code,
            storage_provider=injected_provider,
            storage_config=current_app.config,
        )
    except EvidenceRetrievalAccessError:
        return jsonify({"error": "evidence_access_denied"}), 403
    except EvidenceDeletionConflict as exc:
        return jsonify({"error": "evidence_deletion_conflict", "message": str(exc)}), 409
    except EvidenceRetrievalConfigurationError:
        return jsonify({"error": "evidence_deletion_configuration_unavailable"}), 503
    except EvidenceRetrievalError:
        current_app.logger.exception(
            "evidence_deletion_failed evidence_id=%s user_id=%s",
            evidence_id,
            current_user.id,
        )
        return jsonify({"error": "evidence_deletion_failed"}), 503

    status_code = 202 if result.storage_delete_pending else 200
    return (
        jsonify(
            {
                "evidence_id": result.evidence_id,
                "review_status": result.review_status,
                "storage_state": result.storage_state,
                "storage_delete_pending": result.storage_delete_pending,
            }
        ),
        status_code,
    )
