from datetime import datetime

from flask import (
    Blueprint,
    redirect,
    url_for,
    flash,
    request,
    Response,
)
from flask_login import login_required, current_user

from app import db
from models import (
    VehicleAssessment,
    Consultation,
    CarOwnership,
)
from .utils import advisor_required
from services.assessment_report_builder import build_assessment_report
from services.assessment_pdf_renderer import render_assessment_pdf

# =====================================================
# BLUEPRINT
# =====================================================

admin_assessments_bp = Blueprint(
    "admin_assessments",
    __name__,
    url_prefix="/admin/assessments",
)

# =====================================================
# FINALIZE VEHICLE ASSESSMENT (AUTHORITY LOCK)
# =====================================================


@admin_assessments_bp.route(
    "/<int:assessment_id>/finalize",
    methods=["POST"],
)
@login_required
@advisor_required
def finalize_vehicle_assessment(assessment_id):
    """
    Finalizes a vehicle assessment.

    RULES (NON-NEGOTIABLE):
    - Assessment must exist
    - Assessment must NOT already be finalized
    - Related consultation MUST be completed
    - Once finalized, assessment becomes immutable
    """

    assessment = VehicleAssessment.query.get_or_404(assessment_id)

    # -------------------------------------------------
    # Guard 1: Already finalized
    # -------------------------------------------------
    if assessment.is_finalized:
        flash(
            "This assessment has already been finalized and cannot be modified.",
            "error",
        )
        return redirect(request.referrer or url_for("admin.admin_consultations"))

    # -------------------------------------------------
    # Guard 2: Consultation must be completed
    # -------------------------------------------------
    consultation = Consultation.query.get(assessment.consultation_id)

    if not consultation or consultation.status != "completed":
        flash(
            "Consultation must be completed before finalizing the assessment.",
            "error",
        )
        return redirect(request.referrer or url_for("admin.admin_consultations"))

    # -------------------------------------------------
    # Finalize assessment (authority signature)
    # -------------------------------------------------
    assessment.is_finalized = True
    assessment.finalized_at = datetime.utcnow()
    assessment.finalized_by = current_user.id

    db.session.commit()

    flash(
        "Vehicle assessment finalized. Professional report is now authoritative.",
        "success",
    )

    # -------------------------------------------------
    # Next step: report generation (handled elsewhere)
    # -------------------------------------------------
    return redirect(
        url_for(
            "admin.admin_view_vehicle",
            car_id=assessment.car_id,
        )
    )


# =====================================================
# 🔒 ADMIN — DOWNLOAD ASSESSMENT PDF
# =====================================================


@admin_assessments_bp.route("/<int:assessment_id>/download", methods=["GET"])
@login_required
@advisor_required
def admin_download_assessment_pdf(assessment_id):
    assessment = VehicleAssessment.query.get_or_404(assessment_id)

    if not assessment.is_finalized:
        flash("Assessment must be finalized before download.", "error")
        return redirect(request.referrer)

    # Build authoritative report data
    report_data = build_assessment_report(assessment)

    # Render PDF
    pdf = render_assessment_pdf(report_data=report_data)

    filename = (
        f"AJF_VEHICLE_ASSESSMENT_"
        f"{assessment.car.vin}_"
        f"{assessment.created_at.date()}.pdf"
    )

    return Response(
        pdf.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
