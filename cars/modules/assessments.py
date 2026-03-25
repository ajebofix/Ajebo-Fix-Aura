from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    url_for,
)
from flask_login import login_required, current_user

from models import (
    VehicleAssessment,
    CarOwnership,
)
from services.assessment_report_builder import build_assessment_report
from services.assessment_pdf_renderer import render_assessment_pdf

cars_assessments_bp = Blueprint(
    "car_assessments",
    __name__,
    url_prefix="/cars/assessments",
)


# =====================================================
# 🔒 CLIENT — DOWNLOAD ASSESSMENT PDF
# =====================================================


@cars_assessments_bp.route("/<int:assessment_id>/download", methods=["GET"])
@login_required
def client_download_assessment_pdf(assessment_id):
    assessment = VehicleAssessment.query.get_or_404(assessment_id)

    # Verify ownership
    ownership = CarOwnership.query.filter_by(
        car_id=assessment.car_id,
        user_id=current_user.id,
        is_active=True,
    ).first()

    if not ownership:
        flash("Access denied.", "error")
        return redirect(url_for("dashboard.aura_home"))

    if not assessment.is_finalized:
        flash("Assessment is not yet available for download.", "error")
        return redirect(url_for("cars.car_detail", car_id=assessment.car_id))

    report_data = build_assessment_report(assessment)
    pdf = render_assessment_pdf(report_data=report_data)

    filename = f"Vehicle_Health_Report_" f"{assessment.car.vin}.pdf"

    return Response(
        pdf.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
