"""Disabled-by-default Prince pilot JSON endpoints.

This API is intentionally NOT wired to global Aura owner, vehicle, evidence,
billing or assistant endpoints. Authorization is resolved from DB membership,
not caller-supplied role, tenant IDs or assignments.
"""
from __future__ import annotations

import re

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
    PrinceAudit, PrinceCustomer, PrinceEntryReview, PrinceJob, PrinceJobAssignment,
    PrinceJobEntry, PrinceMembership, PrinceTenant, PrinceVehicle,
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


def _find_authorized_job(tenant, actor, job_id):
    record = (
        db.session.query(PrinceJob, PrinceCustomer, PrinceVehicle)
        .join(PrinceCustomer, (PrinceCustomer.id == PrinceJob.customer_id) &
                              (PrinceCustomer.tenant_id == PrinceJob.tenant_id))
        .join(PrinceVehicle, (PrinceVehicle.id == PrinceJob.vehicle_id) &
                             (PrinceVehicle.tenant_id == PrinceJob.tenant_id))
        .filter(PrinceJob.id == job_id, PrinceJob.tenant_id == tenant.id)
        .first()
    )
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
    job, _, _ = _find_authorized_job(tenant, actor, job_id)
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
    job, _, _ = _find_authorized_job(tenant, actor, job_id)
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
        .outerjoin(
            PrinceEntryReview,
            (PrinceEntryReview.entry_id == PrinceJobEntry.id)
            & (PrinceEntryReview.tenant_id == PrinceJobEntry.tenant_id),
        )
        .filter(
            PrinceJobEntry.tenant_id == tenant.id,
            PrinceEntryReview.id.is_(None),
        )
        .scalar() or 0
    )
    operators = _eligible_operators(tenant.id)

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
        },
        jobs=jobs,
        operators=operators,
        pending=pending,
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
            ).with_for_update().first()
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
