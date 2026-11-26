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
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import User
from security.field_encryption import (
    ProfileEncryptionError, decrypt_profile_value, encrypt_profile_value,
)
from partnerships.prince_access_policy import (
    PrinceAction, PrinceActor, PrinceRole, authorize, operator_intake_response,
    redact_operator_job,
)
from partnerships.prince_models import (
    PrinceAudit, PrinceCustomer, PrinceCustomerAuthorization, PrinceEntryReview,
    PrinceEstimate, PrinceJob, PrinceJobAssignment,
    PrinceJobEntry, PrinceJobTransition, PrinceMembership, PrinceTenant, PrinceVehicle,
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


@prince_pilot_bp.before_request
def pilot_access_gate():
    if not current_app.config.get("AURA_PRINCE_PILOT_API_ENABLED", False):
        abort(404)
    if not current_user.is_authenticated or not current_user.is_active:
        abort(401)


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
    if not isinstance(data, dict) or set(data) - INTAKE_FIELDS:
        abort(400)
    full_name = _text(data, "full_name", 120, required=True)
    if len(full_name) < 2:
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
    if not brand and (year is not None or concern is not None):
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
                brand=brand, model=model, year=year, vin=None,
            )
            db.session.add(vehicle)
            db.session.flush()
            job = PrinceJob(
                tenant_id=tenant.id, customer_id=customer.id,
                vehicle_id=vehicle.id, assigned_user_id=actor.user_id,
                reported_concern=concern, status="draft",
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
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Intake could not be saved"}), 503
    return jsonify(response), 201


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
    for job in jobs:
        job["entries"] = by_job[job["job_id"]]
    return render_template(
        "partnerships/prince_operator.html",
        workshop_name=tenant.display_name,
        slug=tenant.slug,
        jobs=jobs,
        entry_categories=ENTRY_CATEGORIES,
        privacy_notice_version=tenant.approved_privacy_notice_version,
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
        if target == "closed":
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


@prince_pilot_bp.get("/<slug>/jobs/<int:job_id>/estimates/<int:version>/print")
def print_estimate(slug, job_id, version):
    """Principal-only, approved-version quotation preview for synthetic training."""
    tenant, actor = _scope(slug)
    _allow(actor, tenant, PrinceAction.ESTIMATE_APPROVE)
    if not current_app.config.get("PRINCE_STAGING_LOCKDOWN", False):
        abort(404)  # Real customer document issuance requires separate approval.
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
        quotation_ref=f"DEMO-PAC-{job.id:06d}-V{record.version:02d}",
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
    if not current_app.config.get("PRINCE_STAGING_LOCKDOWN", False):
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
            or not evidence.lower().startswith(("demo:", "synthetic:"))):
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
