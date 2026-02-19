from flask import (
    Blueprint,
    request,
    render_template,
    flash,
    redirect,
    url_for,
    Response,
    abort,
)
from flask_login import login_required, current_user
from datetime import datetime

from models import (
    db,
    User,
    Car,
    CarOwnership,
    VehicleEvent,
    CarFault,
    Consultation,
    VehicleAssessment,
)

# from cars import routes
from services.vehicle_intelligence import calculate_vehicle_health
from .utils import advisor_required
from services.consultation_guard import require_active_consultation
from services.assessment_report_builder import build_assessment_report
from services.assessment_pdf_renderer import render_assessment_pdf

from io import BytesIO
from xhtml2pdf import pisa
import hashlib


# =====================================================
# BLUEPRINT
# =====================================================

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

admin_assessments_bp = Blueprint(
    "admin_assessments",
    __name__,
    url_prefix="/admin/assessments",
)

# =====================================================
# CLINICAL DISCLAIMER
# =====================================================

CLINICAL_DISCLAIMER = (
    "Information shown reflects reported observations and professional monitoring. "
    "It does not represent a mechanical diagnosis. "
    "All interpretations are provided by Ajebo Fix advisors."
)


# =====================================================
# SAFE OWNER DISPLAY
# =====================================================


def get_owner_display(user):
    if not user:
        return "—"

    return (
        getattr(user, "full_name", None)
        or getattr(user, "name", None)
        or getattr(user, "email", None)
        or "—"
    )


# =====================================================
# ADMIN DASHBOARD (TEMPLATE-SAFE)
# =====================================================


@admin_bp.route("/dashboard", methods=["GET"])
@login_required
@advisor_required
def admin_dashboard():
    """
    Advisor dashboard — authoritative overview
    """

    total_events = VehicleEvent.query.count()
    archived_events = VehicleEvent.query.filter(
        VehicleEvent.is_deleted.is_(True)
    ).count()

    stats = {
        "clients": User.query.filter(User.role == "user").count(),
        "vehicles": Car.query.count(),
        "active_assignments": CarOwnership.query.filter_by(is_active=True).count(),
        "events": {
            "total": total_events,
            "active": total_events - archived_events,
            "archived": archived_events,
        },
    }

    return render_template(
        "admin/dashboard.html",
        stats=stats,
        disclaimer=CLINICAL_DISCLAIMER,
    )


# =====================================================
# ADMIN — FLEET HEALTH (STATUS NORMALIZED)
# =====================================================


@admin_bp.route("/fleet/health", methods=["GET"])
@login_required
@advisor_required
def admin_fleet_health():
    """
    Clinical fleet overview.
    Buckets are UI-safe and independent of raw engine output.
    """

    STATUS_BUCKET_MAP = {
        "healthy": "green",
        "attention": "amber",
        "critical": "red",
        "unknown": "amber",
    }

    grouped = {
        "green": [],
        "amber": [],
        "red": [],
    }

    cars = Car.query.all()

    for car in cars:
        ownership = CarOwnership.query.filter_by(
            car_id=car.id,
            is_active=True,
        ).first()

        owner_name = get_owner_display(ownership.user if ownership else None)

        active_concerns = CarFault.query.filter(
            CarFault.car_id == car.id,
            CarFault.status != "resolved",
        ).count()

        if ownership:
            health = calculate_vehicle_health(car, ownership)
        else:
            health = {
                "health_status": "critical",
                "risk_reasons": ["No active care assignment"],
                "next_action": "Assign vehicle to Ajebo Fix advisor",
            }

        raw_status = health.get("health_status", "unknown")
        bucket = STATUS_BUCKET_MAP.get(raw_status, "amber")

        grouped[bucket].append(
            {
                "id": car.id,
                "vehicle": car.display_name,
                "owner_name": owner_name,
                "current_mileage": car.current_mileage,
                "health_status": raw_status,
                "risk_reasons": health.get("risk_reasons", []),
                "next_action": health.get("next_action"),
                "active_concerns": active_concerns,
            }
        )

    summary = {
        "total": sum(len(v) for v in grouped.values()),
        "green": len(grouped["green"]),
        "amber": len(grouped["amber"]),
        "red": len(grouped["red"]),
    }

    return render_template(
        "admin/fleet_health.html",
        grouped=grouped,
        summary=summary,
        disclaimer=CLINICAL_DISCLAIMER,
    )


# =====================================================
# ✅ ADMIN — REPORTED CONCERNS (FIXED & CANONICAL)
# =====================================================


@admin_bp.route("/concerns", methods=["GET"])
@login_required
@advisor_required
def admin_reported_concerns():
    """
    Canonical admin concerns view.
    Supports optional filtering by car_id.
    """

    car_id = request.args.get("car_id", type=int)

    query = CarFault.query.filter(CarFault.status != "resolved")

    if car_id:
        query = query.filter(CarFault.car_id == car_id)

    faults = query.order_by(CarFault.created_at.desc()).all()

    car = Car.query.get(car_id) if car_id else None

    return render_template(
        "admin/concerns.html",
        faults=faults,  # ✅ MATCHES TEMPLATE
        car=car,
        disclaimer=CLINICAL_DISCLAIMER,
    )


# =====================================================
# ADMIN — FAULTS (ALIAS → CONCERNS)
# =====================================================


@admin_bp.route("/faults", methods=["GET"])
@login_required
@advisor_required
def admin_faults_alias():
    """
    Backward-compatible alias for /admin/concerns
    """
    return redirect(url_for("admin.admin_reported_concerns"))


# =====================================================
# ADMIN — VEHICLE CONCERNS
# =====================================================


@admin_bp.route("/cars/<int:car_id>/concerns", methods=["GET"])
@login_required
@advisor_required
def admin_vehicle_concerns(car_id):
    car = Car.query.get_or_404(car_id)

    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        is_active=True,
    ).first()

    concerns = (
        CarFault.query.filter_by(car_id=car.id)
        .order_by(CarFault.created_at.desc())
        .all()
    )

    active_concerns = [c for c in concerns if c.status != "resolved"]

    return render_template(
        "admin/vehicle_concerns.html",
        car=car,
        ownership=ownership,
        concerns=concerns,
        active_concerns=active_concerns,
        disclaimer=CLINICAL_DISCLAIMER,
    )


# =====================================================
# ADMIN — REVIEW CONCERN
# =====================================================


@admin_bp.route("/concerns/<int:concern_id>/review", methods=["POST"])
@login_required
@advisor_required
def admin_review_concern(concern_id):
    concern = CarFault.query.get_or_404(concern_id)

    if concern.status == "reported":
        concern.status = "acknowledged"
        db.session.commit()

        flash("Concern is now under professional review.", "success")

    return redirect(request.referrer or url_for("admin.admin_reported_concerns"))


# =====================================================
# ADMIN — RESOLVE CONCERN
# =====================================================


@admin_bp.route("/concerns/<int:concern_id>/resolve", methods=["POST"])
@login_required
@advisor_required
def admin_resolve_concern(concern_id):
    concern = CarFault.query.get_or_404(concern_id)

    if concern.status != "resolved":
        concern.status = "resolved"
        concern.resolved_at = datetime.utcnow()
        concern.resolved_by = current_user.id
        db.session.commit()

        flash("Concern has been resolved and documented.", "success")

    return redirect(request.referrer or url_for("admin.admin_reported_concerns"))


# =====================================================
# ADMIN — VEHICLE PROFILE VIEW
# =====================================================


@admin_bp.route("/cars/<int:car_id>", methods=["GET"])
@login_required
@advisor_required
def admin_view_vehicle(car_id):
    car = Car.query.get_or_404(car_id)

    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        is_active=True,
    ).first()

    health = (
        calculate_vehicle_health(car, ownership)
        if ownership
        else {
            "health_status": "critical",
            "risk_reasons": ["Vehicle not under active care"],
            "next_action": "Assign to Ajebo Fix advisor",
        }
    )

    return render_template(
        "car_detail.html",
        car=car,
        ownership=ownership,
        health=health,
        disclaimer=CLINICAL_DISCLAIMER,
        is_admin_view=True,
    )


# =====================================================
# ADMIN — ADD SERVICE (ON BEHALF OF CLIENT)
# =====================================================


@admin_bp.route("/cars/<int:car_id>/service/add", methods=["GET", "POST"])
@login_required
@advisor_required
def admin_add_service(car_id):
    car = Car.query.get_or_404(car_id)
    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        is_active=True,
    ).first_or_404()

    # 🔒 AUTHORITY GATE
    try:
        require_active_consultation(car_id)
    except PermissionError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.admin_view_vehicle", car_id=car.id))

    if request.method == "POST":
        service_type = request.form.get("service_type", "").strip()
        mileage = request.form.get("mileage", type=int)
        description = request.form.get("description", "").strip()
        service_date = request.form.get("service_date")

        if not service_type or mileage is None or not service_date:
            flash("All required fields must be completed.", "error")
            return redirect(request.referrer)

        if mileage < (car.current_mileage or 0):
            flash("Mileage cannot be lower than current vehicle mileage.", "error")
            return redirect(request.referrer)

        fingerprint = hashlib.sha256(
            f"{car.id}|{ownership.id}|{service_type}|{mileage}|{service_date}|admin".encode()
        ).hexdigest()

        if VehicleEvent.query.filter_by(fingerprint=fingerprint).first():
            flash("Duplicate service record detected.", "error")
            return redirect(request.referrer)

        car.current_mileage = mileage

        event = VehicleEvent(
            car_id=car.id,
            ownership_id=ownership.id,
            event_type="service",
            title=service_type,
            description=description,
            mileage=mileage,
            source="admin",
            fingerprint=fingerprint,
            created_by=current_user.id,
            created_at=datetime.fromisoformat(service_date),
            is_deleted=False,
        )

        db.session.add(event)
        db.session.commit()

        flash("Service record added on behalf of client.", "success")
        return redirect(url_for("admin.admin_vehicle_records", car_id=car.id))

    return render_template(
        "admin/add_service.html",
        car=car,
        ownership=ownership,
        disclaimer=CLINICAL_DISCLAIMER,
    )


# =====================================================
# ADMIN — PDF EXPORT (CONFIDENTIAL)
# =====================================================


@admin_bp.route("/cars/<int:car_id>/records/pdf", methods=["GET"])
@login_required
@advisor_required
def admin_vehicle_records_pdf(car_id):
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        is_active=True,
    ).first_or_404()

    try:
        require_active_consultation(car_id)
    except PermissionError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.admin_vehicle_records", car_id=car_id))

    car = ownership.car
    services = VehicleEvent.query.filter_by(
        car_id=car.id,
        ownership_id=ownership.id,
        is_deleted=False,
    ).all()

    concerns = CarFault.query.filter_by(car_id=car.id).all()
    health = calculate_vehicle_health(car, ownership)

    html = render_template(
        "reports/timeline.html",
        car=car,
        ownership=ownership,
        services=services,
        concerns=concerns,
        health=health,
        is_admin_view=True,
        is_pdf_export=True,
        exported_by=current_user.email,
        exported_at=datetime.utcnow(),
        disclaimer=CLINICAL_DISCLAIMER,
    )

    pdf_buffer = BytesIO()
    pisa.CreatePDF(html, dest=pdf_buffer, encoding="UTF-8")
    pdf_buffer.seek(0)

    return Response(
        pdf_buffer.read(),
        mimetype="application/pdf",
        headers={
            "Content-Disposition": (
                f"attachment; filename=AJF_MEDICAL_FILE_{car.vin}.pdf"
            )
        },
    )


# =====================================================
# ADMIN — ADD CONCERN / OBSERVATION
# =====================================================


@admin_bp.route("/cars/<int:car_id>/concerns/add", methods=["GET", "POST"])
@login_required
@advisor_required
def admin_add_concern(car_id):
    car = Car.query.get_or_404(car_id)
    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        is_active=True,
    ).first_or_404()

    # 🔒 AUTHORITY GATE
    try:
        require_active_consultation(car_id)
    except PermissionError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.admin_view_vehicle", car_id=car.id))

    if request.method == "POST":
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "other")
        observed_at_raw = request.form.get("observed_at")

        if not description:
            flash("Observation description is required.", "error")
            return redirect(request.referrer)

        observed_at = (
            datetime.fromisoformat(observed_at_raw) if observed_at_raw else None
        )

        concern = CarFault(
            car_id=car.id,
            title="Advisor observation",
            category=category,
            description=description,
            status="under_review",
            observed_at=observed_at,
            reported_by=current_user.id,
            reported_at=datetime.utcnow(),
            source="admin",
        )

        db.session.add(concern)
        db.session.commit()

        flash("Observation recorded.", "success")
        return redirect(url_for("admin.admin_vehicle_records", car_id=car.id))

    return render_template(
        "admin/add_concern.html",
        car=car,
        ownership=ownership,
        disclaimer=CLINICAL_DISCLAIMER,
    )


# ======================================================
# ADMIN - VEHICLE MEDICAL FILE / CARE HISTORY
# ======================================================


@admin_bp.route("/cars/<int:car_id>/records", methods=["GET"])
@login_required
@advisor_required
def admin_vehicle_records(car_id):
    """
    ADMIN VIEW — Vehicle Medical File (Read-only)
    Full care timeline
    Services + reported concerns
    Health snapshot
    No mutations
    """

    # ---------------------------------------------------
    # Resolve ACTIVE ownership (authoritative)
    # -------------------------------------------------------
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        is_active=True,
    ).first_or_404()

    car = ownership.car

    # ---------------------------------------
    # Service / treatment history
    # ---------------------------------------
    services = (
        VehicleEvent.query.filter_by(
            car_id=car.id,
            ownership_id=ownership.id,
            is_deleted=False,
        )
        .order_by(VehicleEvent.created_at.desc())
        .all()
    )

    # -----------------------------------------
    # Reported concerns (observational)
    # -----------------------------------------
    concerns = (
        CarFault.query.filter_by(car_id=car.id)
        .order_by(CarFault.created_at.desc())
        .all()
    )

    # ----------------------------------------------
    # Health snapshot (interpreted, non-diagnostic)
    # ------------------------------------------------
    health = calculate_vehicle_health(car, ownership)

    # ------------------------------------------------
    # Render shared Medical File UI
    # -------------------------------------------------
    return render_template(
        "reports/timeline.html",
        car=car,
        ownership=ownership,
        services=services,
        concerns=concerns,
        health=health,
        is_admin_view=True,  # 🔑 important
    )


# ===========================================
# ADMIN SCHEDULE CONSULTATION
# ===========================================


@admin_bp.route("/cars/<int:car_id>/consultations/schedule", methods=["GET", "POST"])
@login_required
@advisor_required
def admin_schedule_consultation(car_id):
    car = Car.query.get_or_404(car_id)

    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        is_active=True,
    ).first_or_404()

    if request.method == "POST":
        scheduled_for_raw = request.form.get("scheduled_for")
        if not scheduled_for_raw:
            flash("Scheduled date is required.", "error")
            return redirect(request.referrer)

        consultation = Consultation(
            car_id=car.id,
            ownership_id=ownership.id,
            advisor_id=current_user.id,
            client_id=ownership.user_id,
            scheduled_for=datetime.fromisoformat(scheduled_for_raw),
            status="scheduled",
        )

        db.session.add(consultation)
        db.session.commit()

        flash("Consultation scheduled.", "success")
        return redirect(url_for("admin.admin_consultations"))

    return render_template(
        "admin/schedule_consultation.html",
        car=car,
        ownership=ownership,
    )


# ===========================================
# BEGIN CONSULTATION (authority unlock)
# ===========================================


@admin_bp.route("/consultations/<int:consultation_id>/start", methods=["POST"])
@login_required
@advisor_required
def admin_start_consultation(consultation_id):
    consultation = Consultation.query.get_or_404(consultation_id)

    if consultation.status != "scheduled":
        flash("Consultation cannot be started.", "error")
        return redirect(request.referrer)

    consultation.status = "in_progress"
    consultation.started_at = datetime.utcnow()

    # Create assessment if it doesn't exist
    existing_assessment = VehicleAssessment.query.filter_by(
        consultation_id=consultation.id
    ).first()

    if not existing_assessment:
        assessment = VehicleAssessment(
            consultation_id=consultation.id,
            car_id=consultation.car_id,
            ownership_id=consultation.ownership_id,
            advisor_id=current_user.id,
            vin=consultation.car.vin,
            mileage_at_assessment=consultation.car.mileage,
            engine_number=consultation.car.engine_number,
            engine_type=consultation.car.engine_type,
            status="draft",
        )
    db.session.add(assessment)

    db.session.commit()

    flash("Consultation is now in progress.", "success")
    return redirect(url_for("admin.admin_vehicle_records", car_id=consultation.car_id))


# ============================================
# COMPLETE CONSULTATION
# ============================================


@admin_bp.route(
    "/consultations/<int:consultation_id>/complete", methods=["GET", "POST"]
)
@login_required
@advisor_required
def admin_complete_consultation(consultation_id):
    consultation = Consultation.query.get_or_404(consultation_id)

    if consultation.status != "in_progress":
        flash("Only active consultations can be completed.", "error")
        return redirect(url_for("admin.admin_consultations"))

    if request.method == "POST":
        consultation.status = "completed"
        consultation.completed_at = datetime.utcnow()
        consultation.summary = request.form.get("summary", "").strip()
        consultation.client_visible_summary = request.form.get(
            "client_visible_summary", ""
        ).strip()

        db.session.commit()

        flash("Consultation completed and documented.", "success")
        return redirect(url_for("admin.admin_view_vehicle", car_id=consultation.car_id))

    return render_template(
        "admin/complete_consultation.html",
        consultation=consultation,
    )


# ===========================================
# ADMIN — CONSULTATION QUEUE (AUTHORITY GATE)
# ===========================================


@admin_bp.route("/consultations", methods=["GET"])
@login_required
@advisor_required
def admin_consultations():
    """
    Advisor consultation queue.

    Shows all consultations grouped by status:
    - scheduled
    - in_progress
    - completed

    This is the authority gate for all vehicle care actions.
    """

    # --------------------------------------------------
    # Fetch consultations (latest first)
    # --------------------------------------------------
    consultations = Consultation.query.order_by(Consultation.created_at.desc()).all()

    # --------------------------------------------------
    # Canonical grouping (DO NOT change keys)
    # --------------------------------------------------
    grouped = {
        "scheduled": [],
        "in_progress": [],
        "completed": [],
    }

    # --------------------------------------------------
    # Defensive grouping (protects against bad data)
    # --------------------------------------------------
    for c in consultations:
        if c.status in grouped:
            grouped[c.status].append(c)
        else:
            # Unknown status → quarantine safely
            grouped["scheduled"].append(c)

    # --------------------------------------------------
    # Render advisor console
    # --------------------------------------------------
    return render_template(
        "admin/consultations.html",
        grouped=grouped,
        disclaimer=CLINICAL_DISCLAIMER,
    )


# ============================================
# ASSESSMENT EDIT ROUTE (DRAFT ONLY)
# ============================================
@admin_bp.route("/assessments/<int:assessment_id>/edit", methods=["GET", "POST"])
@login_required
@advisor_required
def admin_edit_assessment(assessment_id):
    assessment = VehicleAssessment.query.get_or_404(assessment_id)

    if assessment.status != "draft":
        abort(403)

    if request.method == "POST":
        assessment.vehicle_overview = request.form.get("vehicle_overview")
        assessment.current_health_status = request.form.get("current_health_status")
        assessment.identified_risks = request.form.get("identified_risks")
        assessment.urgency_classification = request.form.get("urgency_classification")
        assessment.cost_vs_consequence = request.form.get("cost_vs_consequence")
        assessment.treatment_options = request.form.get("treatment_options")
        assessment.professional_recommendation = request.form.get(
            "professional_recommendation"
        )

        db.session.commit()
        flash("Assessment saved.", "success")

    return render_template(
        "admin/edit_assessment.html",
        assessment=assessment,
    )


# =============================================
# FINALIZE ASSESSMENT ROUTE (POINT OF NO RETURN)
# =============================================


@admin_bp.route("/assessments/<int:assessment_id>/finalize", methods=["POST"])
@login_required
@advisor_required
def admin_finalize_assessment(assessment_id):
    assessment = VehicleAssessment.query.get_or_404(assessment_id)

    if assessment.status != "draft":
        abort(403)

    assessment.status = "finalized"
    assessment.finalized_at = datetime.utcnow()

    db.session.commit()

    flash("Assessment finalized. This document is now locked.", "success")
    return redirect(url_for("admin.admin_view_vehicle", car_id=assessment.car_id))


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
