"""Read-only HTML entry points for the Wave 1.4 evidence interaction flow.

All evidence mutations remain on the existing JSON endpoints in evidence.routes
and evidence.review_routes. These routes only prepare safe HTML surfaces around
those already-tested boundaries.
"""

from __future__ import annotations

from flask import Blueprint, abort, current_app, redirect, render_template, url_for
from flask_login import current_user, login_required

from services.evidence_interaction import (
    EvidenceInteractionAccessError,
    EvidenceInteractionNotFound,
    get_advisor_evidence_review_workspace,
    get_advisor_pending_evidence_queue,
    get_evidence_submission_context,
)


evidence_interaction_bp = Blueprint("evidence_interaction", __name__)


def _verification_redirect():
    if getattr(current_user, "email_verified_at", None) is not None:
        return None
    return redirect(url_for("email_verification.verification_required"))


def _unavailable(*, heading: str, message: str):
    return (
        render_template(
            "evidence/interaction_unavailable.html",
            heading=heading,
            message=message,
            evidence_interaction_styles=True,
        ),
        503,
    )


@evidence_interaction_bp.get("/evidence/vehicles/<int:car_id>/submit")
@login_required
def submit_vehicle_evidence(car_id: int):
    """Show the owner/driver image-submission surface."""

    if not current_app.config.get("EVIDENCE_IMAGE_INTAKE_ENABLED", False):
        return _unavailable(
            heading="Evidence submission is not enabled yet",
            message=(
                "Aura's private image intake remains unavailable until the "
                "approved storage and retention controls are enabled."
            ),
        )

    verification_redirect = _verification_redirect()
    if verification_redirect is not None:
        return verification_redirect

    try:
        context = get_evidence_submission_context(
            car_id=car_id,
            viewer_user_id=current_user.id,
        )
    except EvidenceInteractionNotFound:
        abort(404)
    except EvidenceInteractionAccessError:
        abort(403)

    if context.viewer_authority == "driver":
        return_url = url_for("driver.driver_car_view", car_id=car_id)
    else:
        return_url = url_for("cars.car_detail", car_id=car_id)

    return render_template(
        "evidence/submit_image.html",
        evidence_submission=context.to_dict(),
        upload_endpoint=url_for("evidence.upload_vehicle_image", car_id=car_id),
        return_url=return_url,
        evidence_interaction_styles=True,
    )


@evidence_interaction_bp.get("/admin/evidence/vehicles/<int:car_id>/pending")
@login_required
def advisor_pending_vehicle_evidence(car_id: int):
    """Show an advisor-authorized pending queue for one vehicle."""

    if not current_app.config.get("EVIDENCE_ADVISOR_REVIEW_ENABLED", False):
        return _unavailable(
            heading="Evidence review is not enabled yet",
            message=(
                "Advisor evidence review remains unavailable until the review "
                "control is deliberately enabled."
            ),
        )

    verification_redirect = _verification_redirect()
    if verification_redirect is not None:
        return verification_redirect

    try:
        queue = get_advisor_pending_evidence_queue(
            car_id=car_id,
            viewer_user_id=current_user.id,
        )
    except EvidenceInteractionNotFound:
        abort(404)
    except EvidenceInteractionAccessError:
        abort(403)

    return render_template(
        "evidence/advisor_pending.html",
        pending_evidence=queue.to_dict(),
        vehicle_url=url_for("admin.view_vehicle", car_id=car_id),
        evidence_interaction_styles=True,
    )


@evidence_interaction_bp.get("/admin/evidence/<int:evidence_id>/workspace")
@login_required
def advisor_evidence_workspace(evidence_id: int):
    """Show one pending item without exposing private storage metadata."""

    if not current_app.config.get("EVIDENCE_ADVISOR_REVIEW_ENABLED", False):
        return _unavailable(
            heading="Evidence review is not enabled yet",
            message=(
                "Advisor evidence review remains unavailable until the review "
                "control is deliberately enabled."
            ),
        )

    verification_redirect = _verification_redirect()
    if verification_redirect is not None:
        return verification_redirect

    try:
        workspace = get_advisor_evidence_review_workspace(
            evidence_id=evidence_id,
            viewer_user_id=current_user.id,
        )
    except EvidenceInteractionNotFound:
        abort(404)
    except EvidenceInteractionAccessError:
        abort(403)

    payload = workspace.to_dict()
    marker_concern_id = 999999999

    return render_template(
        "evidence/advisor_workspace.html",
        review_workspace=payload,
        retrieval_enabled=bool(
            current_app.config.get("EVIDENCE_RETRIEVAL_ENABLED", False)
        ),
        grant_endpoint=url_for(
            "evidence.create_private_retrieval_grant",
            evidence_id=evidence_id,
        ),
        review_endpoint=url_for(
            "evidence_review.review_vehicle_evidence",
            evidence_id=evidence_id,
        ),
        link_endpoint_template=url_for(
            "evidence_review.link_vehicle_evidence_to_concern",
            evidence_id=evidence_id,
            concern_id=marker_concern_id,
        ),
        link_endpoint_marker=str(marker_concern_id),
        queue_url=url_for(
            "evidence_interaction.advisor_pending_vehicle_evidence",
            car_id=workspace.car_id,
        ),
        vehicle_url=url_for("admin.view_vehicle", car_id=workspace.car_id),
        evidence_interaction_styles=True,
    )
