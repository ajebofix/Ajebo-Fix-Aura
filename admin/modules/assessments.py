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

from models import VehicleAssessment
from services.assessment_report_builder import build_assessment_report

assessments_bp = Blueprint(
    "admin_assessments",
    __name__,
    url_prefix="/admin/assessments",
)


# =====================================================
# 🔒 ADMIN — DOWNLOAD ASSESSMENT REPORT
# =====================================================


@assessments_bp.route("/<int:assessment_id>/download", methods=["GET"])
@login_required
def admin_download_assessment_pdf(assessment_id):
    """Serve advisor reports and safely hand owner requests to the client route.

    The shared vehicle profile currently renders the admin download endpoint for
    both advisor and owner views. Non-advisors must never inherit advisor
    authority here, so owner requests are redirected to the canonical client
    endpoint where active ownership and finalized-state checks are enforced.
    """

    if current_user.role != "admin":
        return redirect(
            url_for(
                "car_assessments.client_download_assessment_pdf",
                assessment_id=assessment_id,
            )
        )

    assessment = VehicleAssessment.query.get_or_404(assessment_id)

    if not assessment.is_finalized:
        flash("Assessment must be finalized before download.", "error")
        return redirect(request.referrer or url_for("admin.admin_dashboard"))

    report = build_assessment_report(assessment=assessment)

    html = render_template(
        "reports/assessment_report.html",
        report=report,
        car=assessment.car,
        print_mode=True,
    )

    return Response(html, mimetype="text/html")
