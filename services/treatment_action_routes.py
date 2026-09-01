"""Advisor runtime surfaces for Aura Wave 2.3C Treatment Actions and Outcomes."""

from __future__ import annotations

from datetime import datetime
import secrets

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from admin.routes import admin_bp
from admin.utils import advisor_required
from evidence.models import VehicleEvidence
from extensions import db
from models import TreatmentPlan
from security.access import resolve_vehicle_authority
from services.treatment_action_lifecycle import (
    TreatmentActionLifecycleError,
    TreatmentActionLifecycleService,
)
from services.treatment_evidence_linking import (
    TreatmentEvidenceLinkError,
    link_accepted_evidence_to_treatment_subject,
)
from services.treatment_outcome_recording import (
    TreatmentOutcomeRecordingError,
    TreatmentOutcomeRecordingService,
)
from services.treatment_plan_lifecycle import (
    TreatmentPlanLifecycleError,
    TreatmentPlanLifecycleService,
)
from treatment.models import TreatmentAction


def _plan(plan_id: int) -> TreatmentPlan:
    plan = TreatmentPlan.query.get_or_404(plan_id)
    authority = resolve_vehicle_authority(current_user.id, plan.car_id)
    if authority not in {"advisor", "administrator"}:
        abort(403)
    return plan


def _action(action_id: int) -> TreatmentAction:
    action = TreatmentAction.query.get_or_404(action_id)
    authority = resolve_vehicle_authority(current_user.id, action.car_id)
    if authority not in {"advisor", "administrator"}:
        abort(403)
    return action


def _detail_redirect(plan_id: int):
    return redirect(url_for("admin.treatment_plan_actions", plan_id=plan_id))


def _parse_datetime(value: str | None, *, required: bool = False) -> datetime | None:
    text = (value or "").strip()
    if not text:
        if required:
            raise ValueError("A date and time are required.")
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Enter a valid date and time.") from exc


@admin_bp.get("/treatment-actions")
@login_required
@advisor_required
def treatment_action_console():
    plans = TreatmentPlan.query.order_by(
        TreatmentPlan.updated_at.desc(),
        TreatmentPlan.id.desc(),
    ).limit(200).all()
    visible_plans = [
        plan
        for plan in plans
        if resolve_vehicle_authority(current_user.id, plan.car_id)
        in {"advisor", "administrator"}
    ]
    return render_template(
        "treatment_actions/advisor_index.html",
        treatment_plans=visible_plans,
    )


@admin_bp.get("/treatment-plans/<int:plan_id>/actions")
@login_required
@advisor_required
def treatment_plan_actions(plan_id: int):
    plan = _plan(plan_id)
    evidence = (
        VehicleEvidence.query.filter_by(
            car_id=plan.car_id,
            review_status="accepted",
            storage_state="available",
        )
        .filter(VehicleEvidence.deleted_at.is_(None))
        .filter(VehicleEvidence.visibility.in_(["client", "advisor"]))
        .order_by(VehicleEvidence.uploaded_at.desc(), VehicleEvidence.id.desc())
        .all()
    )
    return render_template(
        "treatment_actions/advisor_plan.html",
        plan=plan,
        actions=list(plan.actions),
        outcomes=list(plan.outcomes),
        accepted_evidence=evidence,
        action_creation_key=secrets.token_urlsafe(18),
        outcome_recording_key=secrets.token_urlsafe(18),
    )


@admin_bp.post("/treatment-plans/<int:plan_id>/schedule")
@login_required
@advisor_required
def schedule_treatment_plan_for_actions(plan_id: int):
    _plan(plan_id)
    try:
        TreatmentPlanLifecycleService.schedule(
            plan_id=plan_id,
            actor_user_id=current_user.id,
            source="admin.treatment_action_console.plan_schedule",
            operation_key=(request.form.get("operation_key") or None),
            preserved_owner_consent=(request.form.get("preserved_owner_consent") == "1"),
        )
        db.session.commit()
        flash("Treatment Plan scheduled for intervention coordination.", "success")
    except TreatmentPlanLifecycleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        raise
    return _detail_redirect(plan_id)


@admin_bp.post("/treatment-plans/<int:plan_id>/actions")
@login_required
@advisor_required
def create_treatment_action(plan_id: int):
    _plan(plan_id)
    try:
        action = TreatmentActionLifecycleService.create(
            plan_id=plan_id,
            actor_user_id=current_user.id,
            creation_key=request.form.get("creation_key", ""),
            title=request.form.get("title", ""),
            client_summary=request.form.get("client_summary"),
            internal_instructions=request.form.get("internal_instructions"),
            visibility=request.form.get("visibility", "client"),
            source="admin.treatment_action_create",
        )
        db.session.commit()
        flash(f"Treatment Action recorded: {action.title}", "success")
    except TreatmentActionLifecycleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        raise
    return _detail_redirect(plan_id)


@admin_bp.post("/treatment-actions/<int:action_id>/schedule")
@login_required
@advisor_required
def schedule_treatment_action(action_id: int):
    action = _action(action_id)
    plan_id = action.treatment_plan_id
    try:
        scheduled_for = _parse_datetime(
            request.form.get("scheduled_for"),
            required=True,
        )
        TreatmentActionLifecycleService.schedule(
            action_id=action_id,
            actor_user_id=current_user.id,
            scheduled_for=scheduled_for,
            source="admin.treatment_action_schedule",
            operation_key=(request.form.get("operation_key") or None),
        )
        db.session.commit()
        flash("Treatment Action scheduled.", "success")
    except (TreatmentActionLifecycleError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        raise
    return _detail_redirect(plan_id)


@admin_bp.post("/treatment-actions/<int:action_id>/<operation>")
@login_required
@advisor_required
def transition_treatment_action(action_id: int, operation: str):
    action = _action(action_id)
    plan_id = action.treatment_plan_id
    operations = {
        "start": (TreatmentActionLifecycleService.start, "Treatment Action started."),
        "complete": (
            TreatmentActionLifecycleService.complete,
            "Treatment Action recorded complete. No outcome is implied.",
        ),
        "defer": (TreatmentActionLifecycleService.defer, "Treatment Action deferred."),
        "cancel": (TreatmentActionLifecycleService.cancel, "Treatment Action cancelled."),
    }
    selected = operations.get(operation)
    if selected is None:
        abort(404)

    method, message = selected
    try:
        method(
            action_id=action_id,
            actor_user_id=current_user.id,
            source=f"admin.treatment_action_{operation}",
            operation_key=(request.form.get("operation_key") or None),
        )
        db.session.commit()
        flash(message, "success")
    except TreatmentActionLifecycleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        raise
    return _detail_redirect(plan_id)


@admin_bp.post("/treatment-actions/<int:action_id>/evidence")
@login_required
@advisor_required
def link_treatment_action_evidence(action_id: int):
    action = _action(action_id)
    plan_id = action.treatment_plan_id
    evidence_ids = request.form.getlist("evidence_ids")
    if not evidence_ids:
        flash("Select at least one accepted evidence record.", "error")
        return _detail_redirect(plan_id)

    try:
        for raw_id in evidence_ids:
            link_accepted_evidence_to_treatment_subject(
                actor_user_id=current_user.id,
                evidence_id=int(raw_id),
                subject_type="treatment_action",
                subject_id=action_id,
                relationship_type=request.form.get("relationship_type", "documents"),
            )
        db.session.commit()
        flash("Accepted evidence linked to Treatment Action.", "success")
    except (TreatmentEvidenceLinkError, TypeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        raise
    return _detail_redirect(plan_id)


@admin_bp.post("/treatment-plans/<int:plan_id>/outcomes")
@login_required
@advisor_required
def record_treatment_outcome(plan_id: int):
    _plan(plan_id)
    provenance_kind = (request.form.get("provenance_kind") or "").strip().lower()
    provenance_data = None
    if provenance_kind == "professional_observation":
        provenance_data = {
            "observation_source": request.form.get("observation_source", ""),
            "reference": request.form.get("observation_reference", ""),
        }

    raw_action_id = (request.form.get("treatment_action_id") or "").strip()
    try:
        treatment_action_id = int(raw_action_id) if raw_action_id else None
        observed_at = _parse_datetime(request.form.get("observed_at"))
        outcome = TreatmentOutcomeRecordingService.record(
            plan_id=plan_id,
            actor_user_id=current_user.id,
            recording_key=request.form.get("recording_key", ""),
            progression_direction=request.form.get("progression_direction", ""),
            summary=request.form.get("summary", ""),
            provenance_kind=provenance_kind,
            evidence_ids=request.form.getlist("evidence_ids"),
            treatment_action_id=treatment_action_id,
            advisor_note=request.form.get("advisor_note"),
            visibility=request.form.get("visibility", "client"),
            provenance_data=provenance_data,
            observed_at=observed_at,
            source="admin.treatment_outcome_record",
        )
        db.session.commit()
        flash(
            f"Treatment Outcome recorded: {outcome.progression_direction.replace('_', ' ')}.",
            "success",
        )
    except (TreatmentOutcomeRecordingError, TypeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        raise
    return _detail_redirect(plan_id)
