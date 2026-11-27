"""Disabled-by-default Prince pilot JSON endpoints.

This API is intentionally NOT wired to global Aura owner, vehicle, evidence,
billing or assistant endpoints. Authorization is resolved from DB membership,
not caller-supplied role, tenant IDs or assignments.
"""
from __future__ import annotations

import re
from datetime import datetime

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models import User
from security.rate_limits import limiter
from security.field_encryption import (
    ProfileEncryptionError, decrypt_profile_value, encrypt_profile_value,
)
from partnerships.prince_access_policy import (
    PrinceAction, PrinceActor, PrinceRole, authorize, operator_intake_response,
    redact_operator_job,
)
from partnerships.prince_models import (
    PrinceAudit, PrinceClinicalHandoff, PrinceClinicalWithdrawal, PrinceCustomer,
    PrinceCustomerAuthorization, PrinceEntryReview,
    PrinceEstimate, PrinceInvoice, PrinceJob, PrinceJobAssignment,
    PrinceJobEntry, PrinceJobTransition, PrinceMembership, PrincePayment,
    PrincePaymentReversal, PrinceReceipt, PrinceTenant, PrinceVehicle,
)

prince_pilot_bp = Blueprint("prince_pilot", __name__, url_prefix="/pilot/workshops")

PRIVATE_CONTACT_PATTERN = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)"
)

ENTRY_CATEGORIES = {
    "customer_complaint": "Customer complaint (reported)",
    "technician_observation": "Technician observation",
    "diagnostic_finding": "Diagnostic finding (pending review)",
    "temporary_intervention": "Temporary intervention / workaround",
    "reported_work": "Work reportedly performed (not verified)",
    "outstanding_concern": "Outstanding concern",
    "next_action": "Recommended next action",
}

JOB_NEXT_STATUS = {
    "draft": "awaiting_approval",
    "awaiting_approval": "approved",
    "approved": "in_progress",
    "in_progress": "closed",
}

INTAKE_FIELDS = frozenset({
    "full_name", "vehicle_brand", "vehicle_model", "vehicle_year",
    "reported_concern",
    "privacy_notice_version", "participation_confirmed",
})


VEHICLE_DETAILS_NOTICE = "prince-vehicle-details-v1"
VEHICLE_TEXT_FIELDS = {
    "colour": 60, "engine_number": 80, "registration_number": 30,
    "trim": 100, "engine_type": 60, "fuel_type": 30, "transmission": 30,
    "body_type": 40,
}
VEHICLE_EXTRA_FIELDS = frozenset(VEHICLE_TEXT_FIELDS) | {
    "vin", "odometer_km", "vehicle_details_confirmed",
}


def _vehicle_details_ready(tenant):
    return bool(current_app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False)
                and tenant.vehicle_details_notice_version == VEHICLE_DETAILS_NOTICE)


def _visit_odometer(value):
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                              or not 0 <= value <= 9999999):
        abort(400)
    return value


def _plate_key(value):
    return re.sub(r"[ -]", "", value.upper())


def _plate_expression():
    return func.replace(func.replace(func.upper(PrinceVehicle.registration_number), " ", ""), "-", "")


def _vehicle_details(data, tenant):
    if not (set(data) & VEHICLE_EXTRA_FIELDS):
        return {}
    if not _vehicle_details_ready(tenant) or data.get("vehicle_details_confirmed") is not True:
        abort(400, description="Expanded vehicle details require approved notice and participation.")
    values = {}
    for field, limit in VEHICLE_TEXT_FIELDS.items():
        value = _text(data, field, limit)
        if value and (any(ord(ch) < 32 for ch in value) or
                      (field not in {"engine_number", "registration_number"} and PRIVATE_CONTACT_PATTERN.search(value))):
            abort(400)
        values[field] = value
    vin = _text(data, "vin", 17)
    if vin:
        vin = vin.upper()
        if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
            abort(400, description="VIN must have 17 valid characters; leave unknown VIN blank.")
    values["vin"] = vin
    for field in ("engine_number", "registration_number"):
        if values[field]:
            values[field] = values[field].upper()
            if not re.fullmatch(r"[A-Z0-9 /._-]+", values[field]):
                abort(400)
    if values["registration_number"] and (not re.fullmatch(r"[A-Z0-9 -]+", values["registration_number"])
                                            or len(_plate_key(values["registration_number"])) < 3):
        abort(400, description="Enter a valid registration number.")
    values["odometer_km"] = _visit_odometer(data.get("odometer_km"))
    values["details_notice_version"] = VEHICLE_DETAILS_NOTICE
    return values


@prince_pilot_bp.before_request
def pilot_access_gate():
    if current_app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False):
        from partnerships.prince_live_portal import live_workshop_request_allowed
        if not live_workshop_request_allowed():
            abort(404)
    elif not current_app.config.get("AURA_PRINCE_PILOT_API_ENABLED", False):
        abort(404)
    if not current_user.is_authenticated or not current_user.is_active:
        abort(401)


@prince_pilot_bp.teardown_request
def release_prince_request_transaction(_error):
    """Every mutation commits explicitly; release any remaining locks at request end.

    Flask normally removes its scoped session at application-context teardown.
    A caller may retain an outer app context, notably the PostgreSQL test harness.
    A rejected mutation must not keep its SELECT FOR UPDATE lock in that context.
    Never commit from teardown.
    """
    db.session.rollback()


def _scope(slug: str):
    tenant = PrinceTenant.query.populate_existing().filter_by(slug=slug, active=True).first()
    if tenant is None:
        abort(404)
    member = PrinceMembership.query.populate_existing().filter_by(
        tenant_id=tenant.id, user_id=current_user.id, active=True
    ).first()
    if member is None:
        abort(404)  # Avoid disclosing tenant existence.
    try:
        role = PrinceRole(member.role)
    except ValueError:
        abort(403)
    return tenant, PrinceActor(
        user_id=current_user.id, tenant_id=str(tenant.id),
        role=role, active=bool(member.active and current_user.is_active),
    )


def _allow(actor, tenant, action, assigned_user_id=None):
    assigned = (frozenset({assigned_user_id}) if assigned_user_id is not None
                else frozenset())
    if not authorize(
        actor, tenant_id=str(tenant.id), action=action, assigned_user_ids=assigned
    ):
        abort(404)  # Do not disclose an unassigned job.


def _text(data, field, maximum, *, required=False):
    value = data.get(field)
    if value is None or value == "":
        if required:
            abort(400)
        return None
    if not isinstance(value, str):
        abort(400)
    value = value.strip()
    if not value and required:
        abort(400)
    if len(value) > maximum or any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        abort(400)
    return value or None


def _public_concern(value):
    # Contact strings may be accidentally pasted into workshop notes.
    # Defensive output redaction complements intake validation; it is not
    # an exhaustive personal-data detector, so training is still required.
    return PRIVATE_CONTACT_PATTERN.sub("[private contact redacted]", value or "")


def _job_response(job: PrinceJob, customer: PrinceCustomer, vehicle: PrinceVehicle):
    return redact_operator_job({
        "customer_id": customer.id,
        "full_name": customer.full_name,
        "job_id": job.id,
        "vehicle_id": vehicle.id,
        "brand": vehicle.brand,
        "model": vehicle.model,
        "year": vehicle.year,
        "job_status": job.status,
        "work_authorization": _work_authorization(job),
        "reported_concern": _public_concern(job.reported_concern),
    })


def _audit(tenant_id, user_id, action, kind, target_id):
    db.session.add(PrinceAudit(
        tenant_id=tenant_id, actor_user_id=user_id, action=action,
        target_kind=kind, target_id=target_id,
    ))


@prince_pilot_bp.post("/<slug>/intake")
def intake(slug):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.INTAKE_CREATE)
    if (not tenant.intake_enabled or not tenant.approved_privacy_notice_version):
        abort(403)  # Requires explicit separate provisioning and approved notice.
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - (INTAKE_FIELDS | VEHICLE_EXTRA_FIELDS):
        abort(400)
    full_name = _text(data, "full_name", 120, required=True)
    if len(full_name) < 2:
        abort(400)
    if (current_app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False)
            and PRIVATE_CONTACT_PATTERN.search(full_name)):
        abort(400)
    version = _text(data, "privacy_notice_version", 48, required=True)
    if version != tenant.approved_privacy_notice_version:
        abort(400)
    if data.get("participation_confirmed") is not True:
        abort(400)

    brand = _text(data, "vehicle_brand", 100)
    model = _text(data, "vehicle_model", 100)
    concern = _text(data, "reported_concern", 1200)
    if concern and PRIVATE_CONTACT_PATTERN.search(concern):
        abort(400)
    year = data.get("vehicle_year")
    if (brand is None) != (model is None):
        abort(400)
    details = _vehicle_details(data, tenant)
    if not brand and (year is not None or concern is not None or details):
        abort(400)
    if year is not None and (isinstance(year, bool) or not isinstance(year, int) or
                             not 1886 <= year <= 2100):
        abort(400)

    # Dedicated staging portal is a synthetic-only training surface. This
    # server-side denylist/allowlist avoids collecting real customer contacts
    # through accidental mobile intake, even when the form is modified.
    if current_app.config.get("PRINCE_STAGING_LOCKDOWN", False):
        if not full_name.lower().startswith(("demo ", "synthetic ")):
            abort(400)

    # Serialize intake and repeat visits within this small workshop. The VIN
    # constraint is an additional defense against conflicting writes.
    tenant = PrinceTenant.query.populate_existing().filter_by(id=tenant.id, active=True).with_for_update().one()
    if not tenant.intake_enabled or tenant.approved_privacy_notice_version != version:
        abort(403)
    if details and not _vehicle_details_ready(tenant):
        abort(403)
    if details.get("registration_number") and PrinceVehicle.query.filter(
            PrinceVehicle.tenant_id == tenant.id,
            _plate_expression() == _plate_key(details["registration_number"])).first():
        return jsonify({"error": "Vehicle registration already exists. Ask a principal to review."}), 409
    if details.get("vin") and PrinceVehicle.query.filter_by(tenant_id=tenant.id, vin=details["vin"]).first():
        return jsonify({"error": "Vehicle already registered. Ask a principal to review the existing record."}), 409

    customer = PrinceCustomer(
        tenant_id=tenant.id, full_name=full_name,

        intake_by_user_id=actor.user_id,
        privacy_notice_version=version, participation_confirmed=True,
    )
    try:
        db.session.add(customer)
        db.session.flush()
        job_id = None
        if brand and model:
            vehicle = PrinceVehicle(
                tenant_id=tenant.id, customer_id=customer.id,
                brand=brand, model=model, year=year, **details,
            )
            db.session.add(vehicle)
            db.session.flush()
            job = PrinceJob(
                tenant_id=tenant.id, customer_id=customer.id,
                vehicle_id=vehicle.id, assigned_user_id=actor.user_id,
                reported_concern=concern, status="draft",
                visit_odometer_km=details.get("odometer_km"),
                visit_notice_version=version,
            )
            db.session.add(job)
            db.session.flush()
            job_id = job.id
            _audit(tenant.id, actor.user_id, "intake_job_created", "job", job_id)
        _audit(tenant.id, actor.user_id, "customer_intake_created", "customer", customer.id)
        response = operator_intake_response({
            "customer_id": customer.id, "full_name": customer.full_name,
            "job_id": job_id,
        })
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Conflicting record. Ask a principal to review existing vehicles."}), 409
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Intake could not be saved"}), 503
    return jsonify(response), 201



def _returning_scope(slug):
    tenant, actor = _scope(slug)
    if (not current_app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False)
            or not tenant.intake_enabled
            or not _vehicle_details_ready(tenant)
            or actor.role not in (PrinceRole.PRINCIPAL, PrinceRole.OPERATOR)):
        abort(404)
    _allow(actor, tenant, PrinceAction.INTAKE_CREATE)
    return tenant, actor


def _returning_vehicle(tenant, actor, data):
    kind = data.get("identifier_type")
    value = _text(data, "identifier", 50, required=True)
    q = PrinceVehicle.query.filter_by(tenant_id=tenant.id)
    if kind == "vin":
        value = value.upper()
        if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", value):
            abort(400, description="Enter the exact 17-character VIN.")
        q = q.filter(PrinceVehicle.vin == value,
                     PrinceVehicle.details_notice_version == VEHICLE_DETAILS_NOTICE)
    elif kind == "plate":
        if len(value) > 30 or not re.fullmatch(r"[A-Za-z0-9 -]+", value):
            abort(400, description="Enter the exact vehicle plate number.")
        value = _plate_key(value)
        if len(value) < 3:
            abort(400)
        q = q.filter(_plate_expression() == value,
                     PrinceVehicle.details_notice_version == VEHICLE_DETAILS_NOTICE)
    elif kind == "record":
        if not re.fullmatch(r"[1-9][0-9]{0,8}", value):
            abort(400)
        q = q.filter(PrinceVehicle.id == int(value))
    else:
        abort(400)
    matches = q.limit(2).all()
    if len(matches) != 1:
        # No distinction between missing and ambiguous records for operators.
        abort(404, description="No unique accessible vehicle. Ask a principal to review.")
    vehicle = matches[0]
    if actor.role == PrinceRole.OPERATOR:
        assigned = PrinceJob.query.filter_by(tenant_id=tenant.id, vehicle_id=vehicle.id,
                                              assigned_user_id=actor.user_id).first()
        if assigned is None:
            abort(404)
    customer = PrinceCustomer.query.filter_by(tenant_id=tenant.id, id=vehicle.customer_id).one_or_none()
    if customer is None or not customer.participation_confirmed:
        abort(404)
    return vehicle, customer


@prince_pilot_bp.post("/<slug>/returning-vehicle/lookup")
@limiter.limit("15 per minute; 100 per hour")
def returning_vehicle_lookup(slug):
    tenant, actor = _returning_scope(slug)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"identifier_type", "identifier"}:
        abort(400)
    vehicle, customer = _returning_vehicle(tenant, actor, data)
    all_jobs = PrinceJob.query.filter_by(tenant_id=tenant.id, vehicle_id=vehicle.id)
    jobs = all_jobs
    if actor.role == PrinceRole.OPERATOR:
        jobs = jobs.filter_by(assigned_user_id=actor.user_id)
    history = jobs.order_by(PrinceJob.id.desc()).limit(20).all()
    active = all_jobs.filter(PrinceJob.status != "closed").first() is not None
    # Only return history the current role may already read, not other operators' details.
    _audit(tenant.id, actor.user_id, "returning_vehicle_lookup", "vehicle", vehicle.id)
    db.session.commit()
    return jsonify({
        "vehicle_id": vehicle.id, "brand": vehicle.brand, "model": vehicle.model,
        "year": vehicle.year, "customer_name": customer.full_name,
        "active_job_exists": active,
        "history": [{"job_id": job.id, "status": job.status,
                     "date": job.created_at.strftime("%Y-%m-%d"),
                     "odometer_km": job.visit_odometer_km,
                     "reported_concern": _public_concern(job.reported_concern)}
                    for job in history],
    })


@prince_pilot_bp.post("/<slug>/returning-vehicle/jobs")
@limiter.limit("10 per minute; 50 per hour")
def returning_vehicle_job(slug):
    tenant, actor = _returning_scope(slug)
    data = request.get_json(silent=True)
    allowed = {"identifier_type", "identifier", "vehicle_id", "reported_concern",
               "odometer_km", "privacy_notice_version", "ownership_confirmed",
               "participation_confirmed"}
    if not isinstance(data, dict) or set(data) - allowed:
        abort(400)
    if (data.get("ownership_confirmed") is not True
            or data.get("participation_confirmed") is not True):
        abort(400, description="Verify vehicle identity, current customer and participation.")
    version = _text(data, "privacy_notice_version", 48, required=True)
    concern = _text(data, "reported_concern", 1200, required=True)
    if PRIVATE_CONTACT_PATTERN.search(concern):
        abort(400)
    odometer = _visit_odometer(data.get("odometer_km"))
    # Serialize with new registrations and other returning visits; re-check release
    # and record access after acquiring the lock.
    tenant = PrinceTenant.query.populate_existing().filter_by(id=tenant.id, active=True).with_for_update().one()
    if not tenant.intake_enabled or version != tenant.approved_privacy_notice_version or not _vehicle_details_ready(tenant):
        abort(403)
    tenant, actor = _returning_scope(slug)
    vehicle, customer = _returning_vehicle(tenant, actor, data)
    if type(data.get("vehicle_id")) is not int or data["vehicle_id"] != vehicle.id:
        abort(409, description="Vehicle selection changed. Search again.")
    if PrinceJob.query.filter(PrinceJob.tenant_id == tenant.id, PrinceJob.vehicle_id == vehicle.id,
                              PrinceJob.status != "closed").first():
        return jsonify({"error": "An active job exists. Continue it or ask a principal to review."}), 409
    job = PrinceJob(tenant_id=tenant.id, customer_id=customer.id,
                    vehicle_id=vehicle.id, assigned_user_id=actor.user_id,
                    reported_concern=concern, status="draft",
                    visit_odometer_km=odometer, visit_notice_version=version)
    db.session.add(job)
    try:
        db.session.flush()
        _audit(tenant.id, actor.user_id, "returning_vehicle_job_created", "job", job.id)
        result = {"job_id": job.id, "vehicle_id": vehicle.id}
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Unable to confirm new job. Refresh before retrying."}), 503
    return jsonify(result), 201


def _find_authorized_job(tenant, actor, job_id, *, lock=False):
    query = (
        db.session.query(PrinceJob, PrinceCustomer, PrinceVehicle)
        .join(PrinceCustomer, (PrinceCustomer.id == PrinceJob.customer_id) &
                              (PrinceCustomer.tenant_id == PrinceJob.tenant_id))
        .join(PrinceVehicle, (PrinceVehicle.id == PrinceJob.vehicle_id) &
                             (PrinceVehicle.tenant_id == PrinceJob.tenant_id))
        .filter(PrinceJob.id == job_id, PrinceJob.tenant_id == tenant.id)
        .populate_existing()
    )
    if lock:
        query = query.with_for_update(of=PrinceJob)
    record = query.first()
    if record is None:
        abort(404)
    job, customer, vehicle = record
    _allow(actor, tenant, PrinceAction.JOB_READ, job.assigned_user_id)
    return job, customer, vehicle


@prince_pilot_bp.get("/<slug>/jobs")
def assigned_jobs(slug):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.JOB_READ, actor.user_id)
    q = (
        db.session.query(PrinceJob, PrinceCustomer, PrinceVehicle)
        .join(PrinceCustomer, (PrinceCustomer.id == PrinceJob.customer_id) &
                              (PrinceCustomer.tenant_id == PrinceJob.tenant_id))
        .join(PrinceVehicle, (PrinceVehicle.id == PrinceJob.vehicle_id) &
                             (PrinceVehicle.tenant_id == PrinceJob.tenant_id))
        .filter(PrinceJob.tenant_id == tenant.id)
    )
    if actor.role == PrinceRole.OPERATOR:
        q = q.filter(PrinceJob.assigned_user_id == actor.user_id)
    records = q.order_by(PrinceJob.id.desc()).limit(50).all()
    return jsonify({"jobs": [_job_response(j, c, v) for j, c, v in records]})


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>")
def job_detail(slug, job_id):
    tenant, actor = _scope(slug)
    job, customer, vehicle = _find_authorized_job(tenant, actor, job_id)
    return jsonify(_job_response(job, customer, vehicle))


def _review_response(review):
    if review is None:
        return None
    return {
        "decision": review.decision,
        "basis": review.basis,
        "review_note": _public_concern(review.review_note),
        "reviewed_at": review.reviewed_at.isoformat(timespec="seconds") + "Z",
    }


def _entry_response(entry, *, include_private_review_note=False):
    review = PrinceEntryReview.query.filter_by(
        entry_id=entry.id, tenant_id=entry.tenant_id,
    ).first()
    review_data = _review_response(review)
    if review_data is not None and not include_private_review_note:
        review_data.pop("review_note", None)
    return {
        "id": entry.id,
        "category": entry.category,
        "category_label": ENTRY_CATEGORIES[entry.category],
        "body": _public_concern(entry.body),
        "review_state": entry.review_state,
        "recorded_at": entry.recorded_at.isoformat(timespec="seconds") + "Z",
        "principal_review": review_data,
    }


def _load_job_entries(tenant_id, job_ids, *, include_private_review_note=False):
    """Never load entries outside the already-authorized job set."""
    grouped = {job_id: [] for job_id in job_ids}
    if not job_ids:
        return grouped
    records = PrinceJobEntry.query.filter(
        PrinceJobEntry.tenant_id == tenant_id,
        PrinceJobEntry.job_id.in_(job_ids),
    ).order_by(PrinceJobEntry.id.asc()).all()
    for entry in records:
        grouped[entry.job_id].append(_entry_response(
            entry, include_private_review_note=include_private_review_note,
        ))
    return grouped


@prince_pilot_bp.patch("/<slug>/jobs/<int:job_id>/concern")
def legacy_concern_update_retired(slug, job_id):
    """Prevent ambiguous overwrites of historical concern/intervention text."""
    tenant, actor = _scope(slug)
    job, _, _ = _find_authorized_job(tenant, actor, job_id)
    _allow(actor, tenant, PrinceAction.JOB_EDIT, job.assigned_user_id)
    return jsonify({
        "error": "The old free-form field is retired. Refresh the page and "
                 "choose a category for the new workshop entry."
    }), 410


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/entries")
def job_entries(slug, job_id):
    tenant, actor = _scope(slug)
    job, _, _ = _find_authorized_job(tenant, actor, job_id)
    entries = _load_job_entries(
        tenant.id, [job.id],
        include_private_review_note=(actor.role == PrinceRole.PRINCIPAL),
    )[job.id]
    return jsonify({"job_id": job.id, "entries": entries})


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/entries")
def record_job_entry(slug, job_id):
    tenant, actor = _scope(slug)
    job, _, _ = _find_authorized_job(tenant, actor, job_id, lock=True)
    _allow(actor, tenant, PrinceAction.JOB_EDIT, job.assigned_user_id)
    if job.status == "closed":
        abort(403)
    if request.content_length and request.content_length > 4000:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"category", "body"}:
        abort(400)
    category = data.get("category")
    if not isinstance(category, str) or category not in ENTRY_CATEGORIES:
        abort(400)
    body = _text(data, "body", 1600, required=True)
    if len(body) < 5 or PRIVATE_CONTACT_PATTERN.search(body):
        abort(400)
    # Staging is not an authorized location for real vehicle repair history.
    if current_app.config.get("PRINCE_STAGING_LOCKDOWN", False):
        if not body.lower().startswith(("demo:", "synthetic:")):
            abort(400)
    entry = PrinceJobEntry(
        tenant_id=tenant.id, job_id=job.id, category=category,
        body=body, review_state="unverified",
        recorded_by_user_id=actor.user_id,
    )
    try:
        db.session.add(entry)
        db.session.flush()
        _audit(tenant.id, actor.user_id, "job_entry_recorded", "job_entry", entry.id)
        response = _entry_response(entry)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Job entry could not be saved"}), 503
    return jsonify({"job_id": job.id, "entry": response}), 201


@prince_pilot_bp.get("/<slug>/operator")
def operator_workspace(slug):
    """Operator-facing, mobile-first interface. No private contacts are read."""
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.JOB_READ, actor.user_id)
    query = (
        db.session.query(PrinceJob, PrinceCustomer, PrinceVehicle)
        .join(PrinceCustomer, (PrinceCustomer.id == PrinceJob.customer_id) &
                              (PrinceCustomer.tenant_id == PrinceJob.tenant_id))
        .join(PrinceVehicle, (PrinceVehicle.id == PrinceJob.vehicle_id) &
                             (PrinceVehicle.tenant_id == PrinceJob.tenant_id))
        .filter(PrinceJob.tenant_id == tenant.id)
    )
    if actor.role == PrinceRole.OPERATOR:
        query = query.filter(PrinceJob.assigned_user_id == actor.user_id)
    records = query.order_by(PrinceJob.id.desc()).limit(20).all()
    jobs = [_job_response(job, customer, vehicle)
            for job, customer, vehicle in records]
    by_job = _load_job_entries(
        tenant.id, [job["job_id"] for job in jobs],
        include_private_review_note=(actor.role == PrinceRole.PRINCIPAL),
    )
    for job, (stored_job, _, vehicle) in zip(jobs, records):
        job["entries"] = by_job[job["job_id"]]
        if _vehicle_details_ready(tenant):
            job["visit_odometer_km"] = stored_job.visit_odometer_km
        if _vehicle_details_ready(tenant) and vehicle.details_notice_version == VEHICLE_DETAILS_NOTICE:
            job["vehicle_details"] = {field: getattr(vehicle, field) for field in (*VEHICLE_TEXT_FIELDS, "vin", "odometer_km")}
    return render_template(
        "partnerships/prince_operator.html",
        workshop_name=tenant.display_name,
        slug=tenant.slug,
        jobs=jobs,
        entry_categories=ENTRY_CATEGORIES,
        privacy_notice_version=tenant.approved_privacy_notice_version,
        vehicle_details_ready=_vehicle_details_ready(tenant),
        can_intake=bool(tenant.intake_enabled and
                        tenant.approved_privacy_notice_version),
        can_manage_private=actor.role == PrinceRole.PRINCIPAL,
        can_review=actor.role == PrinceRole.PRINCIPAL,
    )


@prince_pilot_bp.get("/<slug>/customers/<int:customer_id>/private")
def principal_private_customer(slug, customer_id):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.CONTACT_READ)
    customer = PrinceCustomer.query.filter_by(id=customer_id, tenant_id=tenant.id).first()
    if customer is None:
        abort(404)
    try:
        result = {
            "customer_id": customer.id,
            "full_name": customer.full_name,
            "phone_number": decrypt_profile_value(customer.phone_ciphertext),
            "email": decrypt_profile_value(customer.email_ciphertext),
            "home_address": decrypt_profile_value(customer.address_ciphertext),
        }
    except ProfileEncryptionError:
        return jsonify({"error": "Private details unavailable"}), 503
    _audit(tenant.id, actor.user_id, "private_contact_accessed", "customer", customer.id)
    db.session.commit()
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store, private"
    return response


@prince_pilot_bp.patch("/<slug>/customers/<int:customer_id>/private")
def principal_update_customer_private(slug, customer_id):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.CONTACT_CORRECT)
    customer = PrinceCustomer.query.filter_by(id=customer_id, tenant_id=tenant.id).first()
    if customer is None:
        abort(404)
    if request.content_length and request.content_length > 2400:
        abort(413)
    data = request.get_json(silent=True)
    permitted = {"phone_number": 30, "email": 200, "home_address": 400}
    if (not isinstance(data, dict) or not data
            or not set(data) <= set(permitted)):
        abort(400)
    values = {}
    for key in data:
        if data[key] is not None and not isinstance(data[key], str):
            abort(400)
        values[key] = _text(data, key, permitted[key])
    if current_app.config.get("PRINCE_STAGING_LOCKDOWN", False):
        staging_values = {
            "phone_number": "DEMO CONTACT ONLY",
            "email": "demo@example.invalid",
            "home_address": "DEMO ADDRESS ONLY",
        }
        if any(value is not None and value != staging_values[key]
               for key, value in values.items()):
            abort(400)
    try:
        for key, value in values.items():
            setattr(customer, {
                "phone_number": "phone_ciphertext",
                "email": "email_ciphertext",
                "home_address": "address_ciphertext",
            }[key], encrypt_profile_value(value))
        _audit(tenant.id, actor.user_id, "private_contact_updated", "customer", customer.id)
        db.session.commit()
    except (ProfileEncryptionError, SQLAlchemyError):
        db.session.rollback()
        return jsonify({"error": "Secure private update unavailable"}), 503
    return jsonify({"customer_id": customer.id, "updated": True})


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/entries/<int:entry_id>/review")
def principal_review_entry(slug, job_id, entry_id):
    tenant, actor = _scope(slug)
    job, _, _ = _find_authorized_job(tenant, actor, job_id, lock=True)
    _allow(actor, tenant, PrinceAction.SERVICE_CONFIRM)
    if job.status == "closed":
        abort(403)
    entry = PrinceJobEntry.query.filter_by(
        id=entry_id, job_id=job.id, tenant_id=tenant.id,
    ).first()
    if entry is None:
        abort(404)
    data = request.get_json(silent=True)
    if (not isinstance(data, dict)
            or set(data) != {"decision", "basis", "review_note"}):
        abort(400)
    decision = data.get("decision")
    basis = data.get("basis")
    if decision not in {"acknowledged", "needs_evidence", "work_confirmed", "not_confirmed"}:
        abort(400)
    if basis not in {"direct_inspection", "documented_evidence",
                     "technician_demonstration", "not_applicable"}:
        abort(400)
    if (decision == "work_confirmed"
            and (entry.category != "reported_work" or basis == "not_applicable")):
        abort(400)
    if decision != "work_confirmed" and basis != "not_applicable":
        abort(400)
    note = _text(data, "review_note", 1200, required=True)
    if len(note) < 15 or PRIVATE_CONTACT_PATTERN.search(note):
        abort(400)
    if current_app.config.get("PRINCE_STAGING_LOCKDOWN", False):
        if not note.lower().startswith(("demo:", "synthetic:")):
            abort(400)
    if PrinceEntryReview.query.filter_by(entry_id=entry.id).first():
        return jsonify({"error": "Already reviewed; a correction requires a new process"}), 409
    record = PrinceEntryReview(
        tenant_id=tenant.id, job_id=job.id, entry_id=entry.id,
        decision=decision, basis=basis, review_note=note,
        reviewed_by_user_id=actor.user_id,
    )
    try:
        db.session.add(record)
        db.session.flush()
        _audit(tenant.id, actor.user_id, "principal_entry_reviewed", "job_entry", entry.id)
        payload = _review_response(record)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Review could not be saved"}), 503
    return jsonify({"entry_id": entry.id, "principal_review": payload}), 201


def _eligible_operators(tenant_id):
    """Only active operator memberships, never owner/admin or global Aura users."""
    query = (
        db.session.query(PrinceMembership, User)
        .join(User, User.id == PrinceMembership.user_id)
        .filter(
            PrinceMembership.tenant_id == tenant_id,
            PrinceMembership.role == "operator",
            PrinceMembership.active.is_(True),
            User.is_active.is_(True),
        )
        .order_by(User.name.asc(), User.id.asc())
    )
    rows = query.all()
    if current_app.config.get("PRINCE_STAGING_LOCKDOWN", False):
        # Never permit a genuine account on a demo job, even if an operator
        # membership was accidentally provisioned in the staging database.
        allowed = {
            "prince-demo-operator-a@example.invalid",
            "prince-demo-operator-b@example.invalid",
        }
        rows = [(member, user) for member, user in rows
                if (user.email or "").lower() in allowed]
    if current_app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False):
        from partnerships.prince_live_portal import _staff_member
        rows = [(member, user) for member, user in rows if _staff_member(user) is not None]
    return [{"id": user.id, "name": user.name} for _, user in rows]


@prince_pilot_bp.get("/<slug>/manage")
def principal_management(slug):
    """Tenant-scoped, principal-only operational summary (no private contacts)."""
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.MANAGEMENT_VIEW)

    total_jobs = db.session.query(func.count(PrinceJob.id)).filter_by(
        tenant_id=tenant.id,
    ).scalar() or 0
    closed_jobs = db.session.query(func.count(PrinceJob.id)).filter_by(
        tenant_id=tenant.id, status="closed",
    ).scalar() or 0
    customers = db.session.query(func.count(PrinceCustomer.id)).filter_by(
        tenant_id=tenant.id,
    ).scalar() or 0
    pending_reviews = (
        db.session.query(func.count(PrinceJobEntry.id))
        .join(
            PrinceJob,
            (PrinceJob.id == PrinceJobEntry.job_id)
            & (PrinceJob.tenant_id == PrinceJobEntry.tenant_id),
        )
        .outerjoin(
            PrinceEntryReview,
            (PrinceEntryReview.entry_id == PrinceJobEntry.id)
            & (PrinceEntryReview.tenant_id == PrinceJobEntry.tenant_id),
        )
        .filter(
            PrinceJobEntry.tenant_id == tenant.id,
            PrinceJob.status != "closed",
            PrinceEntryReview.id.is_(None),
        )
        .scalar() or 0
    )
    operators = _eligible_operators(tenant.id)
    follow_up_jobs = (
        db.session.query(PrinceJobTransition, PrinceCustomer, PrinceVehicle)
        .join(PrinceJob, (PrinceJob.id == PrinceJobTransition.job_id)
              & (PrinceJob.tenant_id == PrinceJobTransition.tenant_id))
        .join(PrinceCustomer, (PrinceCustomer.id == PrinceJob.customer_id)
              & (PrinceCustomer.tenant_id == PrinceJob.tenant_id))
        .join(PrinceVehicle, (PrinceVehicle.id == PrinceJob.vehicle_id)
              & (PrinceVehicle.tenant_id == PrinceJob.tenant_id))
        .filter(PrinceJobTransition.tenant_id == tenant.id,
                PrinceJob.status == "closed",
                PrinceJobTransition.new_status == "closed",
                PrinceJobTransition.closure_disposition == "follow_up_required")
    )
    follow_up_count = follow_up_jobs.count()
    follow_ups = [
        {"job_id": event.job_id, "customer": customer.full_name,
         "vehicle": f"{vehicle.brand} {vehicle.model}",
         "plan": _public_concern(event.follow_up_plan)}
        for event, customer, vehicle in follow_up_jobs.order_by(
            PrinceJobTransition.id.desc()).limit(20).all()
    ]

    # One tenant-bound join chain; no cross-company or contact columns.
    records = (
        db.session.query(PrinceJob, PrinceCustomer, PrinceVehicle, User.name)
        .join(
            PrinceCustomer,
            (PrinceCustomer.id == PrinceJob.customer_id)
            & (PrinceCustomer.tenant_id == PrinceJob.tenant_id),
        )
        .join(
            PrinceVehicle,
            (PrinceVehicle.id == PrinceJob.vehicle_id)
            & (PrinceVehicle.tenant_id == PrinceJob.tenant_id),
        )
        .join(User, User.id == PrinceJob.assigned_user_id)
        .filter(PrinceJob.tenant_id == tenant.id)
        .order_by(PrinceJob.id.desc()).limit(40).all()
    )
    jobs = []
    for job, customer, vehicle, assignee_name in records:
        # Use the same safe job fields already exposed to operators.
        row = _job_response(job, customer, vehicle)
        row["assignee_user_id"] = job.assigned_user_id
        row["assignee_name"] = assignee_name
        row["next_status"] = JOB_NEXT_STATUS.get(job.status)
        row["transitions"] = [
            _transition_response(event)
            for event in PrinceJobTransition.query.filter_by(
                tenant_id=tenant.id, job_id=job.id,
            ).order_by(PrinceJobTransition.id.asc()).all()
        ]
        jobs.append(row)

    review_queue = (
        db.session.query(
            PrinceJobEntry.id,
            PrinceJobEntry.category,
            PrinceJobEntry.job_id,
            PrinceCustomer.full_name,
            PrinceVehicle.brand,
            PrinceVehicle.model,
        )
        .join(
            PrinceJob,
            (PrinceJob.id == PrinceJobEntry.job_id)
            & (PrinceJob.tenant_id == PrinceJobEntry.tenant_id),
        )
        .join(
            PrinceCustomer,
            (PrinceCustomer.id == PrinceJob.customer_id)
            & (PrinceCustomer.tenant_id == PrinceJob.tenant_id),
        )
        .join(
            PrinceVehicle,
            (PrinceVehicle.id == PrinceJob.vehicle_id)
            & (PrinceVehicle.tenant_id == PrinceJob.tenant_id),
        )
        .outerjoin(
            PrinceEntryReview,
            (PrinceEntryReview.entry_id == PrinceJobEntry.id)
            & (PrinceEntryReview.tenant_id == PrinceJobEntry.tenant_id),
        )
        .filter(
            PrinceJobEntry.tenant_id == tenant.id,
            PrinceJob.status != "closed",
            PrinceEntryReview.id.is_(None),
        )
        .order_by(PrinceJobEntry.id.desc()).limit(12).all()
    )
    pending = [
        {
            "job_id": job_id,
            "entry_id": entry_id,
            "category": ENTRY_CATEGORIES.get(category, "Workshop entry"),
            "customer": full_name,
            "vehicle": f"{brand} {model}",
        }
        for entry_id, category, job_id, full_name, brand, model in review_queue
    ]
    return render_template(
        "partnerships/prince_management.html",
        workshop_name=tenant.display_name,
        slug=tenant.slug,
        stats={
            "total_jobs": total_jobs,
            "active_jobs": total_jobs - closed_jobs,
            "closed_jobs": closed_jobs,
            "customers": customers,
            "pending_reviews": pending_reviews,
            "operators": len(operators),
            "follow_up_count": follow_up_count,
        },
        jobs=jobs,
        operators=operators,
        pending=pending,
        follow_ups=follow_ups,
        active_principal_id=actor.user_id,
    )


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/assignment")
def principal_assign_job(slug, job_id):
    """Fail closed on stale assignment; one audit event per actual change."""
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.JOB_ASSIGN)
    if request.content_length and request.content_length > 1400:
        abort(413)
    data = request.get_json(silent=True)
    if (not isinstance(data, dict)
            or set(data) != {
                "assignee_user_id", "expected_assignee_user_id", "reason"
            }):
        abort(400)
    new_id = data["assignee_user_id"]
    expected_id = data["expected_assignee_user_id"]
    if (isinstance(new_id, bool) or not isinstance(new_id, int)
            or isinstance(expected_id, bool) or not isinstance(expected_id, int)
            or new_id <= 0 or expected_id <= 0):
        abort(400)
    reason = _text(data, "reason", 260, required=True)
    if len(reason) < 12 or PRIVATE_CONTACT_PATTERN.search(reason):
        abort(400)
    if (current_app.config.get("PRINCE_STAGING_LOCKDOWN", False)
            and not reason.lower().startswith(("demo:", "synthetic:"))):
        abort(400)

    # Reject forged target IDs, disabled users and outside-tenant members.
    eligible = {person["id"]: person for person in _eligible_operators(tenant.id)}
    if new_id not in eligible:
        abort(404)

    try:
        # PostgreSQL row lock and expected value prevent losing a principal's
        # concurrent assignment; SQLite tests also exercise compare-and-swap.
        job = (
            db.session.query(PrinceJob).filter_by(
                id=job_id, tenant_id=tenant.id,
            ).populate_existing().with_for_update().first()
        )
        if job is None:
            abort(404)
        if job.status == "closed":
            abort(409)
        if job.assigned_user_id != expected_id:
            abort(409)
        if new_id == expected_id:
            abort(409)
        before_id = job.assigned_user_id
        job.assigned_user_id = new_id
        event = PrinceJobAssignment(
            tenant_id=tenant.id,
            job_id=job.id,
            previous_user_id=before_id,
            new_user_id=new_id,
            changed_by_user_id=actor.user_id,
            reason=reason,
        )
        db.session.add(event)
        db.session.flush()
        _audit(tenant.id, actor.user_id, "job_assignment_changed", "job", job.id)
        response = {
            "job_id": job.id,
            "previous_assignee_user_id": before_id,
            "assigned_user_id": new_id,
            "assigned_operator_name": eligible[new_id]["name"],
            "assignment_id": event.id,
        }
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Job assignment could not be saved"}), 503
    return jsonify(response), 200


def _transition_response(event):
    return {
        "previous_status": event.previous_status,
        "new_status": event.new_status,
        "reason": _public_concern(event.reason),
        "closure_disposition": event.closure_disposition,
        "follow_up_plan": _public_concern(event.follow_up_plan) if event.follow_up_plan else None,
        "changed_by_user_id": event.changed_by_user_id,
        "created_at": event.created_at.isoformat(timespec="seconds") + "Z",
    }


def _closure_blockers(tenant_id, job_id):
    """Conservative administrative closure guard; not a diagnostic assessment."""
    records = (
        db.session.query(PrinceJobEntry, PrinceEntryReview)
        .outerjoin(
            PrinceEntryReview,
            (PrinceEntryReview.entry_id == PrinceJobEntry.id)
            & (PrinceEntryReview.tenant_id == PrinceJobEntry.tenant_id)
            & (PrinceEntryReview.job_id == PrinceJobEntry.job_id),
        )
        .filter(PrinceJobEntry.tenant_id == tenant_id, PrinceJobEntry.job_id == job_id)
        .all()
    )
    pending = sum(review is None for _, review in records)
    unresolved = any(
        entry.category in {"outstanding_concern", "temporary_intervention", "next_action"}
        or (review is not None and review.decision in {"needs_evidence", "not_confirmed"})
        or (entry.category == "reported_work" and
            (review is None or review.decision != "work_confirmed"))
        for entry, review in records
    )
    work_confirmed = any(
        entry.category == "reported_work"
        and review is not None and review.decision == "work_confirmed"
        for entry, review in records
    )
    return len(records), pending, unresolved, work_confirmed


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/lifecycle")
def principal_job_lifecycle(slug, job_id):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.JOB_TRANSITION)
    job, _, _ = _find_authorized_job(tenant, actor, job_id)
    events = PrinceJobTransition.query.filter_by(
        tenant_id=tenant.id, job_id=job.id,
    ).order_by(PrinceJobTransition.id.asc()).all()
    return jsonify({
        "job_id": job.id, "job_status": job.status,
        "next_status": JOB_NEXT_STATUS.get(job.status),
        "history": [_transition_response(event) for event in events],
    })


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/lifecycle")
def principal_transition_job(slug, job_id):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.JOB_TRANSITION)
    if request.content_length and request.content_length > 2500:
        abort(413)
    data = request.get_json(silent=True)
    if (not isinstance(data, dict) or set(data) != {
            "expected_status", "new_status", "reason", "closure_disposition", "follow_up_plan"}):
        abort(400)
    old, target = data["expected_status"], data["new_status"]
    disposition = data["closure_disposition"]
    if not isinstance(old, str) or not isinstance(target, str):
        abort(400)
    if JOB_NEXT_STATUS.get(old) != target:
        abort(409)
    reason = _text(data, "reason", 1000, required=True)
    if len(reason) < 20 or PRIVATE_CONTACT_PATTERN.search(reason):
        abort(400)
    if (current_app.config.get("PRINCE_STAGING_LOCKDOWN", False)
            and not reason.lower().startswith(("demo:", "synthetic:"))):
        abort(400)
    if target == "closed":
        if not isinstance(disposition, str) or disposition not in {
                "follow_up_required", "no_follow_up_identified"}:
            abort(400)
    elif disposition is not None:
        abort(400)
    follow_up_plan = _text(data, "follow_up_plan", 1000)
    if disposition == "follow_up_required":
        if not follow_up_plan or len(follow_up_plan) < 20 or PRIVATE_CONTACT_PATTERN.search(follow_up_plan):
            abort(400)
        if (current_app.config.get("PRINCE_STAGING_LOCKDOWN", False)
                and not follow_up_plan.lower().startswith(("demo:", "synthetic:"))):
            abort(400)
    elif follow_up_plan is not None:
        abort(400)

    try:
        job, _, _ = _find_authorized_job(tenant, actor, job_id, lock=True)
        if job.status != old:
            abort(409)
        if target == "in_progress" and job.assigned_user_id not in {
                person["id"] for person in _eligible_operators(tenant.id)}:
            return jsonify({"error": "Assign an active operator before starting work."}), 409
        if target == "in_progress" and _work_authorization(job) != "accepted":
            return jsonify({"error": "Record customer acceptance of the current approved estimate before starting work."}), 409
        if target == "closed":
            if (disposition == "no_follow_up_identified"
                    and _work_authorization(job) != "accepted"):
                return jsonify({"error": "Missing or withdrawn authorization requires documented follow-up."}), 409
            count, pending, unresolved, work_confirmed = _closure_blockers(tenant.id, job.id)
            if not count or pending:
                return jsonify({
                    "error": "Record and review workshop statements before closing.",
                }), 409
            if (disposition == "no_follow_up_identified"
                    and (unresolved or not work_confirmed)):
                return jsonify({
                    "error": "Recorded concerns or unconfirmed work require a documented follow-up.",
                }), 409
        event = PrinceJobTransition(
            tenant_id=tenant.id, job_id=job.id,
            previous_status=old, new_status=target, reason=reason,
            closure_disposition=disposition, follow_up_plan=follow_up_plan,
            changed_by_user_id=actor.user_id,
        )
        job.status = target
        db.session.add(event)
        db.session.flush()
        _audit(tenant.id, actor.user_id, "job_status_changed", "job", job.id)
        response = {
            "job_id": job.id, "job_status": target,
            "next_status": JOB_NEXT_STATUS.get(target),
            "event": _transition_response(event),
        }
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Job status could not be saved"}), 503
    return jsonify(response), 200


# Monetary inputs are exact integer kobo, never floating-point amounts.
ESTIMATE_MAX_KOBO = 10_000_000_000
ESTIMATE_CATEGORIES = {"parts": "Parts", "labour": "Labour", "other": "Other"}


def _estimate_amount(value, *, maximum=ESTIMATE_MAX_KOBO):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        abort(400)
    return value


def _estimate_latest(tenant_id, job_id):
    return PrinceEstimate.query.populate_existing().filter_by(
        tenant_id=tenant_id, job_id=job_id,
    ).order_by(PrinceEstimate.version.desc()).first()


def _estimate_response(record, *, principal=False):
    return {
        "version": record.version,
        "items": [
            {"category": item["category"], "description": _public_concern(item["description"]),
             "quantity": item["quantity"], "unit_kobo": item["unit_kobo"],
             "line_kobo": item["line_kobo"]}
            for item in record.items
        ],
        "subtotal_kobo": record.subtotal_kobo,
        "discount_kobo": record.discount_kobo,
        "total_kobo": record.total_kobo,
        "internally_approved": record.approved_at is not None,
        "created_at": record.created_at.isoformat(timespec="seconds") + "Z",
        **({"created_by_user_id": record.created_by_user_id,
            "approved_by_user_id": record.approved_by_user_id,
            "approval_note": _public_concern(record.approval_note),
            "approved_at": record.approved_at.isoformat(timespec="seconds") + "Z"
            if record.approved_at else None} if principal else {}),
    }


def _estimate_history(tenant, actor, job):
    records = PrinceEstimate.query.filter_by(
        tenant_id=tenant.id, job_id=job.id,
    ).order_by(PrinceEstimate.version.desc()).all()
    return [_estimate_response(record, principal=actor.role == PrinceRole.PRINCIPAL)
            for record in records]


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/estimate")
def estimate_workspace(slug, job_id):
    tenant, actor = _scope(slug)
    job, customer, vehicle = _find_authorized_job(tenant, actor, job_id)
    _allow(actor, tenant, PrinceAction.ESTIMATE_DRAFT, job.assigned_user_id)
    versions = _estimate_history(tenant, actor, job)
    authorization = []
    if actor.role == PrinceRole.PRINCIPAL and versions and versions[0]["internally_approved"]:
        authorization = [
            _customer_authorization_response(event) for event in
            _customer_authorization_events(tenant.id, job.id, versions[0]["version"])
        ]
    return render_template(
        "partnerships/prince_estimate.html", slug=slug,
        workshop_name=tenant.display_name,
        job=_job_response(job, customer, vehicle), versions=versions,
        latest=versions[0] if versions else None,
        principal=actor.role == PrinceRole.PRINCIPAL,
        categories=ESTIMATE_CATEGORIES,
        authorization=authorization,
        authorization_options=CUSTOMER_DECISIONS[authorization[0]["decision"] if authorization else "not_recorded"],
        authorization_channels=AUTHORIZATION_CHANNELS,
    )


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/estimates")
def estimate_history(slug, job_id):
    tenant, actor = _scope(slug)
    job, _, _ = _find_authorized_job(tenant, actor, job_id)
    _allow(actor, tenant, PrinceAction.ESTIMATE_DRAFT, job.assigned_user_id)
    return jsonify({"job_id": job.id, "versions": _estimate_history(tenant, actor, job)})


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/estimates")
def create_estimate_version(slug, job_id):
    tenant, actor = _scope(slug)
    job, _, _ = _find_authorized_job(tenant, actor, job_id, lock=True)
    _allow(actor, tenant, PrinceAction.ESTIMATE_DRAFT, job.assigned_user_id)
    if job.status == "closed":
        abort(409)
    if request.content_length and request.content_length > 24000:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"expected_version", "items", "discount_kobo"}:
        abort(400)
    expected = _estimate_amount(data["expected_version"], maximum=1_000_000)
    items = data["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 30:
        abort(400)
    cleaned = []
    subtotal = 0
    for item in items:
        if not isinstance(item, dict) or set(item) != {
                "category", "description", "quantity", "unit_kobo"}:
            abort(400)
        category = item["category"]
        if not isinstance(category, str) or category not in ESTIMATE_CATEGORIES:
            abort(400)
        description = _text(item, "description", 180, required=True)
        if len(description) < 3 or PRIVATE_CONTACT_PATTERN.search(description):
            abort(400)
        if (current_app.config.get("PRINCE_STAGING_LOCKDOWN", False)
                and not description.lower().startswith(("demo:", "synthetic:"))):
            abort(400)
        quantity = _estimate_amount(item["quantity"], maximum=1000)
        if quantity < 1:
            abort(400)
        unit = _estimate_amount(item["unit_kobo"])
        line = quantity * unit
        subtotal += line
        if subtotal > ESTIMATE_MAX_KOBO:
            abort(400)
        cleaned.append({"category": category, "description": description,
                        "quantity": quantity, "unit_kobo": unit, "line_kobo": line})
    discount = _estimate_amount(data["discount_kobo"])
    if discount > subtotal:
        abort(400)

    latest = _estimate_latest(tenant.id, job.id)
    if (latest.version if latest else 0) != expected:
        return jsonify({"error": "Estimate changed. Reload before saving."}), 409
    if latest and latest.approved_at is not None:
        return jsonify({"error": "Approved estimate is locked; revisions require a future correction workflow."}), 409
    try:
        record = PrinceEstimate(
            tenant_id=tenant.id, job_id=job.id, version=expected + 1,
            items=cleaned, subtotal_kobo=subtotal, discount_kobo=discount,
            total_kobo=subtotal - discount, created_by_user_id=actor.user_id,
        )
        db.session.add(record)
        db.session.flush()
        _audit(tenant.id, actor.user_id, "estimate_version_created", "estimate", record.id)
        response = _estimate_response(record, principal=actor.role == PrinceRole.PRINCIPAL)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Estimate could not be saved."}), 503
    return jsonify({"job_id": job.id, "estimate": response}), 201


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/estimates/<int:version>/approve")
def approve_estimate(slug, job_id, version):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.ESTIMATE_APPROVE)
    job, _, _ = _find_authorized_job(tenant, actor, job_id, lock=True)
    if job.status == "closed":
        abort(409)
    if request.content_length and request.content_length > 1800:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"approval_note"}:
        abort(400)
    note = _text(data, "approval_note", 700, required=True)
    if len(note) < 20 or PRIVATE_CONTACT_PATTERN.search(note):
        abort(400)
    if (current_app.config.get("PRINCE_STAGING_LOCKDOWN", False)
            and not note.lower().startswith(("demo:", "synthetic:"))):
        abort(400)
    record = _estimate_latest(tenant.id, job.id)
    if record is None or record.version != version or record.approved_at is not None:
        abort(409)
    try:
        record.approved_by_user_id = actor.user_id
        record.approved_at = datetime.utcnow()
        record.approval_note = note
        _audit(tenant.id, actor.user_id, "estimate_internally_approved", "estimate", record.id)
        response = _estimate_response(record, principal=True)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Estimate approval could not be saved."}), 503
    return jsonify({"job_id": job.id, "estimate": response})


def _finance_available():
    from partnerships.prince_live_portal import live_finance_ready
    return bool(current_app.config.get("PRINCE_STAGING_LOCKDOWN", False)
                or live_finance_ready())


def _finance_training():
    return current_app.config.get("PRINCE_STAGING_LOCKDOWN", False)


def _finance_ref(value):
    return f"DEMO-{value}" if _finance_training() else value


def _finance_note_valid(value):
    training_note = value.lower().startswith(("demo:", "synthetic:"))
    return training_note if _finance_training() else not training_note


def _finance_audit(action):
    return ("training_" if _finance_training() else "live_") + action


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/estimates/<int:version>/print")
def print_estimate(slug, job_id, version):
    """Principal-only, approved-version quotation preview for synthetic training."""
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.ESTIMATE_APPROVE)
    if not _finance_available():
        abort(404)
    job, customer, vehicle = _find_authorized_job(tenant, actor, job_id)
    record = _estimate_latest(tenant.id, job.id)
    if record is None or record.version != version or record.approved_at is None:
        abort(404)
    # Explicit projection: never include contact details, review notes,
    # account identifiers, banking credentials, tax IDs or technician notes.
    quotation = _estimate_response(record)
    response = current_app.make_response(render_template(
        "partnerships/prince_quotation_print.html",
        slug=slug, workshop_name=tenant.display_name,
        job={"job_id": job.id, "customer": _public_concern(customer.full_name),
             "brand": _public_concern(vehicle.brand),
             "model": _public_concern(vehicle.model), "year": vehicle.year},
        quotation=quotation,
        quotation_ref=_finance_ref(f"PAC-{job.id:06d}-V{record.version:02d}"),
        approved_date=record.approved_at.strftime("%d %B %Y"),
        categories=ESTIMATE_CATEGORIES,
    ))
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


CUSTOMER_DECISIONS = {
    "not_recorded": ("accepted", "declined"),
    "accepted": ("withdrawn",),
    "declined": ("accepted",),
    "withdrawn": ("accepted",),
}
AUTHORIZATION_CHANNELS = {
    "written_message": "Written message", "signed_document": "Signed document",
    "phone": "Phone conversation", "in_person": "In person",
}


def _customer_authorization_events(tenant_id, job_id, version):
    return PrinceCustomerAuthorization.query.populate_existing().filter_by(
        tenant_id=tenant_id, job_id=job_id, estimate_version=version,
    ).order_by(PrinceCustomerAuthorization.sequence.desc()).all()


def _customer_authorization_response(event):
    return {
        "sequence": event.sequence, "decision": event.decision,
        "channel": event.channel, "evidence_reference": _public_concern(event.evidence_reference),
        "total_kobo": event.total_kobo, "estimate_version": event.estimate_version,
        "recorded_by_user_id": event.recorded_by_user_id,
        "recorded_at": event.recorded_at.isoformat(timespec="seconds") + "Z",
    }


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/estimates/<int:version>/authorization")
def customer_authorization_history(slug, job_id, version):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.CUSTOMER_AUTHORIZATION)
    job, _, _ = _find_authorized_job(tenant, actor, job_id)
    estimate = _estimate_latest(tenant.id, job.id)
    if estimate is None or estimate.version != version or estimate.approved_at is None:
        abort(404)
    events = _customer_authorization_events(tenant.id, job.id, version)
    return jsonify({"state": events[0].decision if events else "not_recorded",
                    "events": [_customer_authorization_response(event) for event in events]})


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/estimates/<int:version>/authorization")
def record_customer_authorization(slug, job_id, version):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.CUSTOMER_AUTHORIZATION)
    job, _, _ = _find_authorized_job(tenant, actor, job_id, lock=True)
    from partnerships.prince_live_portal import live_authorization_ready
    training = current_app.config.get("PRINCE_STAGING_LOCKDOWN", False)
    if not training and not live_authorization_ready():
        abort(404)
    estimate = _estimate_latest(tenant.id, job.id)
    if estimate is None or estimate.version != version or estimate.approved_at is None:
        abort(404)
    if request.content_length and request.content_length > 2400:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {
        "expected_sequence", "decision", "channel", "evidence_reference", "customer_decision_attested"
    }:
        abort(400)
    if data["customer_decision_attested"] is not True:
        abort(400)
    expected = _estimate_amount(data["expected_sequence"], maximum=1_000_000)
    decision = _text(data, "decision", 16, required=True)
    channel = _text(data, "channel", 24, required=True)
    evidence = _text(data, "evidence_reference", 900, required=True)
    if (decision not in {"accepted", "declined", "withdrawn"}
            or channel not in AUTHORIZATION_CHANNELS or len(evidence) < 20
            or PRIVATE_CONTACT_PATTERN.search(evidence)
            or (training and not evidence.lower().startswith(("demo:", "synthetic:")))):
        abort(400)
    if not training and (channel not in {"written_message", "signed_document"}
                         or evidence.lower().startswith(("demo:", "synthetic:"))):
        abort(400)
    events = _customer_authorization_events(tenant.id, job.id, version)
    previous = events[0] if events else None
    if expected != (previous.sequence if previous else 0):
        abort(409)
    if decision not in CUSTOMER_DECISIONS[previous.decision if previous else "not_recorded"]:
        abort(409)
    if job.status == "closed" and decision != "withdrawn":
        abort(409)
    try:
        event = PrinceCustomerAuthorization(
            tenant_id=tenant.id, job_id=job.id, estimate_version=version,
            sequence=expected + 1, decision=decision, channel=channel,
            evidence_reference=evidence, total_kobo=estimate.total_kobo,
            recorded_by_user_id=actor.user_id,
        )
        db.session.add(event)
        db.session.flush()
        _audit(tenant.id, actor.user_id, "customer_authorization_recorded", "customer_authorization", event.id)
        result = _customer_authorization_response(event)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Authorization record could not be saved."}), 503
    return jsonify({"state": decision, "event": result}), 201


def _work_authorization(job):
    """Minimal operational state; never expose the private evidence or actor."""
    estimate = _estimate_latest(job.tenant_id, job.id)
    if estimate is None or estimate.approved_at is None:
        return "estimate_required"
    event = PrinceCustomerAuthorization.query.populate_existing().filter_by(
        tenant_id=job.tenant_id, job_id=job.id, estimate_version=estimate.version,
    ).order_by(PrinceCustomerAuthorization.sequence.desc()).first()
    if event is None:
        return "not_recorded"
    if event.total_kobo != estimate.total_kobo:
        return "not_recorded"
    return event.decision


def _invoice_for_job(job):
    return PrinceInvoice.query.populate_existing().filter_by(
        tenant_id=job.tenant_id, job_id=job.id,
    ).first()


def _invoice_response(record, job):
    estimate = _estimate_latest(job.tenant_id, job.id)
    events = _customer_authorization_events(job.tenant_id, job.id, record.estimate_version)
    current = events[0] if events else None
    authorization_current = bool(
        estimate and estimate.version == record.estimate_version
        and estimate.approved_at is not None and estimate.total_kobo == record.total_kobo
        and current and current.decision == "accepted"
        and current.sequence == record.authorization_sequence
        and current.total_kobo == record.total_kobo
    )
    return {
        "reference": _finance_ref(f"INV-PAC-{record.job_id:06d}"),
        "estimate_version": record.estimate_version,
        "authorization_sequence": record.authorization_sequence,
        "authorization_current": authorization_current,
        "items": [
            {"category": item["category"], "description": _public_concern(item["description"]),
             "quantity": item["quantity"], "unit_kobo": item["unit_kobo"],
             "line_kobo": item["line_kobo"]}
            for item in record.items
        ],
        "subtotal_kobo": record.subtotal_kobo, "discount_kobo": record.discount_kobo,
        "total_kobo": record.total_kobo, "issued_by_user_id": record.issued_by_user_id,
        "issue_reason": _public_concern(record.issue_reason),
        "issued_at": record.issued_at.isoformat(timespec="seconds") + "Z",
    }


def _invoice_scope(slug, job_id, *, lock=False):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.INVOICE_ISSUE)
    if not _finance_available():
        abort(404)
    job, customer, vehicle = _find_authorized_job(tenant, actor, job_id, lock=lock)
    return tenant, actor, job, customer, vehicle


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/invoice")
def invoice_workspace(slug, job_id):
    tenant, _, job, customer, vehicle = _invoice_scope(slug, job_id)
    record = _invoice_for_job(job)
    estimate = _estimate_latest(tenant.id, job.id)
    events = _customer_authorization_events(tenant.id, job.id, estimate.version) if estimate else []
    current = events[0] if events else None
    can_issue = bool(
        record is None and job.status in {"approved", "in_progress", "closed"}
        and _work_authorization(job) == "accepted"
    )
    return render_template(
        "partnerships/prince_invoice.html", slug=slug,
        workshop_name=tenant.display_name, job=_job_response(job, customer, vehicle),
        invoice=_invoice_response(record, job) if record else None,
        estimate=_estimate_response(estimate) if estimate else None,
        authorization_sequence=current.sequence if current else 0,
        can_issue=can_issue, categories=ESTIMATE_CATEGORIES,
        payments=_payment_summary(job, record) if record else None,
    )


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/invoice/data")
def invoice_detail(slug, job_id):
    _, _, job, _, _ = _invoice_scope(slug, job_id)
    record = _invoice_for_job(job)
    return jsonify({"invoice": _invoice_response(record, job) if record else None})


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/invoice")
def issue_invoice(slug, job_id):
    tenant, actor, job, _, _ = _invoice_scope(slug, job_id, lock=True)
    if request.content_length and request.content_length > 2400:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {
        "expected_version", "expected_authorization_sequence", "issue_reason", "issue_attested"
    }:
        abort(400)
    if data["issue_attested"] is not True:
        abort(400)
    expected_version = _estimate_amount(data["expected_version"], maximum=1_000_000)
    expected_sequence = _estimate_amount(data["expected_authorization_sequence"], maximum=1_000_000)
    reason = _text(data, "issue_reason", 900, required=True)
    if (len(reason) < 20 or PRIVATE_CONTACT_PATTERN.search(reason)
            or not _finance_note_valid(reason)):
        abort(400)
    if _invoice_for_job(job) is not None:
        return jsonify({"error": "An invoice already exists for this job."}), 409
    if job.status not in {"approved", "in_progress", "closed"}:
        return jsonify({"error": "Internal job approval is required before invoicing."}), 409
    estimate = _estimate_latest(tenant.id, job.id)
    if (estimate is None or estimate.approved_at is None or estimate.version != expected_version):
        abort(409)
    events = _customer_authorization_events(tenant.id, job.id, estimate.version)
    current = events[0] if events else None
    if (current is None or current.decision != "accepted"
            or current.sequence != expected_sequence or current.total_kobo != estimate.total_kobo):
        return jsonify({"error": "Current customer acceptance is required for this exact estimate."}), 409
    try:
        record = PrinceInvoice(
            tenant_id=tenant.id, job_id=job.id, estimate_version=estimate.version,
            authorization_sequence=current.sequence, items=[dict(item) for item in estimate.items],
            subtotal_kobo=estimate.subtotal_kobo, discount_kobo=estimate.discount_kobo,
            total_kobo=estimate.total_kobo, issued_by_user_id=actor.user_id, issue_reason=reason,
        )
        db.session.add(record)
        db.session.flush()
        _audit(tenant.id, actor.user_id, _finance_audit("invoice_created"), "invoice", record.id)
        result = _invoice_response(record, job)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Invoice could not be created."}), 503
    return jsonify({"invoice": result}), 201


def _payment_summary(job, invoice):
    records = PrincePayment.query.populate_existing().filter_by(
        tenant_id=job.tenant_id, job_id=job.id,
    ).order_by(PrincePayment.id.asc()).all()
    ids = [p.id for p in records]
    reversals = {r.payment_id: r for r in PrincePaymentReversal.query.filter(
        PrincePaymentReversal.payment_id.in_(ids)).all()} if ids else {}
    receipts = {r.payment_id: r for r in PrinceReceipt.query.filter(
        PrinceReceipt.payment_id.in_(ids)).all()} if ids else {}
    verified = sum(p.amount_kobo for p in records if p.decision == "verified" and p.id not in reversals)
    reversed_kobo = sum(p.amount_kobo for p in records if p.id in reversals)
    pending = sum(p.amount_kobo for p in records if p.decision is None)
    return {
        "invoice_total_kobo": invoice.total_kobo, "verified_kobo": verified,
        "pending_kobo": pending, "outstanding_kobo": invoice.total_kobo - verified,
        "reversed_kobo": reversed_kobo,
        "status": "settled" if verified == invoice.total_kobo else (
            "part_paid" if verified else "unpaid"
        ),
        "records": [{
            "id": p.id, "reference": p.reference, "method": p.method,
            "amount_kobo": p.amount_kobo, "decision": p.decision or "pending",
            "reversed": p.id in reversals,
            "reversal_reason": _public_concern(reversals[p.id].reason) if p.id in reversals else None,
            "reversed_at": reversals[p.id].reversed_at.isoformat(timespec="seconds") + "Z" if p.id in reversals else None,
            "reversed_by_user_id": reversals[p.id].reversed_by_user_id if p.id in reversals else None,
            "receipt_reference": _finance_ref(f"RCT-PAC-{receipts[p.id].id:06d}") if p.id in receipts else None,
            "reported_by_user_id": p.reported_by_user_id,
            "reported_at": p.reported_at.isoformat(timespec="seconds") + "Z",
            "reviewed_by_user_id": p.reviewed_by_user_id,
            "reviewed_at": p.reviewed_at.isoformat(timespec="seconds") + "Z" if p.reviewed_at else None,
            "review_note": _public_concern(p.review_note) if p.review_note else None,
        } for p in records],
    }


def _payment_scope(slug, job_id, *, lock=False):
    tenant, actor, job, _, _ = _invoice_scope(slug, job_id, lock=lock)
    _allow(actor, tenant, PrinceAction.PAYMENT_VERIFY)
    invoice = _invoice_for_job(job)
    if invoice is None:
        abort(404)
    return tenant, actor, job, invoice


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/invoice/payments")
def payment_history(slug, job_id):
    _, _, job, invoice = _payment_scope(slug, job_id)
    return jsonify(_payment_summary(job, invoice))


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/invoice/payments")
def report_payment(slug, job_id):
    tenant, actor, job, invoice = _payment_scope(slug, job_id, lock=True)
    if request.content_length and request.content_length > 1500:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"reference", "method", "amount_kobo"}:
        abort(400)
    reference = _text(data, "reference", 64, required=True).upper()
    pattern = (r"DEMO-[A-Z][A-Z0-9-]{2,47}" if _finance_training()
               else r"PAC-PAY-[A-Z][A-Z0-9-]{2,39}")
    if not re.fullmatch(pattern, reference):
        abort(400)
    method = _text(data, "method", 12, required=True)
    amount = _estimate_amount(data["amount_kobo"])
    if method not in {"cash", "transfer"} or amount <= 0:
        abort(400)
    if amount > invoice.total_kobo:
        return jsonify({"error": "Overpayments require a separate reconciliation workflow."}), 409
    if PrincePayment.query.filter_by(tenant_id=tenant.id, reference=reference).first():
        return jsonify({"error": "This payment reference has already been recorded."}), 409
    try:
        payment = PrincePayment(
            tenant_id=tenant.id, job_id=job.id, reference=reference,
            method=method, amount_kobo=amount, reported_by_user_id=actor.user_id,
        )
        db.session.add(payment)
        db.session.flush()
        _audit(tenant.id, actor.user_id, _finance_audit("payment_reported"), "payment", payment.id)
        result = _payment_summary(job, invoice)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Payment report not recorded; reload to check its reference."}), 409
    return jsonify(result), 201


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/invoice/payments/<int:payment_id>/review")
def review_payment(slug, job_id, payment_id):
    tenant, actor, job, invoice = _payment_scope(slug, job_id, lock=True)
    if request.content_length and request.content_length > 2400:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"decision", "review_note", "review_attested"}:
        abort(400)
    decision = _text(data, "decision", 12, required=True)
    note = _text(data, "review_note", 900, required=True)
    if (decision not in {"verified", "rejected"} or data["review_attested"] is not True
            or len(note) < 20 or PRIVATE_CONTACT_PATTERN.search(note)
            or not _finance_note_valid(note)):
        abort(400)
    payment = PrincePayment.query.populate_existing().filter_by(
        id=payment_id, tenant_id=tenant.id, job_id=job.id,
    ).first()
    if payment is None:
        abort(404)
    if payment.decision is not None:
        abort(409)
    summary = _payment_summary(job, invoice)
    if decision == "verified" and payment.amount_kobo > summary["outstanding_kobo"]:
        return jsonify({"error": "Payment exceeds the unallocated invoice balance; reconcile separately."}), 409
    try:
        payment.decision = decision
        payment.review_note = note
        payment.reviewed_by_user_id = actor.user_id
        payment.reviewed_at = datetime.utcnow()
        db.session.flush()
        _audit(tenant.id, actor.user_id, _finance_audit("payment_" + decision), "payment", payment.id)
        result = _payment_summary(job, invoice)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Payment review could not be saved."}), 503
    return jsonify(result)


def _receipt_payment(slug, job_id, payment_id, *, lock=False):
    tenant, actor, job, invoice = _payment_scope(slug, job_id, lock=lock)
    payment = PrincePayment.query.populate_existing().filter_by(
        id=payment_id, tenant_id=tenant.id, job_id=job.id,
    ).first()
    if payment is None:
        abort(404)
    return tenant, actor, job, invoice, payment


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/invoice/payments/<int:payment_id>/receipt")
def issue_receipt(slug, job_id, payment_id):
    tenant, actor, job, _, payment = _receipt_payment(slug, job_id, payment_id, lock=True)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"receipt_attested"} or data["receipt_attested"] is not True:
        abort(400)
    if (payment.decision != "verified"
            or PrincePaymentReversal.query.filter_by(payment_id=payment.id).first()
            or PrinceReceipt.query.filter_by(payment_id=payment.id).first()):
        abort(409)
    try:
        receipt = PrinceReceipt(payment_id=payment.id, issued_by_user_id=actor.user_id)
        db.session.add(receipt)
        db.session.flush()
        _audit(tenant.id, actor.user_id, _finance_audit("receipt_issued"), "receipt", receipt.id)
        result = {"reference": _finance_ref(f"RCT-PAC-{receipt.id:06d}"), "payment_id": payment.id}
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Receipt could not be issued."}), 503
    return jsonify(result), 201


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/invoice/payments/<int:payment_id>/reverse")
def reverse_payment_allocation(slug, job_id, payment_id):
    tenant, actor, job, invoice, payment = _receipt_payment(slug, job_id, payment_id, lock=True)
    if request.content_length and request.content_length > 2400:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"reason", "reversal_attested"}:
        abort(400)
    reason = _text(data, "reason", 900, required=True)
    if (data["reversal_attested"] is not True or len(reason) < 20
            or PRIVATE_CONTACT_PATTERN.search(reason)
            or not _finance_note_valid(reason)):
        abort(400)
    if payment.decision != "verified" or PrincePaymentReversal.query.filter_by(payment_id=payment.id).first():
        abort(409)
    try:
        reversal = PrincePaymentReversal(
            payment_id=payment.id, reversed_by_user_id=actor.user_id, reason=reason,
        )
        db.session.add(reversal)
        db.session.flush()
        _audit(tenant.id, actor.user_id, _finance_audit("payment_allocation_reversed"), "payment", payment.id)
        result = _payment_summary(job, invoice)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Reversal could not be recorded."}), 503
    return jsonify(result)


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/invoice/payments/<int:payment_id>/receipt/print")
def print_receipt(slug, job_id, payment_id):
    tenant, actor, job, invoice, payment = _receipt_payment(slug, job_id, payment_id)
    receipt = PrinceReceipt.query.filter_by(payment_id=payment.id).first()
    if receipt is None or payment.decision != "verified":
        abort(404)
    reversal = PrincePaymentReversal.query.filter_by(payment_id=payment.id).first()
    # Never include account identities, bank references, contact data or private review notes.
    _, customer, vehicle = _find_authorized_job(tenant, actor, job.id)
    response = current_app.make_response(render_template(
        "partnerships/prince_receipt_print.html", slug=slug, workshop_name=tenant.display_name,
        job_id=job.id, customer=_public_concern(customer.full_name),
        vehicle=f"{_public_concern(vehicle.brand)} {_public_concern(vehicle.model)}",
        reference=_finance_ref(f"RCT-PAC-{receipt.id:06d}"),
        invoice_reference=_finance_ref(f"INV-PAC-{invoice.job_id:06d}"),
        amount_kobo=payment.amount_kobo, method=payment.method,
        issued_date=receipt.issued_at.strftime("%d %B %Y"), reversed=bool(reversal),
    ))
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _financial_overview(tenant_id):
    """Single statement snapshot: aggregate payments before joining invoices."""
    payment = PrincePayment
    reversal = PrincePaymentReversal
    receipt = PrinceReceipt
    active = (payment.decision == "verified") & reversal.id.is_(None)
    rollup = (
        db.session.query(
            payment.job_id.label("job_id"),
            func.sum(case((active, payment.amount_kobo), else_=0)).label("verified"),
            func.sum(case((payment.decision.is_(None), payment.amount_kobo), else_=0)).label("pending"),
            func.sum(case((payment.decision == "rejected", payment.amount_kobo), else_=0)).label("rejected"),
            func.sum(case((reversal.id.isnot(None), payment.amount_kobo), else_=0)).label("reversed"),
            func.sum(case((receipt.id.isnot(None), 1), else_=0)).label("receipts"),
            func.sum(case((receipt.id.isnot(None) & reversal.id.isnot(None), 1), else_=0)).label("historical_receipts"),
            func.sum(case((active & receipt.id.is_(None), 1), else_=0)).label("receipt_pending"),
            func.sum(case((payment.decision.is_(None), 1), else_=0)).label("pending_count"),
        )
        .outerjoin(reversal, reversal.payment_id == payment.id)
        .outerjoin(receipt, receipt.payment_id == payment.id)
        .filter(payment.tenant_id == tenant_id)
        .group_by(payment.job_id).subquery()
    )
    auth = PrinceCustomerAuthorization
    latest = (
        db.session.query(auth.job_id.label("job_id"), auth.estimate_version.label("version"),
                         func.max(auth.sequence).label("sequence"))
        .filter(auth.tenant_id == tenant_id)
        .group_by(auth.job_id, auth.estimate_version).subquery()
    )
    invoice = PrinceInvoice
    amounts = ("verified", "pending", "rejected", "reversed", "receipts",
               "historical_receipts", "receipt_pending", "pending_count")
    rows = (
        db.session.query(
            invoice.job_id, invoice.total_kobo, PrinceCustomer.full_name,
            PrinceVehicle.brand, PrinceVehicle.model, PrinceJob.status,
            case(((auth.decision == "accepted")
                  & (auth.sequence == invoice.authorization_sequence)
                  & (auth.total_kobo == invoice.total_kobo), 1), else_=0).label("current"),
            *[func.coalesce(rollup.c[key], 0).label(key) for key in amounts],
        )
        .join(PrinceJob, (PrinceJob.id == invoice.job_id) & (PrinceJob.tenant_id == invoice.tenant_id))
        .join(PrinceCustomer, (PrinceCustomer.id == PrinceJob.customer_id)
              & (PrinceCustomer.tenant_id == PrinceJob.tenant_id))
        .join(PrinceVehicle, (PrinceVehicle.id == PrinceJob.vehicle_id)
              & (PrinceVehicle.tenant_id == PrinceJob.tenant_id))
        .outerjoin(rollup, rollup.c.job_id == invoice.job_id)
        .outerjoin(latest, (latest.c.job_id == invoice.job_id)
                   & (latest.c.version == invoice.estimate_version))
        .outerjoin(auth, (auth.job_id == invoice.job_id)
                   & (auth.tenant_id == invoice.tenant_id)
                   & (auth.estimate_version == latest.c.version)
                   & (auth.sequence == latest.c.sequence))
        .filter(invoice.tenant_id == tenant_id)
        .order_by(invoice.job_id.desc()).all()
    )
    totals = {key: 0 for key in amounts}
    totals.update(invoiced=0, outstanding=0, hold_outstanding=0, hold_count=0,
                  invoice_count=0, settled_count=0)
    details = []
    for row in rows:
        values = {key: int(getattr(row, key)) for key in amounts}
        outstanding = int(row.total_kobo) - values["verified"]
        for key, value in values.items():
            totals[key] += value
        totals["invoice_count"] += 1
        totals["invoiced"] += int(row.total_kobo)
        totals["outstanding"] += outstanding
        totals["settled_count"] += int(outstanding == 0)
        if not row.current:
            totals["hold_count"] += 1
            totals["hold_outstanding"] += outstanding
        details.append({
            "job_id": row.job_id, "reference": _finance_ref(f"INV-PAC-{row.job_id:06d}"),
            "customer": _public_concern(row.full_name),
            "vehicle": f"{_public_concern(row.brand)} {_public_concern(row.model)}",
            "job_status": row.status, "invoiced": int(row.total_kobo),
            "outstanding": outstanding, "authorization_current": bool(row.current),
            **values,
        })
    return {"totals": totals, "rows": details[:50], "shown": min(len(details), 50)}


@prince_pilot_bp.get("/<slug>/finance")
def principal_finance(slug):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.PAYMENT_VERIFY)
    if not _finance_available():
        abort(404)
    response = current_app.make_response(render_template(
        "partnerships/prince_finance.html", slug=slug, workshop_name=tenant.display_name,
        overview=_financial_overview(tenant.id),
    ))
    response.headers["Cache-Control"] = "no-store, private"
    return response


@prince_pilot_bp.get("/<slug>/finance/data")
def principal_finance_data(slug):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.PAYMENT_VERIFY)
    if not _finance_available():
        abort(404)
    response = jsonify(_financial_overview(tenant.id))
    response.headers["Cache-Control"] = "no-store, private"
    return response


CLINICAL_HANDOFF_NOTICE = "synthetic-clinical-handoff-v1"


def _clinical_scope(slug, job_id, *, lock=False):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.SERVICE_CONFIRM)
    if not current_app.config.get("PRINCE_STAGING_LOCKDOWN", False):
        abort(404)
    job, customer, vehicle = _find_authorized_job(tenant, actor, job_id, lock=lock)
    return tenant, actor, job, customer, vehicle


def _clinical_candidate(job):
    """Bounded principal attestation, never inferred from financial events."""
    if job.status != "closed":
        return None
    closure = PrinceJobTransition.query.filter_by(
        tenant_id=job.tenant_id, job_id=job.id, new_status="closed",
    ).first()
    if closure is None:
        return None
    records = (
        db.session.query(PrinceJobEntry, PrinceEntryReview)
        .outerjoin(PrinceEntryReview,
                   (PrinceEntryReview.entry_id == PrinceJobEntry.id)
                   & (PrinceEntryReview.tenant_id == PrinceJobEntry.tenant_id)
                   & (PrinceEntryReview.job_id == PrinceJobEntry.job_id))
        .filter(PrinceJobEntry.tenant_id == job.tenant_id, PrinceJobEntry.job_id == job.id)
        .order_by(PrinceJobEntry.id.asc()).all()
    )
    confirmed = []
    unresolved = []
    for entry, review in records:
        if not entry.body.lower().startswith(("demo:", "synthetic:")):
            return None
        if PRIVATE_CONTACT_PATTERN.search(entry.body):
            return None
        if review is None:
            return None
        if (entry.category == "reported_work" and review.decision == "work_confirmed"
                and review.basis in {"direct_inspection", "documented_evidence", "technician_demonstration"}):
            confirmed.append({
                "entry_id": entry.id, "review_id": review.id, "statement": entry.body,
                "basis": review.basis, "reviewed_at": review.reviewed_at.isoformat(timespec="seconds") + "Z",
            })
        elif (entry.category in {"temporary_intervention", "outstanding_concern", "next_action"}
              or review.decision in {"needs_evidence", "not_confirmed"}
              or entry.category == "reported_work"):
            unresolved.append({
                "entry_id": entry.id, "category": entry.category, "statement": entry.body,
                "review_decision": review.decision,
            })
    if not confirmed:
        return None
    plan = closure.follow_up_plan
    if plan and (PRIVATE_CONTACT_PATTERN.search(plan)
                 or not plan.lower().startswith(("demo:", "synthetic:"))):
        return None
    return {
        "schema_version": 1, "source_job_id": job.id, "source_vehicle_id": job.vehicle_id,
        "confirmed_work": confirmed, "unresolved_statements": unresolved,
        "closure_disposition": closure.closure_disposition,
        "follow_up_plan": plan, "outcome": "not_independently_assessed",
        "identity_status": "unlinked", "destination": "isolated_training_register",
    }


def _clinical_record(job):
    return PrinceClinicalHandoff.query.populate_existing().filter_by(
        tenant_id=job.tenant_id, job_id=job.id,
    ).first()


def _clinical_response(record):
    if record is None:
        return {"state": "not_recorded"}
    withdrawal = PrinceClinicalWithdrawal.query.populate_existing().filter_by(handoff_id=record.id).first()
    if withdrawal:
        return {
            "state": "withdrawn", "handoff_id": record.id,
            "withdrawn_at": withdrawal.recorded_at.isoformat(timespec="seconds") + "Z",
            "withdrawn_by_user_id": withdrawal.recorded_by_user_id,
            "withdrawal_reason": _public_concern(withdrawal.reason),
        }
    return {
        "state": "candidate", "handoff_id": record.id, "snapshot": record.snapshot,
        "consent_channel": record.consent_channel, "consent_notice_version": record.consent_notice_version,
        "consent_reference": _public_concern(record.consent_reference),
        "recorded_by_user_id": record.recorded_by_user_id,
        "recorded_at": record.recorded_at.isoformat(timespec="seconds") + "Z",
    }


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/clinical")
def clinical_workspace(slug, job_id):
    tenant, _, job, customer, vehicle = _clinical_scope(slug, job_id)
    record = _clinical_record(job)
    response = current_app.make_response(render_template(
        "partnerships/prince_clinical.html", slug=slug, workshop_name=tenant.display_name,
        job=_job_response(job, customer, vehicle), handoff=_clinical_response(record),
        candidate=_clinical_candidate(job) if record is None else None,
        notice=CLINICAL_HANDOFF_NOTICE,
    ))
    response.headers["Cache-Control"] = "no-store, private"
    return response


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/clinical/data")
def clinical_detail(slug, job_id):
    _, _, job, _, _ = _clinical_scope(slug, job_id)
    record = _clinical_record(job)
    response = jsonify({"handoff": _clinical_response(record),
                        "candidate": _clinical_candidate(job) if record is None else None})
    response.headers["Cache-Control"] = "no-store, private"
    return response


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/clinical")
def create_clinical_handoff(slug, job_id):
    tenant, actor, job, customer, _ = _clinical_scope(slug, job_id, lock=True)
    if request.content_length and request.content_length > 2500:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {
        "consent_channel", "consent_reference", "consent_notice_version", "consent_attested"
    }:
        abort(400)
    if (data["consent_attested"] is not True
            or data["consent_notice_version"] != CLINICAL_HANDOFF_NOTICE):
        abort(400)
    channel = _text(data, "consent_channel", 24, required=True)
    reference = _text(data, "consent_reference", 900, required=True)
    if (channel not in {"written_message", "signed_document"} or len(reference) < 20
            or PRIVATE_CONTACT_PATTERN.search(reference)
            or not reference.lower().startswith(("demo:", "synthetic:"))):
        abort(400)
    if _clinical_record(job) is not None or not customer.participation_confirmed:
        abort(409)
    snapshot = _clinical_candidate(job)
    if snapshot is None:
        return jsonify({"error": "A closed, reviewed job with confirmed fictional work is required."}), 409
    try:
        record = PrinceClinicalHandoff(
            tenant_id=tenant.id, job_id=job.id, snapshot=snapshot, consent_channel=channel,
            consent_reference=reference, consent_notice_version=CLINICAL_HANDOFF_NOTICE,
            recorded_by_user_id=actor.user_id,
        )
        db.session.add(record)
        db.session.flush()
        _audit(tenant.id, actor.user_id, "training_clinical_handoff_created", "clinical_handoff", record.id)
        result = _clinical_response(record)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Clinical candidate could not be recorded."}), 503
    return jsonify({"handoff": result}), 201


@prince_pilot_bp.post("/<slug>/jobs/<int:job_id>/clinical/withdraw")
def withdraw_clinical_handoff(slug, job_id):
    tenant, actor, job, _, _ = _clinical_scope(slug, job_id, lock=True)
    if request.content_length and request.content_length > 2500:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"reason", "withdrawal_attested"}:
        abort(400)
    reason = _text(data, "reason", 900, required=True)
    if (data["withdrawal_attested"] is not True or len(reason) < 20
            or PRIVATE_CONTACT_PATTERN.search(reason)
            or not reason.lower().startswith(("demo:", "synthetic:"))):
        abort(400)
    record = _clinical_record(job)
    if record is None or PrinceClinicalWithdrawal.query.filter_by(handoff_id=record.id).first():
        abort(409)
    try:
        event = PrinceClinicalWithdrawal(handoff_id=record.id,
                                         recorded_by_user_id=actor.user_id, reason=reason)
        db.session.add(event)
        db.session.flush()
        _audit(tenant.id, actor.user_id, "training_clinical_consent_withdrawn", "clinical_handoff", record.id)
        result = _clinical_response(record)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Withdrawal could not be recorded."}), 503
    return jsonify({"handoff": result})


@prince_pilot_bp.get("/<slug>/vehicles/<int:vehicle_id>/clinical-history")
def clinical_vehicle_history(slug, vehicle_id):
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.SERVICE_CONFIRM)
    if not current_app.config.get("PRINCE_STAGING_LOCKDOWN", False):
        abort(404)
    vehicle = PrinceVehicle.query.filter_by(id=vehicle_id, tenant_id=tenant.id).first()
    if vehicle is None:
        abort(404)
    records = (
        db.session.query(PrinceClinicalHandoff)
        .join(PrinceJob, (PrinceJob.id == PrinceClinicalHandoff.job_id)
              & (PrinceJob.tenant_id == PrinceClinicalHandoff.tenant_id))
        .outerjoin(PrinceClinicalWithdrawal, PrinceClinicalWithdrawal.handoff_id == PrinceClinicalHandoff.id)
        .filter(PrinceClinicalHandoff.tenant_id == tenant.id, PrinceJob.vehicle_id == vehicle.id,
                PrinceClinicalWithdrawal.id.is_(None))
        .order_by(PrinceClinicalHandoff.id.desc()).all()
    )
    response = jsonify({"vehicle_id": vehicle.id, "identity_status": "unlinked",
                        "candidates": [r.snapshot for r in records]})
    response.headers["Cache-Control"] = "no-store, private"
    return response
