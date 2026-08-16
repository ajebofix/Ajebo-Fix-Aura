"""Advisor-only HTTP surface for Wave 1.4 evidence review/linking."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from evidence.review import (
    EvidenceReviewAccessError,
    EvidenceReviewConflict,
    EvidenceReviewError,
    EvidenceReviewNotFound,
    link_evidence_to_reported_concern,
    review_evidence,
)


evidence_review_bp = Blueprint(
    "evidence_review",
    __name__,
    url_prefix="/admin/evidence",
)


def _request_value(name: str) -> str:
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        return str(payload.get(name) or "").strip()
    return str(request.form.get(name) or "").strip()


def _enabled():
    if current_app.config.get("EVIDENCE_ADVISOR_REVIEW_ENABLED", False):
        return None
    return jsonify({"error": "evidence_review_unavailable"}), 503


@evidence_review_bp.post("/<int:evidence_id>/review")
@login_required
def review_vehicle_evidence(evidence_id: int):
    disabled = _enabled()
    if disabled is not None:
        return disabled

    try:
        result = review_evidence(
            reviewer_user_id=current_user.id,
            evidence_id=evidence_id,
            decision=_request_value("decision"),
            reason_code=_request_value("reason_code"),
        )
    except EvidenceReviewAccessError:
        return jsonify({"error": "evidence_review_access_denied"}), 403
    except EvidenceReviewNotFound:
        return jsonify({"error": "evidence_not_found"}), 404
    except EvidenceReviewConflict as exc:
        return jsonify({"error": "evidence_review_conflict", "message": str(exc)}), 409
    except EvidenceReviewError:
        current_app.logger.exception(
            "evidence_review_failed evidence_id=%s reviewer_id=%s",
            evidence_id,
            current_user.id,
        )
        return jsonify({"error": "evidence_review_failed"}), 503

    return jsonify(
        {
            "evidence_id": result.evidence_id,
            "car_id": result.car_id,
            "review_status": result.review_status,
            "visibility": result.visibility,
            "review_reason_code": result.review_reason_code,
            "message": (
                "Evidence review was recorded. This review classifies the evidence "
                "for Aura's care record; it is not a mechanical diagnosis."
            ),
        }
    ), 200


@evidence_review_bp.post(
    "/<int:evidence_id>/links/reported-concerns/<int:concern_id>"
)
@login_required
def link_vehicle_evidence_to_concern(evidence_id: int, concern_id: int):
    disabled = _enabled()
    if disabled is not None:
        return disabled

    try:
        result = link_evidence_to_reported_concern(
            reviewer_user_id=current_user.id,
            evidence_id=evidence_id,
            concern_id=concern_id,
        )
    except EvidenceReviewAccessError:
        return jsonify({"error": "evidence_link_access_denied"}), 403
    except EvidenceReviewNotFound:
        return jsonify({"error": "evidence_or_concern_not_found"}), 404
    except EvidenceReviewConflict as exc:
        return jsonify({"error": "evidence_link_conflict", "message": str(exc)}), 409
    except EvidenceReviewError:
        current_app.logger.exception(
            "evidence_concern_link_failed evidence_id=%s concern_id=%s reviewer_id=%s",
            evidence_id,
            concern_id,
            current_user.id,
        )
        return jsonify({"error": "evidence_link_failed"}), 503

    return jsonify(
        {
            "link_id": result.link_id,
            "evidence_id": result.evidence_id,
            "car_id": result.car_id,
            "concern_id": result.concern_id,
            "relationship_type": result.relationship_type,
            "created": result.created,
            "message": (
                "Advisor-accepted evidence is now linked to this Reported Concern. "
                "The link records supporting evidence; it does not establish a diagnosis."
            ),
        }
    ), (201 if result.created else 200)
