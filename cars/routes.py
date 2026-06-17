# cars/routes.py

from re import DEBUG
from flask import (
    Blueprint,
    request,
    jsonify,
    render_template,
    redirect,
    url_for,
    flash,
    Response,
    current_app,
    abort,
)
from flask_login import login_required, current_user
from datetime import datetime
import hashlib
from sqlalchemy.exc import IntegrityError

from models import (
    db,
    Car,
    CarOwnership,
    VehicleEvent,
    EventAuditLog,
    CarFault,
    Consultation,
    VehicleAssessment,
    TreatmentPlan,
)
from rina.brain import notify_admin
from services.feature_gateways import (
    FEATURE_EMERGENCY_REVIEW,
    FEATURE_PRIORITY_COORDINATION,
    FEATURE_PRIORITY_SCHEDULING,
    FEATURE_PROACTIVE_REMINDERS,
    FEATURE_PREVENTIVE_TRACKING,
)
from services.vehicle_intelligence import calculate_vehicle_health
from services.report_builder import build_vehicle_report
from services.consultation_guard import require_active_consultation
from services.assessment_report_builder import build_assessment_report
from services.rina_escalation_engine import RinaEscalationEngine
from services.rina_alert_awareness_service import RinaCareContextService
from services.rina_action_suggestions import RinaCareGuidanceEngine
from services.care_pathways import (
    CARE_PLAN_LABELS,
    has_priority_access,
    has_preventive_coverage,
)

from services.feature_gateways import has_feature


from io import BytesIO

from services.whatsapp import notify_admin_new_booking, send_booking_confirmation
from models import BookingIntent


cars_bp = Blueprint("cars", __name__, url_prefix="/cars")

# =========================================================
# VEHICLE LIST (API / DEBUG SAFE)
# =========================================================


@cars_bp.route("/", methods=["GET"])
@login_required
def get_cars():
    ownerships = (
        CarOwnership.query.join(Car)
        .filter(CarOwnership.user_id == current_user.id)
        .all()
    )

    vehicles = []
    for o in ownerships:
        if not o.car:
            continue

        vehicles.append(
            {
                "vehicle_id": o.car.id,
                "vehicle": f"{o.car.brand} {o.car.model} {o.car.year}",
                "vin": o.car.vin,
                "plate_reference": o.plate_number,
                "current_mileage": o.car.current_mileage,
                "assigned_since": o.start_date.isoformat() if o.start_date else None,
                "is_active": o.is_active,
            }
        )

    return jsonify(vehicles), 200


# =========================================================
# ADD VEHICLE
# =========================================================


@cars_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_car():
    if request.method == "POST":
        brand = request.form.get("brand", "").strip()
        model = request.form.get("model", "").strip()
        year = request.form.get("year", type=int)
        vin = request.form.get("vin", "").strip().upper()
        engine_number = request.form.get("engine_number", "").strip()
        engine_type = request.form.get("engine_type", "").strip()
        transmission_type = request.form.get("transmission", "").strip()
        plate_number = request.form.get("plate_number", "").strip()
        color = request.form.get("color", "").strip()
        mileage = request.form.get("mileage_at_transfer", type=int)

        if not all([brand, model, year, vin, plate_number, mileage]):
            flash("All fields are required.", "error")
            return render_template("cars/add_car.html")

        try:
            car = Car.query.filter_by(vin=vin).first()
            existing_owner = None

            if car:
                existing_owner = CarOwnership.query.filter_by(
                    car_id=car.id,
                    is_active=True,
                ).first()

            if existing_owner and existing_owner.user_id != current_user.id:
                flash("This vehicle is already registered to another user.", "error")
                return render_template("cars/add_car.html")

            if not car:
                car = Car(
                    brand=brand,
                    model=model,
                    year=year,
                    vin=vin,
                    engine_number=engine_number,
                    engine_type=engine_type,
                    transmission_type=transmission_type,
                    color=color,
                    current_mileage=mileage,
                )
                db.session.add(car)
                db.session.flush()
            else:
                if mileage > (car.current_mileage or 0):
                    car.current_mileage = mileage

            ownership = CarOwnership.query.filter_by(
                user_id=current_user.id,
                car_id=car.id,
            ).first()

            if ownership:
                ownership.is_active = True
                ownership.start_date = ownership.start_date or datetime.utcnow()
            else:
                ownership = CarOwnership(
                    user_id=current_user.id,
                    car_id=car.id,
                    plate_number=plate_number,
                    mileage_at_transfer=mileage,
                    start_date=datetime.utcnow(),
                    is_active=True,
                )
                db.session.add(ownership)

            db.session.commit()

            flash("Vehicle added.", "success")
            return redirect(url_for("dashboard.aura_home"))

        except IntegrityError:
            db.session.rollback()
            flash("Vehicle already exists.", "error")

    return render_template("cars/add_car.html")


# =========================================================
# VEHICLE PROFILE (PRIMARY CLIENT VIEW)
# =========================================================


@cars_bp.route("/<int:car_id>", methods=["GET"])
@login_required
def car_detail(car_id):
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    car = ownership.car
    health = calculate_vehicle_health(car, ownership) if ownership else {}

    guidance = RinaCareGuidanceEngine.generate_guidance(car.id, current_user.id)
    care_context = RinaCareContextService.get_active_care_context(
        car.id, current_user.id
    )
    escalation = RinaEscalationEngine.evaluate(health, guidance, care_context)

    treatment_plans = (
        TreatmentPlan.query.filter_by(car_id=car.id)
        .order_by(TreatmentPlan.created_at.desc())
        .all()
    )

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
        treatment_plans=treatment_plans,
        consultations=consultations,
        assessments=assessments,
        has_active_consultation=has_active_consultation,
        active_driver=None,
        conversation_records=[],
        CARE_PLAN_LABELS=CARE_PLAN_LABELS,

        has_feature=has_feature,

        FEATURE_EMERGENCY_REVIEW=FEATURE_EMERGENCY_REVIEW,
        FEATURE_PRIORITY_SCHEDULING=FEATURE_PRIORITY_SCHEDULING,
        FEATURE_PREVENTIVE_TRACKING=FEATURE_PREVENTIVE_TRACKING,
        FEATURE_PRIORITY_COORDINATION=FEATURE_PRIORITY_COORDINATION,
        FEATURE_PROACTIVE_REMINDERS=FEATURE_PROACTIVE_REMINDERS,
    )


# =========================================================
# VEHICLE HEALTH API (RAW)
# =========================================================


@cars_bp.route("/<int:car_id>/health", methods=["GET"])
@login_required
def car_health(car_id):
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    health = calculate_vehicle_health(ownership.car, ownership)

    return (
        jsonify(
            {
                "vehicle": f"{ownership.car.brand} {ownership.car.model} {ownership.car.year}",
                "health_status": health.get("health_status"),
                "overview": health.get("label"),
                "active_risks": health.get("risk_reasons", []),
                "recommended_next_step": health.get("next_action"),
                "disclaimer": (
                    "This overview reflects monitoring and observations. "
                    "It does not constitute a mechanical diagnosis."
                ),
            }
        ),
        200,
    )


# =========================================================
# CLIENT — ADD SERVICE
# =========================================================


@cars_bp.route("/<int:ownership_id>/service/add", methods=["GET", "POST"])
@login_required
def add_service_record(ownership_id):
    ownership = CarOwnership.query.filter_by(
        id=ownership_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    car = ownership.car
    # escalation = RinaEscalationEngine.evaluate(health, {}, {})

    # AUTHORITY GATE
    try:
        require_active_consultation(car.id)
    except PermissionError as e:
        flash(str(e), "error")
        return redirect(url_for("cars.car_detail", car_id=car.id))

    if request.method == "POST":
        try:
            create_service_event(
                car=car,
                ownership=ownership,
                # escalation=escalation,
                service_type=request.form["service_type"],
                mileage=int(request.form["mileage"]),
                description=request.form.get("description", ""),
                service_date=request.form["service_date"],
                performed_by=current_user.id,
                source="client",
            )
            flash("Service record saved.", "success")
            return redirect(url_for("cars.car_detail", car_id=car.id))
        except Exception as e:
            flash(str(e), "error")

    return render_template("cars/add_service.html", car=car, ownership=ownership)


# =========================================================
# CLIENT — ADD CONCERN (GUARDED)
# =========================================================


@cars_bp.route("/<int:car_id>/concerns/add", methods=["GET", "POST"])
@login_required
def add_reported_concern(car_id):
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    if request.method == "POST":
        description = request.form.get("description", "").strip()

        if not description:
            flash("Please describe your concern.", "error")
            return redirect(request.referrer)

        create_fault_record(
            car=ownership.car,
            category=request.form.get("category", "other"),
            description=description,
            observed_at=None,
            reported_by=current_user.id,
            source="client",
        )

        flash("Concern recorded.", "success")
        return redirect(url_for("cars.car_detail", car_id=car_id))

    return render_template("cars/concern_add.html", car=ownership.car)


# =========================================================
# VEHICLE REPORT (GUARDED)
# =========================================================


@cars_bp.route("/<int:car_id>/report", methods=["GET"])
@login_required
def vehicle_report(car_id):
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    try:
        require_active_consultation(car_id)
    except PermissionError as e:
        flash(str(e), "error")
        return redirect(url_for("cars.car_detail", car_id=car_id))

    report = build_vehicle_report(ownership.car, ownership)

    return render_template(
        "reports/vehicle_report.html",
        report=report,
        car=ownership.car,
        print_mode=request.args.get("print") == "1",
    )


# =========================================================
# VEHICLE MEDICAL FILE / TIMELINE (DAY 5)
# =========================================================


@cars_bp.route("/<int:car_id>/records", methods=["GET"])
@login_required
def vehicle_records(car_id):
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    services = (
        VehicleEvent.query.filter_by(
            car_id=car_id,
            ownership_id=ownership.id,
            is_deleted=False,
        )
        .order_by(VehicleEvent.created_at.desc())
        .all()
    )

    concerns = (
        CarFault.query.filter_by(car_id=car_id)
        .order_by(CarFault.created_at.desc())
        .all()
    )

    health = calculate_vehicle_health(ownership.car, ownership)

    return render_template(
        "reports/timeline.html",
        car=ownership.car,
        ownership=ownership,
        services=services,
        concerns=concerns,
        health=health,
        is_admin_view=False,
    )


# ====================================================
# VEHICLE RECORDS IN PDF
# ===================================================


@cars_bp.route("/<int:car_id>/records/pdf", methods=["GET"])
@login_required
def vehicle_records_pdf(car_id):
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    report = build_vehicle_report(ownership.car, ownership)

    return render_template(
        "reports/vehicle_report.html",
        report=report,
        car=ownership.car,
        print_mode=True,
        is_admin_view=False,
    )


# =========================================================
# CONSULTATION — BOOK
# =========================================================


@cars_bp.route("/<int:car_id>/consultations/book", methods=["GET", "POST"])
@login_required
def book_consultation(car_id):
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    # =============================
    # START BOOKING INTENT (GET)
    # =============================
    if request.method == "GET":

        existing_intent = BookingIntent.query.filter_by(
            user_id=current_user.id, car_id=car_id, completed=False
        ).first()

        if not existing_intent:
            intent = BookingIntent(
                user_id=current_user.id, car_id=car_id, started_at=datetime.utcnow()
            )
            db.session.add(intent)
            db.session.commit()

    if request.method == "POST":
        preferred_time = request.form.get("preferred_time")
        description = request.form.get("description", "").strip()

        if not preferred_time:
            flash("Please select a preferred time.", "error")
            return redirect(request.referrer)

        # ============================
        # CREATE CONSULTATION
        # ============================
        consultation = Consultation(
            car_id=car_id,
            ownership_id=ownership.id,
            advisor_id=None,  # assigned later by admin
            client_id=current_user.id,
            status="scheduled",  # scheduled until advisor confirms
            scheduled_for=datetime.fromisoformat(preferred_time),
            notes=description if description else None,
        )

        db.session.add(consultation)
        db.session.commit()

        # ===========================
        # COMPLETE EXISTING INTENT
        # ===========================
        intent = (
            BookingIntent.query.filter_by(
                user_id=current_user.id, car_id=car_id, completed=False
            )
            .order_by(BookingIntent.started_at.desc())
            .first()
        )

        if intent:
            intent.completed = True
            db.session.commit()

        intent = (
            BookingIntent.query.filter_by(
                user_id=current_user.id, car_id=car_id, completed=False
            )
            .order_by(BookingIntent.started_at.desc())
            .first()
        )

        if intent:
            intent.completed = True
            db.session.commit()

        try:
            user_phone = current_user.phone_number.strip().replace("+", "")

            if user_phone.startswith("0"):
                user_phone = "234" + user_phone[1:]

            user_name = (
                getattr(current_user, "first_name", None)
                or getattr(current_user, "name", None)
                or "there"
            )

            vehicle_name = f"{ownership.car.brand} {ownership.car.model}"

            send_booking_confirmation(
                phone=user_phone,
                name=user_name,
                vehicle=vehicle_name,
            )

            notify_admin_new_booking(
                user=user_name, vehicle=vehicle_name, time=preferred_time
            )

        except Exception as e:
            print("WHATSAPP ERROR:", str(e))

        flash(
            "Consultation requested",
            "success",
        )
        return redirect(url_for("cars.car_detail", car_id=car_id))

    return render_template(
        "cars/book_consultation.html",
        car=ownership.car,
        ownership=ownership,
    )


# ===================================
# DEBUG FOR REMINDER
# ===================================
@cars_bp.route("/debug/run-reminders")
def run_reminders():
    from services.reminder_engine import check_abandoned_bookings

    check_abandoned_bookings()
    return "Reminder sent"


# =========================================================
# SHARED CORE LOGIC
# =========================================================


def create_service_event(
    *,
    car,
    ownership,
    service_type,
    mileage,
    description,
    service_date,
    performed_by,
    source,
):

    if mileage < (car.current_mileage or 0):
        raise ValueError("Mileage cannot be lower than current vehicle mileage.")

    fingerprint = hashlib.sha256(
        f"{car.id}|{ownership.id}|{service_type}|{mileage}|{service_date}".encode()
    ).hexdigest()

    if VehicleEvent.query.filter_by(fingerprint=fingerprint).first():
        raise ValueError("Duplicate service record detected.")

    car.current_mileage = mileage

    event = VehicleEvent(
        car_id=car.id,
        ownership_id=ownership.id,
        event_type="service",
        title=service_type,
        description=description,
        mileage=mileage,
        source=source,
        fingerprint=fingerprint,
        created_by=performed_by,
        created_at=datetime.fromisoformat(service_date),
        is_deleted=False,
    )

    db.session.add(event)
    db.session.commit()


def create_fault_record(
    *,
    car,
    category,
    description,
    observed_at,
    reported_by,
    source,
):
    fault = CarFault(
        car_id=car.id,
        title="Reported concern",
        category=category,
        description=description,
        status="reported",
        observed_at=observed_at,
        reported_by=reported_by,
        reported_at=datetime.utcnow(),
        source=source,
    )

    db.session.add(fault)
    db.session.commit()


# =========================================================
# CLIENT - VIEW FINALIZED ASSESSMENT REPORT
# =========================================================


@cars_bp.route("/<int:car_id>/assessment/report", methods=["GET"])
@login_required
def assessment_report(car_id):
    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    # get latest finalized assessment
    assessment = (
        VehicleAssessment.query.filter_by(
            car_id=car_id,
            is_finalized=True,
        )
        .order_by(VehicleAssessment.finalized_at.desc())
        .first()
    )

    if not assessment:
        flash("No assessment available.", "error")
        return redirect(url_for("cars.car_detail", car_id=car_id))

    report = build_assessment_report(assessment=assessment)

    return render_template(
        "reports/assessment_report.html",
        report=report,
        car=ownership.car,
        print_mode=request.args.get("print") == "1",
        is_admin_view=False,
    )


# ========================================
# PRIORITY SCHEDULING REQUEST
# ========================================


@cars_bp.route("/<int:car_id>/priority-request", methods=["POST", "GET"])
@login_required
def request_priority_scheduling(car_id):

    car = Car.query.get_or_404(car_id)

    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        user_id=current_user.id,
    ).first_or_404()

    if not has_feature(ownership, FEATURE_PRIORITY_SCHEDULING):
        abort(403)

    consultation = Consultation(
        car_id=car.id,
        ownership_id=ownership.id,
        client_id=current_user.id,
        status="scheduled",
        notes="Priority scheduling request by client.",
        scheduled_for=datetime.utcnow(),
    )

    db.session.add(consultation)
    db.session.commit()

    flash(
        "Priority scheduling request received. "
        "An advisor will coordinate the next available session.",
        "success",
    )

    return redirect(url_for("cars.car_detail", car_id=car.id))


# ===========================================
# EMERGENCY REVIEW REQUEST
# ==========================================
@cars_bp.route("/<int:car_id>/emergency-review", methods=["POST"])
@login_required
def request_emergency_review(car_id):

    car = Car.query.get_or_404(car_id)

    ownership = CarOwnership.query.filter_by(
        car_id=car.id,
        user_id=current_user.id,
        is_active=True,
    ).first_or_404()

    if not has_feature(ownership, FEATURE_EMERGENCY_REVIEW):
        abort(403)

    concern = CarFault(
        car_id=car.id,
        title="Emergency review requested",
        category="emergency_review",
        description=("Client requested immediate professional review."),
        status="reported",
        source="client",
        reported_by=current_user.id,
        reported_at=datetime.utcnow(),
    )

    db.session.add(concern)
    db.session.commit()

    flash(
        "Emergency review request received. "
        "An advisor will review the vehicle status shortly.",
        "success",
    )

    return redirect(url_for("cars.car_detail", car_id=car.id))
