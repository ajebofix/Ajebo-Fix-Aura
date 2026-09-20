"""Advisor routes for historical PDF ingestion and review."""

from __future__ import annotations

from copy import deepcopy

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from admin.utils import advisor_required
from evidence.models import VehicleEvidence
from extensions import db
from historical_ingestion.application import (
    HistoricalApplicationError,
    apply_reviewed_historical_treatment,
)
from historical_ingestion.service import (
    HistoricalDocumentValidationError,
    HistoricalIngestionConfigurationError,
    HistoricalIngestionError,
    advance_historical_background_analysis,
    decrypt_extraction_payload,
    has_completed_structured_extraction,
    ingest_pdf_document_background,
    latest_background_extraction,
    latest_structured_extraction,
    reanalyze_stored_document_background,
    save_advisor_review,
)
from historical_ingestion.whatsapp_bundle import (
    WhatsAppBundleValidationError,
    advance_whatsapp_bundle_analysis,
    ingest_whatsapp_bundle,
    latest_whatsapp_bundle_extraction,
    restart_whatsapp_bundle_analysis,
)
from models import Car


historical_ingestion_bp = Blueprint("historical_ingestion", __name__)


def _reviewed_candidate(source: dict, candidate_id: str) -> dict:
    row = deepcopy(source)
    row["candidate_id"] = candidate_id
    row["review_decision"] = (
        "accepted"
        if request.form.get(f"accept_{candidate_id}") == "1"
        else "rejected"
    )
    row["title"] = request.form.get(
        f"title_{candidate_id}",
        row.get("title", ""),
    ).strip()[:255]
    row["detail"] = request.form.get(
        f"detail_{candidate_id}",
        row.get("detail", ""),
    ).strip()[:3000]
    row["state"] = request.form.get(
        f"state_{candidate_id}",
        row.get("state", "unknown"),
    ).strip()
    row["suggested_destination"] = request.form.get(
        f"destination_{candidate_id}",
        row.get("suggested_destination", "context_only"),
    ).strip()
    row["occurred_at"] = (
        request.form.get(f"occurred_at_{candidate_id}", "").strip() or None
    )
    row["outcome_direction"] = request.form.get(
        f"outcome_{candidate_id}",
        row.get("outcome_direction", "insufficient_evidence"),
    ).strip()

    action = dict(row.get("action") or {})
    action["kind"] = request.form.get(
        f"action_kind_{candidate_id}",
        action.get("kind", "other_intervention"),
    ).strip()
    action["component_name"] = (
        request.form.get(
            f"component_name_{candidate_id}",
            action.get("component_name") or "",
        ).strip()
        or None
    )
    action["component_location"] = (
        request.form.get(
            f"component_location_{candidate_id}",
            action.get("component_location") or "",
        ).strip()
        or None
    )
    action["component_condition"] = request.form.get(
        f"component_condition_{candidate_id}",
        action.get("component_condition", "unknown"),
    ).strip()

    quantity = request.form.get(f"quantity_{candidate_id}", "").strip()
    odometer = request.form.get(f"odometer_{candidate_id}", "").strip()
    action["quantity"] = int(quantity) if quantity.isdigit() else None
    action["odometer_km"] = int(odometer) if odometer.isdigit() else None
    row["action"] = action
    row["completion_confirmed"] = (
        request.form.get(f"completion_confirmed_{candidate_id}") == "1"
    )
    row["reviewed_by_advisor"] = True
    return row


@historical_ingestion_bp.route(
    "/admin/cars/<int:car_id>/historical-records/import",
    methods=["GET", "POST"],
)
@login_required
@advisor_required
def import_document(car_id: int):
    car = Car.query.get_or_404(car_id)
    if request.method == "GET":
        return render_template("historical_ingestion/upload.html", car=car)

    uploaded = request.files.get("document")
    if uploaded is None:
        flash("Select a PDF document or WhatsApp ZIP export.", "error")
        return redirect(request.url)

    filename = str(uploaded.filename or "").lower()
    is_zip = filename.endswith(".zip") or (
        (uploaded.content_type or "").lower()
        in {"application/zip", "application/x-zip-compressed"}
    )

    try:
        if is_zip:
            result = ingest_whatsapp_bundle(
                user_id=current_user.id,
                car_id=car.id,
                file_stream=uploaded.stream,
                purpose=request.form.get("purpose", "service_document"),
                retention_days=current_app.config.get("EVIDENCE_RETENTION_DAYS"),
                storage_provider=current_app.extensions.get("evidence_storage_provider"),
                storage_config=current_app.config,
            )
        else:
            result = ingest_pdf_document_background(
                user_id=current_user.id,
                car_id=car.id,
                file_stream=uploaded.stream,
                declared_content_type=uploaded.content_type or "",
                purpose=request.form.get("purpose", "service_document"),
                visibility=request.form.get("visibility", "advisor"),
                retention_days=current_app.config.get("EVIDENCE_RETENTION_DAYS"),
                storage_provider=current_app.extensions.get("evidence_storage_provider"),
                storage_config=current_app.config,
                language_provider=current_app.extensions.get(
                    "historical_document_provider"
                ),
            )
    except (
        HistoricalDocumentValidationError,
        WhatsAppBundleValidationError,
        HistoricalIngestionConfigurationError,
    ) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(request.url)
    except HistoricalIngestionError:
        db.session.rollback()
        current_app.logger.exception(
            "historical_document_ingestion_failed car_id=%s advisor_id=%s",
            car.id,
            current_user.id,
        )
        flash("Aura could not import this historical document.", "error")
        return redirect(request.url)

    if result.status == "completed":
        flash(
            "This source already has an advisor-grade analysis ready for review.",
            "info",
        )
    elif getattr(result, "reused_analysis", False) or getattr(
        result, "reused_existing", False
    ):
        flash(
            "Rina is already analysing this source. Aura resumed the existing "
            "analysis instead of starting another one.",
            "info",
        )
    else:
        flash(
            (
                "WhatsApp case bundle stored privately. Rina will reconcile the chat, "
                "images, voice notes, videos and documents for advisor review."
                if is_zip
                else
                "Document stored privately. Rina has started advisor-grade analysis; "
                "the review page will update automatically when it is ready."
            ),
            "success",
        )

    return redirect(
        url_for(
            "historical_ingestion.review_document",
            car_id=car.id,
            evidence_id=result.evidence_id,
        )
    )


@historical_ingestion_bp.post(
    "/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/reanalyze"
)
@login_required
@advisor_required
def reanalyze_document(car_id: int, evidence_id: int):
    car = Car.query.get_or_404(car_id)
    evidence = VehicleEvidence.query.filter_by(
        id=evidence_id,
        car_id=car.id,
    ).first_or_404()

    try:
        if evidence.evidence_type == "archive":
            result = restart_whatsapp_bundle_analysis(
                evidence_id=evidence.id,
                actor_user_id=current_user.id,
            )
        else:
            result = reanalyze_stored_document_background(
                evidence_id=evidence.id,
                actor_user_id=current_user.id,
                storage_provider=current_app.extensions.get("evidence_storage_provider"),
                storage_config=current_app.config,
                language_provider=current_app.extensions.get(
                    "historical_document_provider"
                ),
            )
    except (
        HistoricalIngestionError,
        HistoricalIngestionConfigurationError,
    ) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for(
                "historical_ingestion.review_document",
                car_id=car.id,
                evidence_id=evidence.id,
            )
        )

    if result.status == "completed":
        flash(
            "Advisor-grade analysis is already ready for review.",
            "info",
        )
    elif result.reused_analysis:
        flash(
            "Rina is already analysing this source. The existing analysis was resumed.",
            "info",
        )
    else:
        flash(
            (
                "Rina has started re-analysing the original private WhatsApp bundle. "
                if evidence.evidence_type == "archive"
                else "Rina has started re-analysing the original private PDF. "
            )
            + "You can stay on this page; it will update automatically.",
            "success",
        )

    return redirect(
        url_for(
            "historical_ingestion.review_document",
            car_id=car.id,
            evidence_id=evidence.id,
        )
    )


@historical_ingestion_bp.get(
    "/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/analysis-status"
)
@login_required
@advisor_required
def analysis_status(car_id: int, evidence_id: int):
    car = Car.query.get_or_404(car_id)
    evidence = VehicleEvidence.query.filter_by(
        id=evidence_id,
        car_id=car.id,
    ).first_or_404()
    analysis = (
        latest_whatsapp_bundle_extraction(evidence.id)
        if evidence.evidence_type == "archive"
        else latest_background_extraction(evidence.id)
    )

    if analysis is None:
        return jsonify(
            {
                "status": "idle",
                "phase": "idle",
                "message": "No advisor-grade analysis is currently running.",
                "review_ready": has_completed_structured_extraction(evidence.id),
            }
        )

    if analysis.status == "processing":
        try:
            if evidence.evidence_type == "archive":
                state = advance_whatsapp_bundle_analysis(
                    extraction_id=analysis.id,
                    actor_user_id=current_user.id,
                    storage_provider=current_app.extensions.get(
                        "evidence_storage_provider"
                    ),
                    storage_config=current_app.config,
                )
            else:
                state = advance_historical_background_analysis(
                    extraction_id=analysis.id,
                    actor_user_id=current_user.id,
                    language_provider=current_app.extensions.get(
                        "historical_document_provider"
                    ),
                )
        except HistoricalIngestionError as exc:
            db.session.rollback()
            current_app.logger.warning(
                "historical_analysis_status_failed evidence_id=%s extraction_id=%s detail=%s",
                evidence.id,
                analysis.id,
                str(exc)[:500],
            )
            return jsonify(
                {
                    "status": "processing",
                    "phase": str(
                        (analysis.provenance or {}).get("background_stage")
                        or "understanding"
                    ),
                    "message": (
                        "Rina is still analysing. Aura will check again automatically."
                    ),
                    "review_ready": has_completed_structured_extraction(evidence.id),
                }
            )

        return jsonify(
            {
                "status": state.status,
                "phase": state.phase,
                "message": state.message,
                "review_ready": state.review_ready,
                "completed_items": getattr(state, "completed_items", 0),
                "total_items": getattr(state, "total_items", 0),
            }
        )

    phase = (
        "completed"
        if analysis.status == "completed"
        else "failed"
        if analysis.status == "failed"
        else str((analysis.provenance or {}).get("background_stage") or "idle")
    )
    return jsonify(
        {
            "status": analysis.status,
            "phase": phase,
            "message": (
                "Advisor-grade extraction is ready for review."
                if analysis.status == "completed"
                else "Rina could not complete this analysis. No vehicle history was changed."
            ),
            "review_ready": has_completed_structured_extraction(evidence.id),
        }
    )


@historical_ingestion_bp.get(
    "/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/review"
)
@login_required
@advisor_required
def review_document(car_id: int, evidence_id: int):
    car = Car.query.get_or_404(car_id)
    evidence = VehicleEvidence.query.filter_by(
        id=evidence_id,
        car_id=car.id,
    ).first_or_404()
    extraction = latest_structured_extraction(evidence.id)
    background_analysis = (
        latest_whatsapp_bundle_extraction(evidence.id)
        if evidence.evidence_type == "archive"
        else latest_background_extraction(evidence.id)
    )
    analysis_in_progress = bool(
        background_analysis and background_analysis.status == "processing"
    )

    payload = {}
    reviewed_payload = {}
    if extraction and extraction.status == "completed":
        payload = decrypt_extraction_payload(extraction)
        if extraction.review_status in {"accepted", "corrected"}:
            reviewed_payload = decrypt_extraction_payload(
                extraction,
                reviewed=True,
            )

    return render_template(
        "historical_ingestion/review.html",
        car=car,
        evidence=evidence,
        extraction=extraction,
        payload=reviewed_payload or payload,
        has_review=bool(reviewed_payload),
        background_analysis=background_analysis,
        analysis_in_progress=analysis_in_progress,
        analysis_kind=(
            "whatsapp_bundle" if evidence.evidence_type == "archive" else "pdf"
        ),
    )


@historical_ingestion_bp.post(
    "/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/review"
)
@login_required
@advisor_required
def save_review(car_id: int, evidence_id: int):
    car = Car.query.get_or_404(car_id)
    evidence = VehicleEvidence.query.filter_by(
        id=evidence_id,
        car_id=car.id,
    ).first_or_404()
    extraction = latest_structured_extraction(evidence.id)

    if extraction is None or extraction.status != "completed":
        flash("There is no completed structured extraction to review.", "error")
        return redirect(
            url_for(
                "historical_ingestion.review_document",
                car_id=car.id,
                evidence_id=evidence.id,
            )
        )

    source = decrypt_extraction_payload(extraction)
    reviewed = deepcopy(source)
    reviewed_candidates = []

    for row in source.get("candidates", []):
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id:
            continue
        reviewed_candidates.append(
            _reviewed_candidate(row, candidate_id)
        )

    reviewed["candidates"] = reviewed_candidates
    reviewed["advisor_review_note"] = request.form.get(
        "advisor_review_note",
        "",
    ).strip()[:3000]

    try:
        save_advisor_review(
            extraction=extraction,
            actor_user_id=current_user.id,
            reviewed_payload=reviewed,
        )
        db.session.commit()
    except HistoricalIngestionError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for(
                "historical_ingestion.review_document",
                car_id=car.id,
                evidence_id=evidence.id,
            )
        )

    flash(
        "Advisor review saved. Accepted extraction facts are now governed "
        "context; completed work is still not applied unless you choose "
        "Apply completed work.",
        "success",
    )
    return redirect(
        url_for(
            "historical_ingestion.review_document",
            car_id=car.id,
            evidence_id=evidence.id,
        )
    )


@historical_ingestion_bp.post(
    "/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/apply"
)
@login_required
@advisor_required
def apply_document(car_id: int, evidence_id: int):
    car = Car.query.get_or_404(car_id)
    evidence = VehicleEvidence.query.filter_by(
        id=evidence_id,
        car_id=car.id,
    ).first_or_404()
    extraction = latest_structured_extraction(evidence.id)

    if extraction is None:
        flash("Structured extraction was not found.", "error")
        return redirect(
            url_for(
                "historical_ingestion.review_document",
                car_id=car.id,
                evidence_id=evidence.id,
            )
        )

    try:
        plan = apply_reviewed_historical_treatment(
            extraction_id=extraction.id,
            actor_user_id=current_user.id,
        )
        db.session.commit()
    except HistoricalApplicationError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for(
                "historical_ingestion.review_document",
                car_id=car.id,
                evidence_id=evidence.id,
            )
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "historical_document_application_failed evidence_id=%s advisor_id=%s",
            evidence.id,
            current_user.id,
        )
        flash("Aura could not apply the reviewed historical record.", "error")
        return redirect(
            url_for(
                "historical_ingestion.review_document",
                car_id=car.id,
                evidence_id=evidence.id,
            )
        )

    if plan is None:
        flash(
            "Review is saved, but there are no advisor-confirmed completed-work "
            "or outcome candidates ready to apply.",
            "info",
        )
    else:
        flash(
            "Reviewed completed work was added to the vehicle's durable care history.",
            "success",
        )

    return redirect(
        url_for(
            "historical_ingestion.review_document",
            car_id=car.id,
            evidence_id=evidence.id,
        )
    )
