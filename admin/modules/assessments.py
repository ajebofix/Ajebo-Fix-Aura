from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    url_for,
)
from flask_login import login_required, current_user
from datetime import datetime

from models import (
    db,
    VehicleAssessment,
    CarOwnership,
)
from services.assessment_report_builder import build_assessment_report
from services.assessment_pdf_renderer import render_assessment_pdf
from ..utils import advisor_required

admin_assessments_bp = Blueprint(
    "admin_assessments",
    __name__,
    url_prefix="/admin/assessments",
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
