"""Validated, admin-controlled Gulf pilot transitions and capacity checks.

All clock times in the pilot form are naive Africa/Lagos local wall times.
No public/partner write access. No autonomous oil selection, diagnosis or
voucher redemption. Mutations are committed by the calling Flask route.
"""
from datetime import date, datetime, timedelta
import hashlib
import hmac
import re

from flask import current_app
from sqlalchemy import select

from extensions import db
from partnerships.capacity import (
    BayAvailability, DayPolicy, TechnicianAvailability, TimeWindow, assess_slot, LAGOS,
)
from partnerships.models import (
    GulfAudit, GulfBusySlot, GulfDayPlan, GulfJob, GulfPayment, GulfShift, GulfTechnician,
)

ACTIVE_STATES = ("tentative", "confirmed", "in_service", "completed", "handed_over")
RESERVED_STATES = ("tentative", "confirmed", "in_service")
PAID_STATES = ("submitted", "partial", "paid")


class GulfOperationError(ValueError):
    pass


def clean(value, limit=120, *, required=False):
    result = str(value or "").strip()
    if len(result) > limit or (required and not result):
        raise GulfOperationError("A required field is empty or too long.")
    return result


def parse_local(value):
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except (TypeError, ValueError) as exc:
        raise GulfOperationError("Enter a valid Lagos date and time.") from exc
    return parsed


def parse_day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise GulfOperationError("Enter a valid calendar date.") from exc


def nonnegative_money(value):
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GulfOperationError("Enter a whole Naira amount.") from exc
    if result < 0 or result > 100_000_000:
        raise GulfOperationError("Amount is outside the accepted range.")
    return result * 100


def audit(job_id, actor_id, action, note=None):
    db.session.add(GulfAudit(
        job_id=job_id, actor_user_id=actor_id,
        action=action, note=note,
    ))


def voucher_digest(voucher):
    # Never use unsalted SHA-256: campaign codes are structured/predictable.
    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    return hmac.new(secret, voucher.upper().encode("utf-8"), hashlib.sha256).hexdigest()


def create_request(data, actor_id):
    voucher = clean(data.get("voucher"), 80, required=True).upper()
    if not re.fullmatch(r"[A-Z0-9-]{5,80}", voucher):
        raise GulfOperationError("Voucher code must use letters, digits or hyphens.")
    name = clean(data.get("customer_name"), 120, required=True)
    phone = clean(data.get("phone"), 30, required=True)
    plate = clean(data.get("plate"), 40, required=True)
    make = clean(data.get("make"), 70, required=True)
    model = clean(data.get("model"), 90, required=True)
    klass = clean(data.get("service_class"), 20, required=True)
    if klass not in {"car", "suv", "exception"}:
        raise GulfOperationError("Choose the correct campaign service class.")
    year = clean(data.get("model_year"), 4)
    if year and (not year.isdigit() or not 1950 <= int(year) <= datetime.now().year + 1):
        raise GulfOperationError("Vehicle year is invalid.")
    digest = voucher_digest(voucher)
    if GulfJob.query.filter_by(voucher_digest=digest).first():
        raise GulfOperationError("This voucher already has a Gulf job record.")
    job = GulfJob(
        voucher_digest=digest, voucher_tail=voucher[-6:],
        make=make, model=model, model_year=int(year) if year else None,
        fuel=clean(data.get("fuel"), 30) or None,
        service_class=klass,
        requested_day=parse_day(data["requested_day"]) if data.get("requested_day") else None,
        created_by=actor_id,
    )
    job.voucher = voucher
    job.customer_name = name
    job.phone = phone
    job.plate = plate
    db.session.add(job)
    db.session.flush()
    audit(job.id, actor_id, "request_created")
    return job


def _accepted_shift(technician_id, start, end):
    return GulfShift.query.filter(
        GulfShift.technician_id == technician_id,
        GulfShift.accepted.is_(True),
        GulfShift.starts_at <= start,
        GulfShift.ends_at >= end,
    ).first() is not None


def _candidate_resources(job_id, technician_id, bay, start, end):
    technician = db.session.get(GulfTechnician, technician_id)
    if not technician or not technician.active or technician.is_intern:
        raise GulfOperationError("Assign an active qualified lead technician; interns may assist.")
    if not _accepted_shift(technician_id, start, end):
        raise GulfOperationError("Technician has not explicitly accepted this work window.")
    occupied = GulfBusySlot.query.filter(
        GulfBusySlot.starts_at < end, GulfBusySlot.ends_at > start
    ).all()
    ongoing = GulfJob.query.filter(
        GulfJob.id != job_id,
        GulfJob.state.in_(RESERVED_STATES),
        GulfJob.slot_start < end, GulfJob.slot_end > start,
    ).all()
    # An expired tentative reservation ceases to occupy workshop resources.
    ongoing = [j for j in ongoing if j.state != "tentative" or
               (j.hold_expires_at and j.hold_expires_at > datetime.utcnow())]
    tech_busy = [TimeWindow(b.starts_at.replace(tzinfo=LAGOS),
                            b.ends_at.replace(tzinfo=LAGOS))
                 for b in occupied if b.technician_id == technician_id]
    tech_busy += [TimeWindow(j.slot_start.replace(tzinfo=LAGOS),
                             j.slot_end.replace(tzinfo=LAGOS))
                  for j in ongoing if j.technician_id == technician_id]
    bay_busy = [TimeWindow(b.starts_at.replace(tzinfo=LAGOS),
                           b.ends_at.replace(tzinfo=LAGOS))
                for b in occupied if b.bay == bay]
    bay_busy += [TimeWindow(j.slot_start.replace(tzinfo=LAGOS),
                            j.slot_end.replace(tzinfo=LAGOS))
                 for j in ongoing if j.bay == bay]
    tech = TechnicianAvailability(
        technician_id=str(technician_id),
        qualified_for=frozenset(s.strip() for s in technician.qualified_classes.split(",")),
        accepted_windows=(TimeWindow(start.replace(tzinfo=LAGOS),end.replace(tzinfo=LAGOS)),),
        other_commitments=tuple(tech_busy),
    )
    bay_model = BayAvailability(
        bay_id=bay,
        open_windows=(TimeWindow(start.replace(tzinfo=LAGOS),end.replace(tzinfo=LAGOS)),),
        other_commitments=tuple(bay_busy),
    )
    return tech, bay_model, occupied, ongoing


def _day_plan(day, actor_id):
    # Persist a row for the day so concurrent transactions can lock this
    # shared booking resource on PostgreSQL. Real bookings must use this.
    plan = db.session.execute(
        select(GulfDayPlan).where(GulfDayPlan.service_day == day).with_for_update()
    ).scalar_one_or_none()
    if plan is None:
        plan = GulfDayPlan(
            service_day=day, daily_cap=6, concurrent_cap=2,
            reason="Default pilot safety limits", approved_by=actor_id,
        )
        db.session.add(plan)
        db.session.flush()
    return plan


def evaluate_reservation(job, technician_id, bay, start, end, actor_id):
    """Called in one DB transaction holding the day's plan row lock."""
    if end <= start or (end - start) > timedelta(hours=5):
        raise GulfOperationError("Reservation duration must be positive and realistic.")
    if end - start < timedelta(minutes=90):
        raise GulfOperationError("Hold at least 90 minutes until full service timing is approved.")
    if start < datetime.now(LAGOS).replace(tzinfo=None) - timedelta(minutes=15):
        raise GulfOperationError("Cannot reserve a past appointment.")
    if not re.fullmatch(r"[1-4]", bay):
        raise GulfOperationError("Select a recognised service work position.")
    if start.date() != end.date():
        raise GulfOperationError("Appointments cannot cross a Lagos service day.")
    plan = _day_plan(start.date(), actor_id)
    tech, work_bay, occupied, ongoing = _candidate_resources(job.id, technician_id, bay, start, end)
    jobs_today = GulfJob.query.filter(
        GulfJob.id != job.id,
        GulfJob.slot_start >= datetime.combine(start.date(), datetime.min.time()),
        GulfJob.slot_start < datetime.combine(start.date() + timedelta(days=1), datetime.min.time()),
        GulfJob.state.in_(ACTIVE_STATES),
    ).all()
    counted = sum(1 for j in jobs_today if j.state != "tentative" or
                  (j.hold_expires_at and j.hold_expires_at > datetime.utcnow()))
    regular_daily = sum(1 for b in GulfBusySlot.query.filter(
        GulfBusySlot.starts_at >= datetime.combine(start.date(), datetime.min.time()),
        GulfBusySlot.starts_at < datetime.combine(start.date() + timedelta(days=1), datetime.min.time()),
        GulfBusySlot.is_oil_change.is_(True),
    ).all())
    overlap = len(ongoing) + sum(1 for b in occupied if b.is_oil_change)
    policy = DayPolicy(
        service_day=start.date(), counted_gulf_jobs=counted,
        baseline_day_cap=plan.daily_cap, baseline_concurrent_cap=plan.concurrent_cap,
        saturday_staff_check_done=plan.saturday_verified,
        sunday_open_approved=plan.sunday_open,
    )
    result = assess_slot(
        window=TimeWindow(start.replace(tzinfo=LAGOS), end.replace(tzinfo=LAGOS)),
        policy=policy, required_skill=job.service_class,
        technicians=(tech,), bays=(work_bay,),
        overlapping_oil_jobs=overlap,
        non_gulf_oil_jobs_today=regular_daily,
    )
    if result.blockers:
        raise GulfOperationError("Appointment is blocked: " + ", ".join(result.blockers))
    return plan


def record_hold(job, data, actor_id):
    if job.state not in {"new", "tentative"}:
        raise GulfOperationError("Only unconfirmed requests can be scheduled.")
    if not job.voucher_verified_at or not job.technical_approved_at:
        raise GulfOperationError("Verify voucher and approve correct oil/filter first.")
    tech_id = int(data.get("technician_id") or 0)
    start = parse_local(data.get("starts_at"))
    end = parse_local(data.get("ends_at"))
    bay = clean(data.get("bay"), 2, required=True)
    commission = nonnegative_money(data.get("commission_ngn"))
    evaluate_reservation(job, tech_id, bay, start, end, actor_id)
    job.slot_start, job.slot_end, job.bay = start, end, bay
    job.technician_id = tech_id
    job.hold_expires_at = datetime.utcnow() + timedelta(hours=2)
    job.commission_kobo = commission
    job.state = "tentative"
    audit(job.id, actor_id, "appointment_held")
    return job


def campaign_readiness():
    if not current_app.config.get("AURA_GULF_CAMPAIGN_ACTIVE", False):
        raise GulfOperationError("Campaign is inactive pending signed agreement.")
    checks = [
        "AURA_GULF_PARTNER_SIGNED",
        "AURA_GULF_FILTERS_APPROVED",
        "AURA_GULF_RATES_APPROVED",
    ]
    if any(not current_app.config.get(c, False) for c in checks):
        raise GulfOperationError("Partner agreement, filter approval or prices are not authorised.")
    raw = current_app.config.get("AURA_GULF_CHECKLIST_ITEMS", [])
    if not isinstance(raw, (list, tuple)) or len(raw) != 10 or len(set(raw)) != 10:
        raise GulfOperationError("Ten distinct inspection points must be agreed.")
    return raw


def confirm_job(job, actor_id):
    campaign_readiness()
    if job.state != "tentative" or not job.hold_expires_at or job.hold_expires_at <= datetime.utcnow():
        raise GulfOperationError("Tentative booking is expired or unavailable.")
    if job.dispatch_state != "accepted" or job.products_received_at is None:
        raise GulfOperationError("Correct oil and filter must be received and accepted.")
    if not job.voucher_verified_at or not job.technical_approved_at:
        raise GulfOperationError("Voucher and lubricant specification are not verified.")
    if not job.technician_id or job.commission_kobo is None or not job.bay:
        raise GulfOperationError("Technician and commission must be agreed.")
    evaluate_reservation(job, job.technician_id, job.bay, job.slot_start, job.slot_end, actor_id)
    rate_key = ("AURA_GULF_CAR_FEE_NGN" if job.service_class == "car" else
                "AURA_GULF_SUV_FEE_NGN" if job.service_class == "suv" else None)
    if not rate_key:
        raise GulfOperationError("Exception vehicles need separately agreed pricing.")
    fee = nonnegative_money(current_app.config.get(rate_key))
    if not fee or job.commission_kobo > fee:
        raise GulfOperationError("Campaign rate is missing or below agreed commission.")
    job.labour_fee_kobo = fee
    job.state = "confirmed"
    job.hold_expires_at = None
    audit(job.id, actor_id, "appointment_confirmed")
    return job


def add_payment(job, amount_kobo, actor_id, *, technician=False, reference=None):
    if job.state != "handed_over":
        raise GulfOperationError("Payments can only be reconciled after handover.")
    if technician:
        maximum = job.commission_kobo or 0
        existing = job.commission_paid_kobo
    else:
        if job.claim_state not in {"submitted", "partial", "paid"}:
            raise GulfOperationError("Submit partner claim before reconciling payment.")
        maximum = job.labour_fee_kobo or 0
        existing = job.paid_kobo
    if amount_kobo <= 0 or amount_kobo + existing > maximum:
        raise GulfOperationError("Payment must be positive and not exceed the agreed amount.")
    ref = clean(reference, 100, required=True)
    kind = "technician" if technician else "partner"
    if GulfPayment.query.filter_by(job_id=job.id, kind=kind, reference=ref).first():
        raise GulfOperationError("This payment reference has already been recorded.")
    db.session.add(GulfPayment(
        job_id=job.id, kind=kind, amount_kobo=amount_kobo,
        reference=ref, recorded_by=actor_id,
    ))
    if technician:
        job.commission_paid_kobo += amount_kobo
        audit(job.id, actor_id, "technician_paid")
    else:
        job.paid_kobo += amount_kobo
        job.claim_state = "paid" if job.paid_kobo == maximum else "partial"
        job.payment_reference = ref
        audit(job.id, actor_id, "partner_payment_recorded")
