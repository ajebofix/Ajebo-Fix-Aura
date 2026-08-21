"""Production-safe draft assessment persistence adapter.

This compatibility cutover replaces the legacy ``admin.admin_edit_assessment``
view at blueprint registration time. The existing template currently submits
repeating child fields with ``[]`` suffixes, while the legacy route reads the
unsuffixed names. That mismatch caused a successful Save Draft POST to delete
existing risks/treatment options and recreate none of them.

This adapter accepts both field-name shapes, validates repeating groups before
replacement, preserves fields that are absent from a submission, and uses
Post/Redirect/Get after a successful save so the next page render proves what
was actually persisted.
"""

from __future__ import annotations

from collections.abc import Sequence

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from admin.routes import admin_bp
from admin.utils import advisor_required
from extensions import db
from models import (
    VehicleAssessment,
    VehicleAssessmentRisk,
    VehicleAssessmentTreatmentOption,
)
from services.assessment_risk_engine import calculate_assessment_risk


class AssessmentDraftFormError(ValueError):
    """Raised when a repeating assessment form group is malformed."""


def _submitted_list(name: str) -> list[str] | None:
    """Return a repeated field using canonical or legacy ``[]`` keys."""

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
    """Validate an optional repeating group before destructive replacement."""

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


@login_required
@advisor_required
def admin_edit_assessment_cutover(assessment_id: int):
    assessment = VehicleAssessment.query.get_or_404(assessment_id)

    if assessment.status != "draft":
        abort(403)

    if request.method == "POST":
        risk_descriptions = _submitted_list("risk_description")
        risk_causes = _submitted_list("risk_cause")
        risk_consequences = _submitted_list("risk_consequence")
        risk_urgencies = _submitted_list("risk_urgency")

        treatment_titles = _submitted_list("treatment_title")
        treatment_descriptions = _submitted_list("treatment_description")
        treatment_codes = _submitted_list("treatment_code")

        try:
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

            if replace_risks:
                VehicleAssessmentRisk.query.filter_by(
                    assessment_id=assessment.id
                ).delete(synchronize_session=False)

                for description, cause, consequence, urgency in zip(
                    risk_descriptions or [],
                    risk_causes or [],
                    risk_consequences or [],
                    risk_urgencies or [],
                    strict=True,
                ):
                    clean_description = (description or "").strip()
                    if not clean_description:
                        continue

                    db.session.add(
                        VehicleAssessmentRisk(
                            assessment_id=assessment.id,
                            description=clean_description,
                            likely_cause=(cause or "").strip(),
                            consequence_if_ignored=(consequence or "").strip(),
                            urgency=(urgency or "").strip() or "monitoring",
                        )
                    )

            if replace_treatments:
                VehicleAssessmentTreatmentOption.query.filter_by(
                    assessment_id=assessment.id
                ).delete(synchronize_session=False)

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
                    if (
                        not clean_title
                        or not clean_description
                        or clean_code not in {"A", "B", "C"}
                    ):
                        raise AssessmentDraftFormError(
                            "Each treatment option must have a valid A/B/C code, title and description. Existing data was preserved."
                        )

                    db.session.add(
                        VehicleAssessmentTreatmentOption(
                            assessment_id=assessment.id,
                            option_code=clean_code,
                            title=clean_title,
                            description=clean_description,
                        )
                    )

            scalar_fields = (
                "engine_status",
                "transmission_status",
                "suspension_status",
                "electrical_status",
                "cooling_status",
                "cost_vs_consequence_analysis",
                "professional_recommendation",
            )

            for form_name in scalar_fields:
                if form_name not in request.form:
                    continue

                model_name = (
                    "cost_consequence_analysis"
                    if form_name == "cost_vs_consequence_analysis"
                    else form_name
                )
                setattr(assessment, model_name, request.form.get(form_name))

            db.session.commit()
        except AssessmentDraftFormError as exc:
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


@admin_bp.record_once
def install_assessment_draft_cutover(state):
    endpoint = "admin.admin_edit_assessment"
    if endpoint not in state.app.view_functions:
        raise RuntimeError(
            "Assessment draft cutover could not find endpoint: " + endpoint
        )

    state.app.view_functions[endpoint] = admin_edit_assessment_cutover
