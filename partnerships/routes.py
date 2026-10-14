"""Mobile-friendly, admin-only Gulf campaign workbench.

Preparation flag exposes the workspace only to Aura administrators. A separate
explicit launch gate controls appointment confirmation. All mutations use the
existing app-wide CSRF mechanism and are audited without customer PII.
"""
import csv
from datetime import datetime
from io import BytesIO, StringIO
import uuid

from flask import (
    Blueprint, abort, Response, current_app, flash, redirect, render_template,
    request, send_file, url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from admin.utils import advisor_required
from evidence.image_sanitizer import sanitize_evidence_image, EvidenceImageValidationError
from evidence.storage import build_evidence_storage_provider, EvidenceStorageError
from extensions import db
from partnerships.models import (
    GulfAudit, GulfBusySlot, GulfDayPlan, GulfJob, GulfPayment, GulfPhoto, GulfShift, GulfTechnician,
)
from partnerships.technician_qualifications import (
    BRAND_SPECIALTIES, SERVICE_CLASSES, split_qualifications,
)
from partnerships.operations import (
    GulfOperationError, add_payment, audit, campaign_readiness, clean,
    confirm_job, create_request, nonnegative_money, parse_day, parse_local,
    record_hold,
)

gulf_bp = Blueprint("gulf_pilot", __name__, url_prefix="/admin/partners/gulf")
PHOTO_KINDS = {"odometer", "sealed_oil", "filters", "after", "other"}


@gulf_bp.before_request
def feature_off_by_default():
    if not current_app.config.get("AURA_GULF_PILOT_ENABLED", False):
        abort(404)
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    if current_user.role != "admin":
        abort(403)


def _require_int(value, label, minimum=0, maximum=100_000_000):
    try:
        n = int(str(value))
    except (TypeError, ValueError) as exc:
        raise GulfOperationError(f"Enter a valid {label}.") from exc
    if not minimum <= n <= maximum:
        raise GulfOperationError(f"{label} is outside the permitted range.")
    return n


def _dispatch(action):
    return redirect(url_for("gulf_pilot.dashboard"))


def _provider():
    return (current_app.extensions.get("evidence_storage_provider") or
            build_evidence_storage_provider(current_app.config))


def _safe_action(action, go_to):
    try:
        result = action()
        db.session.commit()
        flash("Gulf pilot record updated.", "success")
        return redirect(go_to if isinstance(go_to, str) else go_to(result))
    except (GulfOperationError, ValueError, IntegrityError, EvidenceImageValidationError) as exc:
        db.session.rollback()
        flash(str(exc) if isinstance(exc, GulfOperationError) else
              "The request could not be saved. Check the supplied information.", "error")
    except EvidenceStorageError:
        db.session.rollback()
        current_app.logger.exception("gulf_private_photo_store_error")
        flash("Private image storage is temporarily unavailable.", "error")
    return redirect(url_for("gulf_pilot.dashboard"))


@gulf_bp.get("/")
@login_required
@advisor_required
def dashboard():
    jobs = GulfJob.query.order_by(GulfJob.created_at.desc()).limit(120).all()
    technicians = GulfTechnician.query.order_by(GulfTechnician.display_name).all()
    shifts = GulfShift.query.order_by(GulfShift.starts_at.desc()).limit(25).all()
    blocks = GulfBusySlot.query.order_by(GulfBusySlot.starts_at.desc()).limit(25).all()
    plans = GulfDayPlan.query.order_by(GulfDayPlan.service_day.desc()).limit(14).all()
    stats = {status: sum(j.state == status for j in jobs) for status in
             ("new", "tentative", "confirmed", "in_service", "completed", "handed_over")}
    waiting = sum(j.dispatch_state in {"requested", "dispatched"} for j in jobs)
    due = sum((j.labour_fee_kobo or 0) - j.paid_kobo
              for j in jobs if j.claim_state in {"submitted", "partial"})
    return render_template(
        "admin/gulf_dashboard.html", jobs=jobs, technicians=technicians,
        shifts=shifts, blocks=blocks, plans=plans, stats=stats,
        waiting=waiting, due_naira=due // 100, pilot_live=current_app.config.get("AURA_GULF_CAMPAIGN_ACTIVE", False),
    )


@gulf_bp.post("/jobs")
@login_required
@advisor_required
def new_job():
    return _safe_action(
        lambda: create_request(request.form, current_user.id),
        lambda j: url_for("gulf_pilot.job_detail", job_id=j.id),
    )


@gulf_bp.post("/technicians")
@login_required
@advisor_required
def new_technician():
    def save():
        name = clean(request.form.get("name"), 100, required=True)
        # Vehicle class and manufacturer competence are separate questions.
        # Parse checkbox values, not arbitrary comma-separated brand text.
        raw_classes = request.form.getlist("service_classes")
        raw_brands = request.form.getlist("brand_specialties")
        try:
            selected_classes = split_qualifications(
                ",".join(raw_classes), SERVICE_CLASSES
            )
            selected_brands = split_qualifications(
                ",".join(raw_brands), BRAND_SPECIALTIES
            )
        except ValueError as exc:
            raise GulfOperationError(
                "Select at least one service class and one brand specialisation."
            ) from exc
        row = GulfTechnician(
            display_name=name,
            qualified_classes=",".join(sorted(selected_classes)),
            brand_specialties=",".join(sorted(selected_brands)),
            is_intern=bool(request.form.get("is_intern")),
        )
        row.phone = clean(request.form.get("phone"), 30)
        db.session.add(row)
        db.session.flush()
        audit(None, current_user.id, "technician_added")
    return _safe_action(save, url_for("gulf_pilot.dashboard"))


@gulf_bp.post("/shifts")
@login_required
@advisor_required
def new_shift():
    def save():
        worker_id = _require_int(request.form.get("technician_id"), "technician", 1)
        worker = db.session.get(GulfTechnician, worker_id)
        if not worker or not worker.active:
            raise GulfOperationError("Choose an active technician.")
        start = parse_local(request.form.get("starts_at"))
        end = parse_local(request.form.get("ends_at"))
        if end <= start or start.date() != end.date():
            raise GulfOperationError("Accepted availability requires a same-day time range.")
        db.session.add(GulfShift(
            technician_id=worker_id, starts_at=start, ends_at=end,
            accepted=bool(request.form.get("accepted")), recorded_by=current_user.id,
        ))
        audit(None, current_user.id, "technician_availability_recorded")
    return _safe_action(save, url_for("gulf_pilot.dashboard"))


@gulf_bp.post("/busy")
@login_required
@advisor_required
def busy():
    def save():
        start = parse_local(request.form.get("starts_at"))
        end = parse_local(request.form.get("ends_at"))
        if end <= start or start.date() != end.date():
            raise GulfOperationError("Existing job must have valid same-day start and end.")
        worker_raw = request.form.get("technician_id")
        worker_id = _require_int(worker_raw, "technician ID", 1) if worker_raw else None
        if worker_id and not db.session.get(GulfTechnician, worker_id):
            raise GulfOperationError("Technician not found.")
        bay = clean(request.form.get("bay"), 2)
        if bay and bay not in {"1", "2", "3", "4"}:
            raise GulfOperationError("Select a valid work position.")
        if not bay and not worker_id:
            raise GulfOperationError("Set a technician or occupied work position.")
        db.session.add(GulfBusySlot(
            starts_at=start, ends_at=end, technician_id=worker_id,
            bay=bay or None, reason=clean(request.form.get("reason"), 100, required=True),
            is_oil_change=bool(request.form.get("is_oil_change")),
            recorded_by=current_user.id,
        ))
        audit(None, current_user.id, "existing_work_blocked")
    return _safe_action(save, url_for("gulf_pilot.dashboard"))


@gulf_bp.post("/busy/<int:block_id>/release")
@login_required
@advisor_required
def release_busy(block_id):
    def save():
        record = db.get_or_404(GulfBusySlot, block_id)
        db.session.delete(record)
        audit(None, current_user.id, "existing_work_block_released")
    return _safe_action(save, url_for("gulf_pilot.dashboard"))


@gulf_bp.post("/shifts/<int:shift_id>/revoke")
@login_required
@advisor_required
def revoke_shift(shift_id):
    def save():
        record = db.get_or_404(GulfShift, shift_id)
        active = GulfJob.query.filter(
            GulfJob.technician_id == record.technician_id,
            GulfJob.state.in_(("tentative", "confirmed", "in_service")),
            GulfJob.slot_start < record.ends_at,
            GulfJob.slot_end > record.starts_at,
        ).all()
        if any(j.state != "tentative" or
               (j.hold_expires_at and j.hold_expires_at > datetime.utcnow())
               for j in active):
            raise GulfOperationError("Reassign or cancel affected Gulf bookings first.")
        db.session.delete(record)
        audit(None, current_user.id, "technician_availability_revoked")
    return _safe_action(save, url_for("gulf_pilot.dashboard"))


@gulf_bp.post("/day-plan")
@login_required
@advisor_required
def day_plan():
    def save():
        date_value = parse_day(request.form.get("day"))
        daily = _require_int(request.form.get("daily_cap"), "daily limit", 0, 7)
        concurrent = _require_int(request.form.get("concurrent_cap"), "parallel limit", 0, 4)
        if concurrent > daily:
            raise GulfOperationError("Parallel capacity cannot exceed daily capacity.")
        reason = clean(request.form.get("reason"), 240, required=True)
        if (daily > 6 or concurrent > 2) and len(reason) < 12:
            raise GulfOperationError("Explain the verified resources supporting a capacity increase.")
        row = GulfDayPlan.query.filter_by(service_day=date_value).with_for_update().first()
        if not row:
            row = GulfDayPlan(service_day=date_value, approved_by=current_user.id, reason=reason)
            db.session.add(row)
        row.daily_cap, row.concurrent_cap = daily, concurrent
        row.saturday_verified = bool(request.form.get("saturday_verified"))
        row.sunday_open = bool(request.form.get("sunday_open"))
        row.reason, row.approved_by = reason, current_user.id
        audit(None, current_user.id, "capacity_approved")
    return _safe_action(save, url_for("gulf_pilot.dashboard"))


@gulf_bp.get("/jobs/<int:job_id>")
@login_required
@advisor_required
def job_detail(job_id):
    job = db.get_or_404(GulfJob, job_id)
    audit_rows = GulfAudit.query.filter_by(job_id=job.id).order_by(GulfAudit.id.desc()).limit(40).all()
    photos = GulfPhoto.query.filter_by(job_id=job.id).order_by(GulfPhoto.id.desc()).all()
    technicians = GulfTechnician.query.filter_by(active=True).all()
    payments = GulfPayment.query.filter_by(job_id=job.id).order_by(GulfPayment.id.desc()).all()
    points = current_app.config.get("AURA_GULF_CHECKLIST_ITEMS") or []
    return render_template(
        "admin/gulf_job.html", job=job, audit_rows=audit_rows, photos=photos,
        payments=payments, technicians=technicians, points=points,
        pilot_live=current_app.config.get("AURA_GULF_CAMPAIGN_ACTIVE", False),
    )


@gulf_bp.post("/jobs/<int:job_id>/action")
@login_required
@advisor_required
def job_action(job_id):
    job = db.get_or_404(GulfJob, job_id)
    action = clean(request.form.get("action"), 32, required=True)
    def apply():
        now = datetime.utcnow()
        if action == "clear_repeat":
            if not job.repeat_plate_review or job.state not in {"new", "tentative"}:
                raise GulfOperationError("No eligible repeat-plate review is pending.")
            ref = clean(request.form.get("reference"), 100, required=True)
            if len(ref) < 4:
                raise GulfOperationError("Record an Ebice review reference.")
            job.repeat_plate_reference = ref
            job.repeat_plate_cleared_at = now
        elif action == "verify":
            if job.state not in {"new", "tentative"}:
                raise GulfOperationError("Voucher cannot be reverified after the service starts.")
            job.verification_reference = clean(request.form.get("reference"), 120, required=True)
            job.voucher_verified_at = now
        elif action == "approve_oil":
            if job.state not in {"new", "tentative"}:
                raise GulfOperationError("Oil selection cannot change after final booking.")
            job.oil_spec = clean(request.form.get("oil_spec"), 100, required=True)
            job.oil_sku = clean(request.form.get("oil_sku"), 100, required=True)
            job.oil_litres = clean(request.form.get("litres"), 12, required=True)
            job.filter_part = clean(request.form.get("filter_part"), 100, required=True)
            if not request.form.get("human_approved"):
                raise GulfOperationError("Advisor must manually attest OEM and filter fitment.")
            job.technical_approved_at = now
            job.approved_by = current_user.id
        elif action == "dispatch":
            if not job.technical_approved_at:
                raise GulfOperationError("Approve exact engine-oil and filter requirements first.")
            status = clean(request.form.get("dispatch_state"), 20, required=True)
            transitions = {
                "not_requested": {"requested"},
                "requested": {"dispatched"},
                "dispatched": {"accepted", "rejected"},
                "rejected": {"requested"},
                "accepted": set(),
            }
            if status not in transitions.get(job.dispatch_state, set()):
                raise GulfOperationError("Dispatch change is out of sequence.")
            job.dispatch_state = status
            job.dispatch_reference = clean(request.form.get("reference"), 100) or job.dispatch_reference
            if status == "accepted":
                if not request.form.get("checked_product"):
                    raise GulfOperationError("Confirm physical receipt, correct grade, filter and quantity.")
                job.products_received_at = now
            elif status == "rejected":
                job.products_received_at = None
        elif action == "hold":
            record_hold(job, request.form, current_user.id)
        elif action == "confirm":
            if not request.form.get("customer_confirmed"):
                raise GulfOperationError("Record explicit customer acceptance.")
            job.customer_confirmed = True
            confirm_job(job, current_user.id)
        elif action == "start":
            if job.state != "confirmed":
                raise GulfOperationError("Only a confirmed appointment can start.")
            if job.dispatch_state != "accepted":
                raise GulfOperationError("Product approval is missing.")
            job.state, job.actual_start = "in_service", now
        elif action == "complete":
            items = campaign_readiness()
            if job.state != "in_service":
                raise GulfOperationError("Record the actual service start first.")
            inspection = {}
            for i, item in enumerate(items):
                value = clean(request.form.get(f"check_{i}"), 16, required=True)
                if value not in {"observed_ok", "observation", "not_checked"}:
                    raise GulfOperationError("Every agreed inspection point needs a result.")
                inspection[item] = value
            job.odometer_km = _require_int(request.form.get("odometer"), "odometer", 0, 2_000_000)
            job.oil_batch = clean(request.form.get("oil_batch"), 80, required=True)
            job.work_result = clean(request.form.get("work_result"), 500, required=True)
            if not request.form.get("level_and_leak_checked"):
                raise GulfOperationError("Oil level and post-service leak check must be attested.")
            job.inspection = inspection
            job.actual_finish, job.state = now, "completed"
        elif action == "handover":
            if job.state != "completed" or not request.form.get("handover_confirmed"):
                raise GulfOperationError("The completed service must have customer handover confirmation.")
            job.handover_at, job.state = now, "handed_over"
        elif action == "claim":
            if job.state != "handed_over" or job.claim_state != "unsubmitted":
                raise GulfOperationError("Claim requires a handed-over job and cannot be duplicated.")
            photo_kinds = {p.kind for p in GulfPhoto.query.filter_by(job_id=job.id).all()}
            if not {"odometer", "sealed_oil", "filters"}.issubset(photo_kinds):
                raise GulfOperationError("Private odometer, sealed oil and filter evidence required before claim.")
            if not job.inspection or not job.labour_fee_kobo or not job.handover_at:
                raise GulfOperationError("Service documentation or agreed rate is missing.")
            job.claim_state, job.claim_submitted_at = "submitted", now
        elif action == "payment":
            amount = nonnegative_money(request.form.get("amount_ngn"))
            add_payment(job, amount, current_user.id, reference=request.form.get("reference"))
        elif action == "commission":
            amount = nonnegative_money(request.form.get("amount_ngn"))
            add_payment(job, amount, current_user.id, technician=True,
                        reference=request.form.get("reference"))
        elif action == "cancel":
            if job.state not in {"new", "tentative", "confirmed"}:
                raise GulfOperationError("A started service needs an exception review, not cancellation.")
            job.state, job.hold_expires_at = "cancelled", None
        else:
            raise GulfOperationError("Unsupported Gulf pilot action.")
        audit(job.id, current_user.id, action)
    return _safe_action(apply, url_for("gulf_pilot.job_detail", job_id=job.id))


@gulf_bp.post("/jobs/<int:job_id>/photos")
@login_required
@advisor_required
def photo_upload(job_id):
    job = db.get_or_404(GulfJob, job_id)
    def save():
        if job.state not in {"confirmed", "in_service", "completed", "handed_over"}:
            raise GulfOperationError("Photo evidence requires a booked or in-progress Gulf job.")
        kind = clean(request.form.get("kind"), 30, required=True)
        if kind not in PHOTO_KINDS:
            raise GulfOperationError("Unknown service evidence type.")
        uploaded = request.files.get("image")
        if not uploaded:
            raise GulfOperationError("Select one JPEG, PNG or WebP image.")
        image = sanitize_evidence_image(uploaded.stream, declared_content_type=uploaded.content_type or "")
        key = f"internal/gulf/{job.id}/{uuid.uuid4().hex}{image.extension}"
        _provider().put_bytes(object_key=key, payload=image.payload, content_type=image.content_type)
        db.session.add(GulfPhoto(
            job_id=job.id, kind=kind, object_key=key, mime_type=image.content_type,
            sha256=image.sha256, created_by=current_user.id,
        ))
        audit(job.id, current_user.id, "private_evidence_saved")
    return _safe_action(save, url_for("gulf_pilot.job_detail", job_id=job.id))


@gulf_bp.get("/photos/<int:photo_id>")
@login_required
@advisor_required
def private_photo(photo_id):
    photo = db.get_or_404(GulfPhoto, photo_id)
    try:
        stored = _provider().get_bytes(object_key=photo.object_key, max_bytes=5 * 1024 * 1024)
    except EvidenceStorageError:
        abort(503)
    response = send_file(BytesIO(stored.payload), mimetype=photo.mime_type)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Disposition"] = "inline"
    return response


@gulf_bp.get("/settlement.csv")
@login_required
@advisor_required
def settlement_export():
    rows = GulfJob.query.filter(GulfJob.claim_state.in_(("submitted", "partial", "paid"))).order_by(GulfJob.id).all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(("Internal job", "Voucher suffix", "Make", "Model", "Labour NGN", "Paid NGN", "Balance NGN", "Claim state"))
    for job in rows:
        # Prevent spreadsheet formula injection from untrusted vehicle labels.
        make = "'" + job.make if job.make and job.make[0] in "=+-@" else job.make
        model = "'" + job.model if job.model and job.model[0] in "=+-@" else job.model
        writer.writerow((
            job.id, "..." + job.voucher_tail, make, model,
            (job.labour_fee_kobo or 0) // 100, job.paid_kobo // 100,
            ((job.labour_fee_kobo or 0) - job.paid_kobo) // 100, job.claim_state,
        ))
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=gulf-settlement.csv"})
