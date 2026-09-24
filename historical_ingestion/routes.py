"""Advisor routes for governed historical-source ingestion and review."""

from __future__ import annotations

from copy import deepcopy
import uuid

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
from werkzeug.exceptions import NotFound

from admin.utils import advisor_required
from evidence.models import (
    SUPPORTED_HISTORICAL_IMPORT_SOURCE_TYPES,
    EvidenceExtraction,
    VehicleEvidence,
)
from extensions import db
from historical_ingestion.application import (
    HistoricalApplicationError,
    apply_reviewed_historical_treatment,
)
from historical_ingestion.case_attribution import (
    HistoricalCaseAttributionError,
    attribution_payload,
    create_episode_from_finalized_source,
    episodes_for_car,
    latest_case_attribution,
    start_case_attribution,
)
from historical_ingestion.models import HistoricalServiceEpisode
from historical_ingestion.reconciliation import (
    HistoricalReconciliationError,
    applied_reconciliation_plan,
    apply_reconciliation,
    durable_work_for_episode,
    latest_reconciliation,
    reconciliation_payload,
    reconciliation_signal,
    save_reconciliation_review,
    start_episode_reconciliation,
)
from historical_ingestion.background_runner import (
    advance_historical_analysis_once,
)
from historical_ingestion.service import (
    HistoricalDocumentValidationError,
    HistoricalIngestionConfigurationError,
    HistoricalIngestionError,
    HistoricalSourceSupersessionError,
    decrypt_extraction_payload,
    has_completed_structured_extraction,
    historical_source_summaries,
    ingest_pdf_document_background,
    latest_background_extraction,
    latest_structured_extraction,
    reanalyze_stored_document_background,
    save_advisor_review,
    supersede_historical_source,
    supersession_replacement_candidates,
)
from historical_ingestion.whatsapp_bundle import (
    WhatsAppBundleValidationError,
    ingest_whatsapp_bundle,
    latest_whatsapp_bundle_extraction,
    restart_whatsapp_bundle_analysis,
)
from models import Car
from services.treatment_action_addenda import (
    TreatmentActionAddendumError,
    add_treatment_action_addendum,
)


historical_ingestion_bp = Blueprint("historical_ingestion", __name__)


def _validate_historical_source_upload(
    *,
    source_type: str,
    filename: str,
    content_type: str,
) -> str | None:
    if source_type not in SUPPORTED_HISTORICAL_IMPORT_SOURCE_TYPES:
        return "Select a supported historical source type."

    normalized_name = (filename or "").strip().lower()
    normalized_content_type = (content_type or "").strip().lower()
    is_zip = normalized_name.endswith(".zip") or normalized_content_type in {
        "application/zip",
        "application/x-zip-compressed",
    }

    if source_type == "whatsapp_conversation" and not is_zip:
        return (
            "WhatsApp conversation sources must be uploaded as the original "
            "WhatsApp ZIP export."
        )
    if source_type == "standalone_document" and is_zip:
        return "Standalone document sources accept PDF files, not ZIP archives."
    return None


def _is_async_historical_upload() -> bool:
    return request.headers.get("X-Aura-Upload", "").strip() == "1"


def _historical_upload_error(message: str, *, status: int = 400):
    if _is_async_historical_upload():
        return jsonify({"ok": False, "message": message}), status
    flash(message, "error")
    return redirect(request.url)


def _canonical_historical_source(evidence: VehicleEvidence) -> VehicleEvidence:
    """Return a bundle root when a stale URL points at one of its child files."""

    parent_link = next(iter(evidence.bundle_parent_items or []), None)
    if parent_link is not None and parent_link.bundle is not None:
        return parent_link.bundle
    return evidence


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


@historical_ingestion_bp.get(
    "/admin/cars/<int:car_id>/historical-records"
)
@login_required
@advisor_required
def source_library(car_id: int):
    car = Car.query.get_or_404(car_id)
    show_superseded = request.args.get("show_superseded", "").strip() == "1"
    active_sources = historical_source_summaries(car.id)
    all_sources = historical_source_summaries(
        car.id,
        include_superseded=True,
    )
    superseded_count = sum(item.state == "superseded" for item in all_sources)
    sources = all_sources if show_superseded else active_sources
    counts = {
        "all": len(active_sources),
        "analyzing": sum(item.state == "analyzing" for item in active_sources),
        "ready_for_review": sum(
            item.state == "ready_for_review" for item in active_sources
        ),
        "finalized": sum(item.state == "finalized" for item in active_sources),
        "attention": sum(
            item.state in {"analysis_failed", "stored"} for item in active_sources
        ),
    }
    whatsapp_sources = [
        item.evidence
        for item in active_sources
        if item.evidence.historical_source_type == "whatsapp_conversation"
        and item.evidence.evidence_type == "archive"
    ]
    episode_views = []
    state_priority = {
        "none": 0,
        "reconciled": 1,
        "preparing": 2,
        "advisor_review": 3,
        "ready_to_apply": 4,
        "needs_attention": 5,
        "reconciliation_needed": 6,
    }
    for episode in episodes_for_car(car.id):
        reconciliation_state = "none"
        for source in whatsapp_sources:
            analysis = latest_case_attribution(
                episode_id=episode.id,
                corpus_evidence_id=source.id,
            )
            if analysis is None or analysis.status != "completed":
                continue
            payload = attribution_payload(analysis)
            if not reconciliation_signal(payload):
                continue

            reconciliation = latest_reconciliation(
                episode_id=episode.id,
                attribution_extraction_id=analysis.id,
            )
            candidate_state = "reconciliation_needed"
            if reconciliation is not None:
                if reconciliation.status == "processing":
                    candidate_state = "preparing"
                elif reconciliation.status == "failed":
                    candidate_state = "needs_attention"
                elif applied_reconciliation_plan(reconciliation.id) is not None:
                    candidate_state = "reconciled"
                elif reconciliation.review_status in {"accepted", "corrected"}:
                    candidate_state = "ready_to_apply"
                elif reconciliation.status == "completed":
                    candidate_state = "advisor_review"

            if state_priority[candidate_state] > state_priority[reconciliation_state]:
                reconciliation_state = candidate_state

        episode_views.append(
            {
                "episode": episode,
                "reconciliation_state": reconciliation_state,
            }
        )

    return render_template(
        "historical_ingestion/library.html",
        car=car,
        sources=sources,
        counts=counts,
        superseded_count=superseded_count,
        show_superseded=show_superseded,
        episode_views=episode_views,
    )


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

    source_type = request.form.get("source_type", "").strip().lower()

    uploaded = request.files.get("document")
    if uploaded is None:
        return _historical_upload_error("Select a source file to upload.")

    validation_error = _validate_historical_source_upload(
        source_type=source_type,
        filename=str(uploaded.filename or ""),
        content_type=uploaded.content_type or "",
    )
    if validation_error:
        return _historical_upload_error(validation_error)

    try:
        if source_type == "whatsapp_conversation":
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
        return _historical_upload_error(str(exc))
    except HistoricalIngestionError:
        db.session.rollback()
        current_app.logger.exception(
            "historical_document_ingestion_failed car_id=%s advisor_id=%s",
            car.id,
            current_user.id,
        )
        return _historical_upload_error(
            "Aura could not import this historical source.",
            status=500,
        )

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
                if source_type == "whatsapp_conversation"
                else
                "Document stored privately. Rina has started advisor-grade analysis; "
                "the review page will update automatically when it is ready."
            ),
            "success",
        )

    review_url = url_for(
        "historical_ingestion.review_document",
        car_id=car.id,
        evidence_id=result.evidence_id,
    )
    if _is_async_historical_upload():
        return jsonify(
            {
                "ok": True,
                "redirect_url": review_url,
                "status": result.status,
                "evidence_id": result.evidence_id,
            }
        )
    return redirect(review_url)


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
    evidence = _canonical_historical_source(evidence)

    if evidence.review_status == "superseded":
        flash(
            "This source is archived as superseded. Re-analysis is disabled; "
            "open the active replacement source instead.",
            "info",
        )
        return redirect(
            url_for(
                "historical_ingestion.review_document",
                car_id=car.id,
                evidence_id=evidence.id,
            )
        )

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
    elif getattr(result, "reused_analysis", False) or getattr(
        result, "reused_existing", False
    ):
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
    evidence = _canonical_historical_source(evidence)
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
            # The production background runner owns progress even when the client
            # disconnects. A status request may opportunistically advance one
            # transition only when it can acquire the same cross-process lock.
            state = advance_historical_analysis_once(
                current_app._get_current_object(),
                analysis.id,
            )
        except HistoricalIngestionError as exc:
            db.session.rollback()
            current_app.logger.warning(
                "historical_analysis_status_failed evidence_id=%s extraction_id=%s detail=%s",
                evidence.id,
                analysis.id,
                str(exc)[:500],
            )
            state = None

        if state is None:
            db.session.expire_all()
            analysis = db.session.get(type(analysis), analysis.id) or analysis
            phase = str(
                (analysis.provenance or {}).get("background_stage")
                or ("preprocessing" if evidence.evidence_type == "archive" else "understanding")
            )
            return jsonify(
                {
                    "status": "processing",
                    "phase": phase,
                    "message": (
                        "Rina is continuing this analysis securely on the server. "
                        "You can leave this page and come back later."
                    ),
                    "review_ready": has_completed_structured_extraction(evidence.id),
                    "completed_items": 0,
                    "total_items": 0,
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
    evidence = _canonical_historical_source(evidence)
    extraction = latest_structured_extraction(evidence.id)
    background_analysis = (
        latest_whatsapp_bundle_extraction(evidence.id)
        if evidence.evidence_type == "archive"
        else latest_background_extraction(evidence.id)
    )
    analysis_in_progress = bool(
        background_analysis and background_analysis.status == "processing"
    )
    analysis_available = bool(extraction and extraction.status == "completed")
    analysis_reviewed = bool(
        analysis_available
        and (
            evidence.review_status == "accepted"
            or extraction.review_status in {"accepted", "corrected"}
        )
    )
    analysis_failed = bool(
        background_analysis
        and background_analysis.status == "failed"
        and not analysis_available
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

    replacement_sources = (
        supersession_replacement_candidates(
            car.id,
            exclude_evidence_id=evidence.id,
        )
        if (
            evidence.historical_source_type == "standalone_document"
            and evidence.review_status == "pending_review"
        )
        else []
    )
    historical_episode = HistoricalServiceEpisode.query.filter_by(
        anchor_evidence_id=evidence.id
    ).first()

    return render_template(
        "historical_ingestion/review.html",
        car=car,
        evidence=evidence,
        extraction=extraction,
        payload=reviewed_payload or payload,
        has_review=bool(reviewed_payload),
        background_analysis=background_analysis,
        analysis_in_progress=analysis_in_progress,
        analysis_available=analysis_available,
        analysis_reviewed=analysis_reviewed,
        analysis_failed=analysis_failed,
        replacement_sources=replacement_sources,
        historical_episode=historical_episode,
        analysis_kind=(
            "whatsapp_bundle"
            if evidence.historical_source_type == "whatsapp_conversation"
            else "pdf"
        ),
    )


@historical_ingestion_bp.post(
    "/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/create-episode"
)
@login_required
@advisor_required
def create_historical_episode(car_id: int, evidence_id: int):
    car = Car.query.get_or_404(car_id)
    try:
        result = create_episode_from_finalized_source(
            car_id=car.id,
            evidence_id=evidence_id,
            actor_user_id=current_user.id,
        )
        db.session.commit()
    except HistoricalCaseAttributionError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for(
                "historical_ingestion.review_document",
                car_id=car.id,
                evidence_id=evidence_id,
            )
        )

    flash(
        (
            "Historical service episode created from the finalised source."
            if result.created
            else "This finalised source already anchors a historical service episode."
        ),
        "success" if result.created else "info",
    )
    return redirect(
        url_for(
            "historical_ingestion.episode_detail",
            car_id=car.id,
            episode_id=result.episode_id,
        )
    )


@historical_ingestion_bp.get(
    "/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>"
)
@login_required
@advisor_required
def episode_detail(car_id: int, episode_id: int):
    car = Car.query.get_or_404(car_id)
    episode = HistoricalServiceEpisode.query.filter_by(
        id=episode_id,
        car_id=car.id,
        status="active",
    ).first_or_404()

    active_sources = historical_source_summaries(car.id)
    whatsapp_sources = [
        item.evidence
        for item in active_sources
        if item.evidence.historical_source_type == "whatsapp_conversation"
        and item.evidence.evidence_type == "archive"
    ]

    attribution_views = []
    for source in whatsapp_sources:
        analysis = latest_case_attribution(
            episode_id=episode.id,
            corpus_evidence_id=source.id,
        )
        payload = (
            attribution_payload(analysis)
            if analysis is not None and analysis.status == "completed"
            else {}
        )
        reconciliation = (
            latest_reconciliation(
                episode_id=episode.id,
                attribution_extraction_id=analysis.id,
            )
            if analysis is not None and analysis.status == "completed"
            else None
        )
        reconciliation_result = (
            reconciliation_payload(reconciliation)
            if reconciliation is not None
            and reconciliation.status == "completed"
            else {}
        )
        reconciliation_review = (
            reconciliation_payload(reconciliation, reviewed=True)
            if reconciliation is not None
            and reconciliation.status == "completed"
            and reconciliation.review_status in {"accepted", "corrected"}
            else {}
        )
        applied_plan = (
            applied_reconciliation_plan(reconciliation.id)
            if reconciliation is not None
            else None
        )
        attribution_views.append(
            {
                "source": source,
                "analysis": analysis,
                "payload": payload,
                "reconciliation_needed": reconciliation_signal(payload),
                "reconciliation": reconciliation,
                "reconciliation_result": reconciliation_result,
                "reconciliation_review": reconciliation_review,
                "reconciliation_plan": applied_plan,
            }
        )

    durable_episode_work = durable_work_for_episode(episode)
    addendum_keys = {
        int(item["treatment_action_id"]): uuid.uuid4().hex
        for item in durable_episode_work
        if item.get("treatment_action_id") is not None
    }

    return render_template(
        "historical_ingestion/episode.html",
        car=car,
        episode=episode,
        attribution_views=attribution_views,
        durable_episode_work=durable_episode_work,
        addendum_keys=addendum_keys,
    )


@historical_ingestion_bp.post(
    "/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/"
    "treatment-actions/<int:treatment_action_id>/addenda"
)
@login_required
@advisor_required
def add_episode_treatment_action_addendum(
    car_id: int,
    episode_id: int,
    treatment_action_id: int,
):
    car = Car.query.get_or_404(car_id)
    episode = HistoricalServiceEpisode.query.filter_by(
        id=episode_id,
        car_id=car.id,
        status="active",
    ).first_or_404()

    allowed_action_ids = {
        int(item["treatment_action_id"])
        for item in durable_work_for_episode(episode)
        if item.get("treatment_action_id") is not None
    }
    if treatment_action_id not in allowed_action_ids:
        raise NotFound()

    try:
        add_treatment_action_addendum(
            treatment_action_id=treatment_action_id,
            actor_user_id=current_user.id,
            category=request.form.get("category", "additional_information"),
            reason=request.form.get("reason", ""),
            visibility=request.form.get("visibility", "advisor"),
            detail_text=request.form.get("detail_text", ""),
            idempotency_key=request.form.get("idempotency_key", ""),
        )
        db.session.commit()
    except TreatmentActionAddendumError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        flash(
            "Detail added as an immutable addendum. The original completed-work record was not changed.",
            "success",
        )

    return redirect(
        url_for(
            "historical_ingestion.episode_detail",
            car_id=car.id,
            episode_id=episode.id,
        )
    )


@historical_ingestion_bp.post(
    "/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/"
    "attribute/<int:corpus_evidence_id>"
)
@login_required
@advisor_required
def start_episode_attribution(
    car_id: int,
    episode_id: int,
    corpus_evidence_id: int,
):
    car = Car.query.get_or_404(car_id)
    episode = HistoricalServiceEpisode.query.filter_by(
        id=episode_id,
        car_id=car.id,
        status="active",
    ).first_or_404()

    try:
        result = start_case_attribution(
            episode_id=episode.id,
            corpus_evidence_id=corpus_evidence_id,
            actor_user_id=current_user.id,
        )
    except HistoricalCaseAttributionError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for(
                "historical_ingestion.episode_detail",
                car_id=car.id,
                episode_id=episode.id,
            )
        )

    flash(
        (
            "Rina is separating this historical episode from the stored WhatsApp corpus."
            if result.status == "processing" and not result.reused_existing
            else "Aura reopened the existing case-attribution result."
        ),
        "success" if not result.reused_existing else "info",
    )
    return redirect(
        url_for(
            "historical_ingestion.episode_detail",
            car_id=car.id,
            episode_id=episode.id,
        )
    )


@historical_ingestion_bp.get(
    "/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/"
    "attributions/<int:extraction_id>/status"
)
@login_required
@advisor_required
def episode_attribution_status(
    car_id: int,
    episode_id: int,
    extraction_id: int,
):
    car = Car.query.get_or_404(car_id)
    episode = HistoricalServiceEpisode.query.filter_by(
        id=episode_id,
        car_id=car.id,
        status="active",
    ).first_or_404()
    analysis = db.session.get(EvidenceExtraction, extraction_id)
    if analysis is None:
        return jsonify(
            {
                "status": "missing",
                "phase": "missing",
                "message": "Historical case attribution was not found.",
                "review_ready": False,
            }
        ), 404

    provenance = analysis.provenance or {}
    if (
        analysis.extraction_type != "historical_case_attribution"
        or int(provenance.get("episode_id") or 0) != episode.id
        or analysis.evidence is None
        or analysis.evidence.car_id != car.id
    ):
        return jsonify(
            {
                "status": "invalid",
                "phase": "invalid",
                "message": "Historical case attribution does not belong to this episode.",
                "review_ready": False,
            }
        ), 404

    if analysis.status == "processing":
        try:
            state = advance_historical_analysis_once(
                current_app._get_current_object(),
                analysis.id,
            )
        except HistoricalIngestionError as exc:
            db.session.rollback()
            current_app.logger.warning(
                "historical_case_attribution_status_failed episode_id=%s extraction_id=%s detail=%s",
                episode.id,
                analysis.id,
                str(exc)[:500],
            )
            state = None

        if state is not None:
            return jsonify(
                {
                    "status": state.status,
                    "phase": state.phase,
                    "message": state.message,
                    "review_ready": state.review_ready,
                }
            )

        db.session.expire_all()
        analysis = db.session.get(EvidenceExtraction, extraction_id) or analysis

    return jsonify(
        {
            "status": analysis.status,
            "phase": str(
                (analysis.provenance or {}).get("background_stage")
                or analysis.status
            ),
            "message": (
                "Historical case attribution is ready for advisor review."
                if analysis.status == "completed"
                else "Rina is still separating this episode from the WhatsApp history."
                if analysis.status == "processing"
                else "Historical case attribution failed safely. No vehicle history changed."
            ),
            "review_ready": analysis.status == "completed",
        }
    )


@historical_ingestion_bp.post(
    "/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/"
    "reconcile/<int:attribution_extraction_id>/start"
)
@login_required
@advisor_required
def start_episode_reconciliation_review(
    car_id: int,
    episode_id: int,
    attribution_extraction_id: int,
):
    car = Car.query.get_or_404(car_id)
    episode = HistoricalServiceEpisode.query.filter_by(
        id=episode_id,
        car_id=car.id,
        status="active",
    ).first_or_404()

    try:
        result = start_episode_reconciliation(
            episode_id=episode.id,
            attribution_extraction_id=attribution_extraction_id,
            actor_user_id=current_user.id,
        )
    except HistoricalReconciliationError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for(
                "historical_ingestion.episode_detail",
                car_id=car.id,
                episode_id=episode.id,
            )
        )

    flash(
        (
            "Rina is preparing a reconciliation checklist for work that may be missing "
            "from durable history."
            if result.status == "processing" and not result.reused_existing
            else "Aura reopened the existing reconciliation review."
        ),
        "success" if not result.reused_existing else "info",
    )
    return redirect(
        url_for(
            "historical_ingestion.episode_detail",
            car_id=car.id,
            episode_id=episode.id,
        )
    )


@historical_ingestion_bp.get(
    "/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/"
    "reconciliations/<int:extraction_id>/status"
)
@login_required
@advisor_required
def episode_reconciliation_status(
    car_id: int,
    episode_id: int,
    extraction_id: int,
):
    car = Car.query.get_or_404(car_id)
    episode = HistoricalServiceEpisode.query.filter_by(
        id=episode_id,
        car_id=car.id,
        status="active",
    ).first_or_404()
    analysis = db.session.get(EvidenceExtraction, extraction_id)
    if analysis is None:
        return jsonify(
            {
                "status": "missing",
                "phase": "missing",
                "message": "Historical reconciliation was not found.",
                "review_ready": False,
            }
        ), 404

    provenance = analysis.provenance or {}
    if (
        analysis.extraction_type != "historical_reconciliation"
        or int(provenance.get("episode_id") or 0) != episode.id
        or analysis.evidence is None
        or analysis.evidence.car_id != car.id
    ):
        return jsonify(
            {
                "status": "invalid",
                "phase": "invalid",
                "message": "Historical reconciliation does not belong to this episode.",
                "review_ready": False,
            }
        ), 404

    if analysis.status == "processing":
        try:
            state = advance_historical_analysis_once(
                current_app._get_current_object(),
                analysis.id,
            )
        except HistoricalIngestionError as exc:
            db.session.rollback()
            current_app.logger.warning(
                "historical_reconciliation_status_failed episode_id=%s extraction_id=%s detail=%s",
                episode.id,
                analysis.id,
                str(exc)[:500],
            )
            state = None

        if state is not None:
            return jsonify(
                {
                    "status": state.status,
                    "phase": state.phase,
                    "message": state.message,
                    "review_ready": state.review_ready,
                }
            )
        db.session.expire_all()
        analysis = db.session.get(EvidenceExtraction, extraction_id) or analysis

    return jsonify(
        {
            "status": analysis.status,
            "phase": str(
                (analysis.provenance or {}).get("background_stage")
                or analysis.status
            ),
            "message": (
                "Historical reconciliation is ready for advisor decisions."
                if analysis.status == "completed"
                else "Rina is preparing the advisor reconciliation checklist."
                if analysis.status == "processing"
                else "Historical reconciliation failed safely. No vehicle history changed."
            ),
            "review_ready": analysis.status == "completed",
        }
    )


@historical_ingestion_bp.post(
    "/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/"
    "reconciliations/<int:extraction_id>/review"
)
@login_required
@advisor_required
def save_episode_reconciliation_review(
    car_id: int,
    episode_id: int,
    extraction_id: int,
):
    car = Car.query.get_or_404(car_id)
    episode = HistoricalServiceEpisode.query.filter_by(
        id=episode_id,
        car_id=car.id,
        status="active",
    ).first_or_404()
    extraction = db.session.get(EvidenceExtraction, extraction_id)
    if extraction is None:
        raise NotFound()

    provenance = extraction.provenance or {}
    if (
        extraction.extraction_type != "historical_reconciliation"
        or int(provenance.get("episode_id") or 0) != episode.id
        or extraction.evidence is None
        or extraction.evidence.car_id != car.id
    ):
        raise NotFound()

    source = reconciliation_payload(extraction)
    rows = source.get("candidates") if isinstance(source, dict) else []
    reviewed_candidates = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidate_id") or "").strip()
        decision = request.form.get(
            f"decision_{candidate_id}",
            "",
        ).strip()
        kind = request.form.get(
            f"kind_{candidate_id}",
            str(row.get("kind") or "other_intervention"),
        ).strip()
        component_name = request.form.get(
            f"component_name_{candidate_id}",
            str(row.get("component_name") or ""),
        ).strip()
        component_location = request.form.get(
            f"component_location_{candidate_id}",
            str(row.get("component_location") or ""),
        ).strip()
        occurred_at = request.form.get(
            f"occurred_at_{candidate_id}",
            str(row.get("suggested_occurred_at") or ""),
        ).strip()
        condition = request.form.get(
            f"component_condition_{candidate_id}",
            str(row.get("component_condition") or "unknown"),
        ).strip()
        advisor_note = request.form.get(
            f"advisor_note_{candidate_id}",
            "",
        ).strip()

        reviewed = dict(row)
        reviewed["advisor_decision"] = decision
        reviewed["kind"] = kind
        reviewed["component_name"] = component_name or None
        reviewed["component_location"] = component_location or None
        reviewed["occurred_at"] = occurred_at or None
        reviewed["component_condition"] = condition
        reviewed["advisor_note"] = advisor_note[:1200]
        reviewed_candidates.append(reviewed)

    reviewed_payload = {
        **source,
        "candidates": reviewed_candidates,
        "advisor_review_note": request.form.get(
            "advisor_review_note",
            "",
        ).strip()[:2000],
    }

    try:
        save_reconciliation_review(
            extraction_id=extraction.id,
            actor_user_id=current_user.id,
            reviewed_payload=reviewed_payload,
        )
        db.session.commit()
    except HistoricalReconciliationError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for(
                "historical_ingestion.episode_detail",
                car_id=car.id,
                episode_id=episode.id,
            )
        )

    confirmed_count = sum(
        row.get("advisor_decision") == "confirmed"
        for row in reviewed_candidates
    )
    flash(
        (
            f"Reconciliation decisions saved. {confirmed_count} completed "
            "intervention(s) are ready to apply to durable history."
        ),
        "success",
    )
    return redirect(
        url_for(
            "historical_ingestion.episode_detail",
            car_id=car.id,
            episode_id=episode.id,
        )
    )


@historical_ingestion_bp.post(
    "/admin/cars/<int:car_id>/historical-episodes/<int:episode_id>/"
    "reconciliations/<int:extraction_id>/apply"
)
@login_required
@advisor_required
def apply_episode_reconciliation(
    car_id: int,
    episode_id: int,
    extraction_id: int,
):
    car = Car.query.get_or_404(car_id)
    episode = HistoricalServiceEpisode.query.filter_by(
        id=episode_id,
        car_id=car.id,
        status="active",
    ).first_or_404()
    extraction = db.session.get(EvidenceExtraction, extraction_id)
    if extraction is None:
        raise NotFound()
    provenance = extraction.provenance or {}
    if (
        extraction.extraction_type != "historical_reconciliation"
        or int(provenance.get("episode_id") or 0) != episode.id
        or extraction.evidence is None
        or extraction.evidence.car_id != car.id
    ):
        raise NotFound()

    try:
        plan = apply_reconciliation(
            extraction_id=extraction.id,
            actor_user_id=current_user.id,
        )
        db.session.commit()
    except HistoricalReconciliationError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for(
                "historical_ingestion.episode_detail",
                car_id=car.id,
                episode_id=episode.id,
            )
        )

    if plan is None:
        flash("No completed work was confirmed for durable history.", "info")
    else:
        flash(
            "Advisor-confirmed reconciliation was added to the vehicle's durable care history.",
            "success",
        )
    return redirect(
        url_for(
            "historical_ingestion.episode_detail",
            car_id=car.id,
            episode_id=episode.id,
        )
    )


@historical_ingestion_bp.post(
    "/admin/cars/<int:car_id>/historical-records/<int:evidence_id>/supersede"
)
@login_required
@advisor_required
def supersede_source(car_id: int, evidence_id: int):
    car = Car.query.get_or_404(car_id)
    source = VehicleEvidence.query.filter_by(
        id=evidence_id,
        car_id=car.id,
    ).first_or_404()
    source = _canonical_historical_source(source)

    replacement_raw = request.form.get("replacement_evidence_id", "").strip()
    try:
        replacement_evidence_id = int(replacement_raw)
    except (TypeError, ValueError):
        flash("Choose the finalised source that replaces this record.", "error")
        return redirect(
            url_for(
                "historical_ingestion.review_document",
                car_id=car.id,
                evidence_id=source.id,
            )
        )

    try:
        supersede_historical_source(
            evidence_id=source.id,
            replacement_evidence_id=replacement_evidence_id,
            actor_user_id=current_user.id,
        )
        db.session.commit()
    except HistoricalSourceSupersessionError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(
            url_for(
                "historical_ingestion.review_document",
                car_id=car.id,
                evidence_id=source.id,
            )
        )

    flash(
        f"Evidence #{source.id} was archived as superseded. "
        f"Evidence #{replacement_evidence_id} remains the active source.",
        "success",
    )
    return redirect(
        url_for("historical_ingestion.source_library", car_id=car.id)
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
    evidence = _canonical_historical_source(evidence)
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
    evidence = _canonical_historical_source(evidence)
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
