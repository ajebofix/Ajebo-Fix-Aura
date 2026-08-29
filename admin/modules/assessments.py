import re

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
from services.assessment_report_pdf import build_assessment_pdf

assessments_bp = Blueprint(
    "admin_assessments",
    __name__,
    url_prefix="/admin/assessments",
)


# =====================================================
# SHARED — FINALIZED ASSESSMENT REPORT AUTHORITY
# =====================================================


def _authorized_finalized_assessment(assessment_id):
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
            return None, redirect(url_for("dashboard.aura_home"))

    if not assessment.is_finalized:
        if is_advisor:
            flash("Assessment must be finalized before the report is available.", "error")
            return None, redirect(
                request.referrer or url_for("admin.admin_dashboard")
            )

        flash("Assessment is not yet available.", "error")
        return None, redirect(url_for("cars.car_detail", car_id=assessment.car_id))

    return assessment, None


# =====================================================
# SHARED — CANONICAL FINALIZED ASSESSMENT REPORT
# =====================================================


@login_required
def assessment_report(assessment_id):
    """Serve one finalized HTML report under advisor/active-owner authority."""

    assessment, denied_response = _authorized_finalized_assessment(assessment_id)
    if denied_response is not None:
        return denied_response

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
            "Content-Disposition": f"inline; filename=assessment_{assessment.id}.html",
            "Cache-Control": "private, no-store",
        },
    )


@login_required
def assessment_report_pdf(assessment_id):
    """Download the same owner-safe finalized professional record as a PDF."""

    assessment, denied_response = _authorized_finalized_assessment(assessment_id)
    if denied_response is not None:
        return denied_response

    report = build_assessment_report(assessment=assessment)
    pdf_bytes = build_assessment_pdf(report=report)

    raw_identifier = assessment.car.vin or f"assessment_{assessment.id}"
    safe_identifier = re.sub(r"[^A-Za-z0-9_-]+", "_", raw_identifier).strip("_")
    filename = f"Ajebo_Fix_Vehicle_Health_Risk_Report_{safe_identifier}.pdf"

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@assessments_bp.record_once
def register_canonical_assessment_report(state):
    """Register role-neutral HTML and PDF report URLs."""

    state.app.add_url_rule(
        "/assessments/<int:assessment_id>/report",
        endpoint="assessment_reports.assessment_report",
        view_func=assessment_report,
        methods=["GET"],
    )
    state.app.add_url_rule(
        "/assessments/<int:assessment_id>/report.pdf",
        endpoint="assessment_reports.assessment_report_pdf",
        view_func=assessment_report_pdf,
        methods=["GET"],
    )


# =====================================================
# LEGACY — ADMIN-PREFIXED REPORT URL
# =====================================================


@assessments_bp.route("/<int:assessment_id>/download", methods=["GET"])
@login_required
def admin_download_assessment_pdf(assessment_id):
    """Redirect old/bookmarked report URLs to the canonical neutral HTML route."""

    return redirect(
        url_for(
            "assessment_reports.assessment_report",
            assessment_id=assessment_id,
        ),
        code=302,
    )
