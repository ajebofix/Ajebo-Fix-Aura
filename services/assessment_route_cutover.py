"""Runtime cutover for the Wave 2.2B Vehicle Assessment lifecycle.

The legacy admin routes remain bound for compatibility, but these handlers
replace their view functions at blueprint registration time. Raw form details
stay in this HTTP adapter; lifecycle legality and persistence live in
AssessmentLifecycleService.
"""

from __future__ import annotations

from collections.abc import Sequence

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from admin.routes import admin_bp
from admin.utils import advisor_required
from extensions import db
from models import TreatmentPlan, VehicleAssessment
from services.assessment_lifecycle import (
    AssessmentLifecycleError,
    AssessmentLifecycleService,
)
from services.assessment_risk_engine import calculate_assessment_risk
from services.treatment_plan_lifecycle import TreatmentPlanLifecycleService


class AssessmentDraftFormError(ValueError):
    """Raised when the assessment form cannot be normalized safely."""


def _submitted_list(name: str) -> list[str] | None:
    """Accept canonical and historical ``[]`` repeated field names."""

    if name in request.form:
        return request.form.getlist(name)

    legacy_name = f"{name}[]"
    if legacy_name in request.form:
        return request.form.getlist(legacy_name)

    return None


def _require_parallel_groups(
    *,
    group_name: str,
    groups: Sequence[list[str] | None],
) -> bool:
    if all(group is None for group in groups):
        return False

    if any(group is None for group in groups):
        raise AssessmentDraftFormError(
            f"Incomplete {group_name} form submission. Existing data was preserved."
        )

    lengths = {len(group or []) for group in groups}
    if len(lengths) != 1:
        raise AssessmentDraftFormError(
            f"Mismatched {group_name} rows. Existing data was preserved."
        )

    return True


def _normalise_draft_submission() -> tuple[
    dict[str, str | None],
    list[dict[str, str]] | None,
    list[dict[str, str]] | None,
]:
    risk_descriptions = _submitted_list("risk_description")
    risk_causes = _submitted_list("risk_cause")
    risk_consequences = _submitted_list("risk_consequence")
    risk_urgencies = _submitted_list("risk_urgency")

    treatment_titles = _submitted_list("treatment_title")
    treatment_descriptions = _submitted_list("treatment_description")
    treatment_codes = _submitted_list("treatment_code")

    replace_risks = _require_parallel_groups(
        group_name="risk",
        groups=(
            risk_descriptions,
            risk_causes,
            risk_consequences,
            risk_urgencies,
        ),
    )
    replace_treatments = _require_parallel_groups(
        group_name="treatment option",
        groups=(
            treatment_titles,
            treatment_descriptions,
            treatment_codes,
        ),
    )

    risks: list[dict[str, str]] | None = None
    if replace_risks:
        risks = []
        for description, cause, consequence, urgency in zip(
            risk_descriptions or [],
            risk_causes or [],
            risk_consequences or [],
            risk_urgencies or [],
            strict=True,
        ):
            clean_description = (description or "").strip()
            clean_cause = (cause or "").strip()
            clean_consequence = (consequence or "").strip()
            clean_urgency = (urgency or "").strip()

            if not clean_description:
                if clean_cause or clean_consequence:
                    raise AssessmentDraftFormError(
                        "A partially completed risk row was not saved. "
                        "Existing data was preserved."
                    )
                continue

            risks.append(
                {
                    "description": clean_description,
                    "likely_cause": clean_cause,
                    "consequence_if_ignored": clean_consequence,
                    "urgency": clean_urgency or "monitoring",
                }
            )

    treatment_options: list[dict[str, str]] | None = None
    if replace_treatments:
        treatment_options = []
        for title, description, code in zip(
            treatment_titles or [],
            treatment_descriptions or [],
            treatment_codes or [],
            strict=True,
        ):
            clean_title = (title or "").strip()
            clean_description = (description or "").strip()
            clean_code = (code or "").strip()

            if not clean_title and not clean_description:
                continue
            if not clean_title or not clean_description:
                raise AssessmentDraftFormError(
                    "A partially completed treatment option was not saved. "
                    "Existing data was preserved."
                )

            treatment_options.append(
                {
                    "option_code": clean_code,
                    "title": clean_title,
                    "description": clean_description,
                }
            )

    form_to_model = {
        "engine_status": "engine_status",
        "transmission_status": "transmission_status",
        "suspension_status": "suspension_status",
        "electrical_status": "electrical_status",
        "cooling_status": "cooling_status",
        "cost_vs_consequence_analysis": "cost_consequence_analysis",
        "professional_recommendation": "professional_recommendation",
    }
    scalar_updates = {
        model_name: request.form.get(form_name)
        for form_name, model_name in form_to_model.items()
        if form_name in request.form
    }

    return scalar_updates, risks, treatment_options


@login_required
@advisor_required
def admin_start_assessment_cutover(consultation_id: int):
    existing = VehicleAssessment.query.filter_by(
        consultation_id=consultation_id
    ).first()

    try:
        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation_id,
            actor_user_id=current_user.id,
            source="admin.assessment_start",
        )
        db.session.commit()
    except AssessmentLifecycleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_consultations"))
    except Exception:
        db.session.rollback()
        raise

    flash(
        "Continuing draft assessment." if existing else "Assessment started.",
        "info" if existing else "success",
    )
    return redirect(
        url_for("admin.admin_edit_assessment", assessment_id=assessment.id)
    )


@login_required
@advisor_required
def admin_edit_assessment_cutover(assessment_id: int):
    assessment = VehicleAssessment.query.get_or_404(assessment_id)

    if assessment.status != "draft" or assessment.is_finalized:
        abort(403)

    if request.method == "POST":
        try:
            scalar_updates, risks, treatment_options = _normalise_draft_submission()
            assessment = AssessmentLifecycleService.save_draft(
                assessment_id=assessment.id,
                actor_user_id=current_user.id,
                scalar_updates=scalar_updates,
                risks=risks,
                treatment_options=treatment_options,
            )
            db.session.commit()
        except (AssessmentDraftFormError, AssessmentLifecycleError) as exc:
            db.session.rollback()
            flash(str(exc), "error")
        except Exception:
            db.session.rollback()
            raise
        else:
            flash("Assessment draft saved.", "success")
            return redirect(
                url_for("admin.admin_edit_assessment", assessment_id=assessment.id)
            )

    risk = calculate_assessment_risk(assessment)
    return render_template(
        "admin/edit_assessment.html",
        assessment=assessment,
        risk=risk,
    )


@login_required
@advisor_required
def admin_finalize_assessment_cutover(assessment_id: int):
    existing_plan = TreatmentPlan.query.filter_by(
        assessment_id=assessment_id
    ).first()

    try:
        assessment = AssessmentLifecycleService.finalize(
            assessment_id=assessment_id,
            actor_user_id=current_user.id,
            source="admin.assessment_finalize",
        )
        if existing_plan is None:
            TreatmentPlanLifecycleService.canonicalize_new_assessment_plan(
                assessment_id=assessment.id,
                actor_user_id=current_user.id,
                occurred_at=assessment.finalized_at,
                source="admin.assessment_finalize",
            )
        db.session.commit()
    except AssessmentLifecycleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("admin.admin_consultations"))
    except Exception:
        db.session.rollback()
        raise

    flash("Assessment finalized. This document is now locked.", "success")
    return redirect(url_for("admin.view_vehicle", car_id=assessment.car_id))


@admin_bp.record_once
def install_assessment_lifecycle_cutover(state):
    replacements = {
        "admin.admin_start_assessment": admin_start_assessment_cutover,
        "admin.admin_edit_assessment": admin_edit_assessment_cutover,
        "admin.admin_finalize_assessment": admin_finalize_assessment_cutover,
    }

    missing = [
        endpoint for endpoint in replacements if endpoint not in state.app.view_functions
    ]
    if missing:
        raise RuntimeError(
            "Assessment lifecycle cutover could not find endpoint(s): "
            + ", ".join(sorted(missing))
        )

    state.app.view_functions.update(replacements)
