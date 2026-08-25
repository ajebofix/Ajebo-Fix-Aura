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
# SHARED — CANONICAL FINALIZED ASSESSMENT REPORT
# =====================================================


@login_required
def assessment_report(assessment_id):
    """Serve one finalized report under explicit advisor/owner authority.

    The report is a vehicle professional-record resource rather than an admin
    resource, so its canonical URL is role-neutral. Advisors retain direct
    access. Owners are authorized only when they hold the active ownership for
    the assessment vehicle.
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


@assessments_bp.record_once
def register_canonical_assessment_report(state):
    """Register the role-neutral report URL when the legacy blueprint mounts."""

    state.app.add_url_rule(
        "/assessments/<int:assessment_id>/report",
        endpoint="assessment_reports.assessment_report",
        view_func=assessment_report,
        methods=["GET"],
    )


# =====================================================
# LEGACY — ADMIN-PREFIXED REPORT URL
# =====================================================


@assessments_bp.route("/<int:assessment_id>/download", methods=["GET"])
@login_required
def admin_download_assessment_pdf(assessment_id):
    """Redirect old/bookmarked report URLs to the canonical neutral route."""

    return redirect(
        url_for(
            "assessment_reports.assessment_report",
            assessment_id=assessment_id,
        ),
        code=302,
    )
