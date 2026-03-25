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
    VehicleAssessmentTreatmentOption,
    db,
    User,
    Car,
    CarOwnership,
    VehicleEvent,
    CarFault,
    Consultation,
    VehicleAssessment,
    VehicleAssessmentRisk,
)

from services.vehicle_intelligence import calculate_vehicle_health
from .utils import advisor_required
from services.consultation_guard import require_active_consultation
from services.assessment_report_builder import build_assessment_report
from services.assessment_pdf_renderer import render_assessment_pdf
from services.assessment_risk_engine import calculate_assessment_risk
from services.rina_escalation_engine import RinaEscalationEngine
from services.rina_action_suggestions import RinaCareGuidanceEngine
from services.rina_alert_awareness_service import RinaCareContextService

from io import BytesIO
from xhtml2pdf import pisa
import hashlib
from sqlalchemy import func


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
        concern.status = "under_review"
        concern.reviewed_at = datetime.utcnow()
        concern.reviewed_by = current_user.id

        db.session.commit()

        flash("Concern is now under professional review.", "success")

    return redirect(request.referrer or url_for("admin.admin_reported_concerns"))


# ===================================
# ADMIN - MONITOR CONCERN
# ===================================


@admin_bp.route("/concerns/<int:concern_id>/monitor", methods=["POST"])
@login_required
@advisor_required
def admin_monitor_concern(concern_id):
    concern = CarFault.query.get_or_404(concern_id)

    if concern.status in ("reported", "under_review"):
        concern.status = "monitoring"
        db.session.commit()

        flash("Concern moved to monitoring.", "success")

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

        flash("Concern resolved.", "success")

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

    guidance = RinaCareGuidanceEngine.generate_guidance(car.id, ownership.user_id)

    care_context = RinaCareContextService.get_active_care_context(car.id, ownership.user_id)

    escalation = RinaEscalationEngine.evaluate(health, guidance, care_context)

    consultations = Consultation.query.filter_by(car_id=car.id).all()

    assessments = VehicleAssessment.query.filter_by(car_id=car.id).all()

    has_active_consultation = any(c.status == "in_progress" for c in consultations)

    return render_template(
        "car_detail.html",
        car=car,
        ownership=ownership,
        health=health,
        guidance=guidance,
        care_context=care_context,
        escalation=escalation,
        consultations=consultations,
        assessments=assessments,
        has_active_consultation=has_active_consultation,
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

    # AUTHORITY GATE
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

        flash("Service record added.", "success")
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

    # AUTHORITY GATE
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

    # AUTHORITY GATE
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
    """
    Begin a scheduled consultation.
    This route ONLY transitions the consultation to 'in_progress'.
    It intentionally does NOT create a VehicleAssessment.
    """

    consultation = Consultation.query.get_or_404(consultation_id)

    # ------------------------------------------------
    # Guard: Only scheduled consultations can start
    # ------------------------------------------------
    if consultation.status != "scheduled":
        flash("Consultation cannot be started.", "error")
        return redirect(url_for("admin.admin_consultations"))

    # ------------------------------------------------
    # Transition state
    # ------------------------------------------------
    consultation.status = "in_progress"
    consultation.started_at = datetime.utcnow()

    # ------------------------------------------------
    # Persist safely
    # ------------------------------------------------
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Failed to start consultation due to a system error.", "error")
        return redirect(url_for("admin.admin_consultations"))

    flash("Consultation is now in progress.", "success")
    return redirect(url_for("admin.admin_consultations"))


# ============================================
# COMPLETE CONSULTATION
# ============================================


@admin_bp.route(
    "/consultations/<int:consultation_id>/complete", methods=["GET", "POST"]
)
@login_required
@advisor_required
def admin_complete_consultation(consultation_id):
    """
    Complete a consultation.
    Requires:
      - consultation.status == "in_progress"
      - a VehicleAssessment exists for this consultation and is finalized
    If request.method == GET -> render the completion form.
    If POST -> persist summary + complete consultation.
    """
    consultation = Consultation.query.get_or_404(consultation_id)

    if consultation.status != "in_progress":
        flash("Only active consultations can be completed.", "error")
        return redirect(url_for("admin.admin_consultations"))

    # Find the assessment (there should be at most one because of unique constraint)
    assessment = VehicleAssessment.query.filter_by(
        consultation_id=consultation.id
    ).first()

    # If GET: render form (same behavior as you had before)
    if request.method == "GET":
        return render_template(
            "admin/complete_consultation.html",
            consultation=consultation,
            assessment=assessment,
        )

    # POST: validate assessment finalized state
    # Use both status and is_finalized for safety
    if not assessment:
        flash(
            "Cannot complete consultation: no assessment exists. Start and finalize an assessment first.",
            "error",
        )
        return redirect(url_for("admin.admin_consultations"))

    if not (
        assessment.is_finalized
        or (assessment.status and assessment.status == "finalized")
    ):
        flash(
            "Cannot complete consultation: the assessment must be finalized first.",
            "error",
        )
        return redirect(url_for("admin.admin_consultations"))

    # Persist completion data
    consultation.status = "completed"
    consultation.completed_at = datetime.utcnow()
    consultation.summary = (
        request.form.get("summary", "").strip() or consultation.summary
    )
    consultation.client_visible_summary = (
        request.form.get("client_visible_summary", "").strip()
        or consultation.client_visible_summary
    )

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash("Failed to complete consultation (database error).", "error")
        return redirect(url_for("admin.admin_consultations"))

    flash("Consultation completed.", "success")
    return redirect(url_for("admin.admin_view_vehicle", car_id=consultation.car_id))


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
    consultations = Consultation.query.order_by(Consultation.scheduled_for.asc()).all()

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


# ==========================================
# ADMIN - START ASSESSMENT ROUTE
# ==========================================


@admin_bp.route(
    "/consultations/<int:consultation_id>/assessment/start", methods=["POST"]
)
@login_required
@advisor_required
def admin_start_assessment(consultation_id):

    consultation = Consultation.query.get_or_404(consultation_id)

    if consultation.status != "in_progress":
        flash("Consultation must be active first.", "error")
        return redirect(url_for("admin.admin_consultations"))

    assessment = VehicleAssessment.query.filter_by(
        consultation_id=consultation.id
    ).first()

    # ----------------------------------
    # If assessment exists
    # ----------------------------------
    if assessment:

        if assessment.status == "draft":
            flash("Continuing draft assessment.", "info")
            return redirect(
                url_for("admin.admin_edit_assessment", assessment_id=assessment.id)
            )

        if assessment.status == "finalized":
            flash("Assessment already finalized.", "info")
            return redirect(
                url_for("admin.admin_view_vehicle", car_id=consultation.car_id)
            )

    # ----------------------------------
    # Create new draft assessment
    # ----------------------------------

    assessment = VehicleAssessment(
        consultation_id=consultation.id,
        car_id=consultation.car_id,
        advisor_id=current_user.id,
        vin=consultation.car.vin,
        mileage_at_assessment=consultation.car.current_mileage,
        engine_number=consultation.car.engine_number,
        engine_type=consultation.car.engine_type,
        status="draft",
    )

    db.session.add(assessment)
    db.session.commit()

    flash("Assessment started.", "success")

    return redirect(url_for("admin.admin_edit_assessment", assessment_id=assessment.id))


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

        # -----------------------------------------------
        # SAVE RISKS
        # -----------------------------------------------
        VehicleAssessmentRisk.query.filter_by(assessment_id=assessment.id).delete()

        descriptions = request.form.getlist("risk_description")
        causes = request.form.getlist("risk_cause")
        consequences = request.form.getlist("risk_consequence")
        urgencies = request.form.getlist("risk_urgency")

        for description, cause, consequence, urgency in zip(
            descriptions, causes, consequences, urgencies
        ):

            if description:

                risk = VehicleAssessmentRisk(
                    assessment_id=assessment.id,
                    description=description,
                    likely_cause=cause,
                    consequence_if_ignored=consequence,
                    urgency=urgency,
                )

                db.session.add(risk)

        # -------------------------------
        # SYSTEM STATUS (for risk engine)
        # -------------------------------
        assessment.engine_status = request.form.get("engine_status")
        assessment.transmission_status = request.form.get("transmission_status")
        assessment.suspension_status = request.form.get("suspension_status")
        assessment.electrical_status = request.form.get("electrical_status")
        assessment.cooling_status = request.form.get("cooling_status")

        # -------------------------------
        # COST VS CONSEQUENCE
        # -------------------------------

        assessment.cost_consequence_analysis = request.form.get(
            "cost_vs_consequence_analysis"
        )

        # -------------------------------
        # TREATMENT OPTIONS (supports A/B/C)
        # -------------------------------

        VehicleAssessmentTreatmentOption.query.filter_by(
            assessment_id=assessment.id
        ).delete()

        titles = request.form.getlist("treatment_title")
        descriptions = request.form.getlist("treatment_description")
        codes = request.form.getlist("treatment_code")

        for i in range(len(titles)):

            if titles[i] and descriptions[i]:

                option = VehicleAssessmentTreatmentOption(
                    assessment_id=assessment.id,
                    option_code=codes[i],
                    title=titles[i],
                    description=descriptions[i],
                )

                db.session.add(option)

        # -------------------------------
        # PROFESSIONAL RECOMMENDATION
        # -------------------------------
        assessment.professional_recommendation = request.form.get(
            "professional_recommendation"
        )

        db.session.commit()

        flash("Assessment saved.", "success")

    # -------------------------------
    # Calculate risk after loading
    # -------------------------------
    risk = calculate_assessment_risk(assessment)

    return render_template(
        "admin/edit_assessment.html",
        assessment=assessment,
        risk=risk,
    )


# =============================================
# FINALIZE ASSESSMENT ROUTE (POINT OF NO RETURN)
# =============================================


@admin_bp.route("/assessments/<int:assessment_id>/finalize", methods=["POST"])
@login_required
@advisor_required
def admin_finalize_assessment(assessment_id):
    assessment = VehicleAssessment.query.get_or_404(assessment_id)

    if not assessment.engine_status:
        flash("Engine status is required before finalizing.", "error")
        return redirect(url_for("admin.admin_consultations"))

    if assessment.status != "draft":
        abort(403)

    if not all(
        [
            assessment.engine_status,
            assessment.transmission_status,
            assessment.suspension_status,
            assessment.electrical_status,
            assessment.cooling_status,
        ]
    ):
        flash("All system statuses are required before finalizing.", "error")
        return redirect(url_for("admin.admin_consultations"))

    assessment.status = "finalized"
    assessment.is_finalized = True
    assessment.finalized_at = datetime.utcnow()
    assessment.finalized_by = current_user.id

    db.session.commit()

    flash("Assessment finalized. This document is now locked.", "success")
    return redirect(url_for("admin.admin_view_vehicle", car_id=assessment.car_id))


# ===========================================
# ADVISOR CONTROL PANEL ROUTE
# ===========================================
@admin_bp.route("/control", methods=["GET"])
@login_required
@advisor_required
def advisor_control_panel():

    today = datetime.utcnow().date()

    # consultations today
    consultations_today = Consultation.query.filter(
        func.date(Consultation.scheduled_for) == today
    ).all()

    # active consultations
    active_consultations = Consultation.query.filter_by(status="in_progress").all()

    # draft assessments
    draft_assessments = (
        VehicleAssessment.query.filter_by(status="draft")
        .order_by(VehicleAssessment.created_at.asc())
        .limit(10)
        .all()
    )

    # vehicles needing attention
    cars = Car.query.join(CarOwnership).filter(CarOwnership.is_active == (True)).all()

    vehicles_at_risk = []

    for car in cars:

        ownership = CarOwnership.query.filter_by(car_id=car.id, is_active=True).first()

        if not ownership:
            continue

        health = calculate_vehicle_health(car, ownership)

        if health["health_status"] in ("critical", "attention"):
            vehicles_at_risk.append({"car": car, "health": health})

    return render_template(
        "admin/control_panel.html",
        consultations_today=consultations_today,
        active_consultations=active_consultations,
        draft_assessments=draft_assessments,
        vehicles_at_risk=vehicles_at_risk,
    )


# ======================================
# ADMIN DOWNLOAD ASSESSMENT PDF ROUTE
# ======================================


@admin_bp.route("/<int:assessment_id>/pdf", methods=["GET"])
@login_required
@advisor_required
def admin_download_assessment_pdf(assessment_id):

    assessment = VehicleAssessment.query.get_or_404(assessment_id)

    if not assessment.is_finalized:
        abort(403)

    # Build structured report
    report = build_assessment_report(assessment=assessment)

    # Render PDF
    pdf_file = render_assessment_pdf(report_data=report)

    return Response(
        pdf_file.read(),
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=AJF_ASSESSMENT_{assessment.id}.pdf"
        },
    )
