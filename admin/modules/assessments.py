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

from models import CarOwnership, VehicleAssessment
from services.assessment_report_builder import build_assessment_report

assessments_bp = Blueprint(
    "admin_assessments",
    __name__,
    url_prefix="/admin/assessments",
)


# =====================================================
# 🔒 SHARED — DOWNLOAD FINALIZED ASSESSMENT REPORT
# =====================================================


@assessments_bp.route("/<int:assessment_id>/download", methods=["GET"])
@login_required
def admin_download_assessment_pdf(assessment_id):
    """Serve one finalized report under explicit advisor/owner authority.

    The shared vehicle profile currently renders this endpoint for both advisor
    and owner views. Advisors retain direct report access. Owners are authorized
    only when they hold the active ownership for the assessment vehicle.

    This compatibility route intentionally performs owner authorization itself
    instead of redirecting to the dormant ``car_assessments`` blueprint. That
    blueprint is not registered in the production application today, and a
    redirect to its endpoint caused the Wave 2.2A5 production ``BuildError``.
    """

    assessment = VehicleAssessment.query.get_or_404(assessment_id)
    is_advisor = current_user.role == "admin"

    if not is_advisor:
        ownership = CarOwnership.query.filter_by(
            car_id=assessment.car_id,
            user_id=current_user.id,
            is_active=True,
        ).first()

        if not ownership:
            flash("Access denied.", "error")
            return redirect(url_for("dashboard.aura_home"))

    if not assessment.is_finalized:
        if is_advisor:
            flash("Assessment must be finalized before download.", "error")
            return redirect(request.referrer or url_for("admin.admin_dashboard"))

        flash("Assessment is not yet available for download.", "error")
        return redirect(url_for("cars.car_detail", car_id=assessment.car_id))

    report = build_assessment_report(assessment=assessment)

    html = render_template(
        "reports/assessment_report.html",
        report=report,
        car=assessment.car,
        print_mode=True,
    )

    return Response(
        html,
        mimetype="text/html",
        headers={
            "Content-Disposition": f"inline; filename=assessment_{assessment.id}.html"
        },
    )
