from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    url_for,
    render_template,
    request,
)
from flask_login import login_required, current_user
from datetime import datetime

from models import (
    db,
    VehicleAssessment,
    CarOwnership,
)
from services.assessment_report_builder import build_assessment_report
from ..utils import advisor_required

admin_assessments_bp = Blueprint(
    "admin_assessments",
    __name__,
    url_prefix="/admin/assessments",
)


# =====================================================
# 🔒 ADMIN — DOWNLOAD ASSESSMENT PDF
# =====================================================


from flask import render_template, Response, flash, redirect, url_for, request


@admin_assessments_bp.route("/<int:assessment_id>/download", methods=["GET"])
@login_required
@advisor_required
def admin_download_assessment_pdf(assessment_id):

    print("ADMIN DOWNLOAD ROUTE HIT")

    assessment = VehicleAssessment.query.get_or_404(assessment_id)

    if not assessment.is_finalized:
        flash("Assessment must be finalized before download.", "error")
        return redirect(request.referrer or url_for("admin.admin_dashboard"))

    report = build_assessment_report(assessment)

    html = render_template(
        "reports/assessment_report.html",
        report=report,
        car=assessment.car,
        print_mode=True,
    )

    return Response(html, mimetype="text/html")
