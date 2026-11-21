"""Disabled-by-default Prince pilot JSON endpoints.

This API is intentionally NOT wired to global Aura owner, vehicle, evidence,
billing or assistant endpoints. Authorization is resolved from DB membership,
not caller-supplied role, tenant IDs or assignments.
"""
from __future__ import annotations

import re

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from security.field_encryption import ProfileEncryptionError, encrypt_profile_value
from partnerships.prince_access_policy import (
    PrinceAction, PrinceActor, PrinceRole, authorize, operator_intake_response,
    redact_operator_job,
)
from partnerships.prince_models import (
    PrinceAudit, PrinceCustomer, PrinceJob, PrinceMembership, PrinceTenant,
    PrinceVehicle,
)

prince_pilot_bp = Blueprint("prince_pilot", __name__, url_prefix="/pilot/workshops")

PRIVATE_CONTACT_PATTERN = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)"
)

INTAKE_FIELDS = frozenset({
    "full_name", "phone_number", "email", "home_address", "vehicle_brand",
    "vehicle_model", "vehicle_year", "reported_concern",
    "privacy_notice_version", "participation_confirmed",
})


@prince_pilot_bp.before_request
def pilot_access_gate():
    if not current_app.config.get("AURA_PRINCE_PILOT_API_ENABLED", False):
        abort(404)
    if not current_user.is_authenticated or not current_user.is_active:
        abort(401)


def _scope(slug: str):
    tenant = PrinceTenant.query.filter_by(slug=slug, active=True).first()
    if tenant is None:
        abort(404)
    member = PrinceMembership.query.filter_by(
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

    phone = _text(data, "phone_number", 30)
    email = _text(data, "email", 200)
    address = _text(data, "home_address", 400)
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
        if phone not in (None, "DEMO CONTACT ONLY"):
            abort(400)
        if email not in (None, "demo@example.invalid"):
            abort(400)
        if address not in (None, "DEMO ADDRESS ONLY"):
            abort(400)

    # Encrypt *before* writing. No logging of request bodies or contact fields.
    try:
        encrypted_phone = encrypt_profile_value(phone)
        encrypted_email = encrypt_profile_value(email)
        encrypted_address = encrypt_profile_value(address)
    except ProfileEncryptionError:
        return jsonify({"error": "Secure intake unavailable"}), 503

    customer = PrinceCustomer(
        tenant_id=tenant.id, full_name=full_name,
        phone_ciphertext=encrypted_phone, email_ciphertext=encrypted_email,
        address_ciphertext=encrypted_address,
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


@prince_pilot_bp.patch("/<slug>/jobs/<int:job_id>/concern")
def update_reported_concern(slug, job_id):
    tenant, actor = _scope(slug)
    job, _, _ = _find_authorized_job(tenant, actor, job_id)
    _allow(actor, tenant, PrinceAction.JOB_EDIT, job.assigned_user_id)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"reported_concern"}:
        abort(400)
    concern = _text(data, "reported_concern", 1200, required=True)
    if PRIVATE_CONTACT_PATTERN.search(concern):
        abort(400)
    if job.status == "closed":
        abort(403)
    job.reported_concern = concern
    _audit(tenant.id, actor.user_id, "reported_concern_updated", "job", job.id)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Update could not be saved"}), 503
    return jsonify({"job_id": job.id, "updated": True})


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
    return render_template(
        "partnerships/prince_operator.html",
        workshop_name=tenant.display_name,
        slug=tenant.slug,
        jobs=jobs,
        privacy_notice_version=tenant.approved_privacy_notice_version,
        can_intake=bool(tenant.intake_enabled and
                        tenant.approved_privacy_notice_version),
    )
