"""Isolated, tenant-scoped Prince workshop pilot persistence.

Never put Prince intake records into global Aura User or Car tables.
Private contact details are encrypted at rest and cannot be directly joined to
existing owner/advisor endpoints.
"""
from __future__ import annotations

from datetime import datetime

from extensions import db


class PrinceTenant(db.Model):
    __tablename__ = "prince_tenants"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(64), nullable=False, unique=True)
    display_name = db.Column(db.String(160), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=False)
    intake_enabled = db.Column(db.Boolean, nullable=False, default=False)
    approved_privacy_notice_version = db.Column(db.String(48))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrinceMembership(db.Model):
    __tablename__ = "prince_memberships"
    __table_args__ = (
        db.UniqueConstraint("tenant_id", "user_id", name="uq_prince_member_tenant_user"),
        db.CheckConstraint(
            "role IN ('principal', 'operator', 'pilot_admin')",
            name="ck_prince_member_role",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("prince_tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrinceCustomer(db.Model):
    __tablename__ = "prince_customers"
    __table_args__ = (
        db.UniqueConstraint("id", "tenant_id", name="uq_prince_customer_id_tenant"),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("prince_tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    phone_ciphertext = db.Column(db.Text)
    email_ciphertext = db.Column(db.Text)
    address_ciphertext = db.Column(db.Text)
    intake_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    privacy_notice_version = db.Column(db.String(48), nullable=False)
    participation_confirmed = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrinceVehicle(db.Model):
    __tablename__ = "prince_vehicles"
    __table_args__ = (
        db.UniqueConstraint("id", "tenant_id", name="uq_prince_vehicle_id_tenant"),
        db.UniqueConstraint("tenant_id", "vin", name="uq_prince_tenant_vin"),
        db.ForeignKeyConstraint(
            ["customer_id", "tenant_id"],
            ["prince_customers.id", "prince_customers.tenant_id"],
            name="fk_prince_vehicle_customer_tenant",
            ondelete="RESTRICT",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("prince_tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    customer_id = db.Column(db.Integer, nullable=False)
    brand = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(100), nullable=False)
    year = db.Column(db.Integer)
    vin = db.Column(db.String(50))  # Unknown means NULL; never fabricate VIN.
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrinceJob(db.Model):
    __tablename__ = "prince_jobs"
    __table_args__ = (
        db.UniqueConstraint("id", "tenant_id", name="uq_prince_job_id_tenant"),
        db.ForeignKeyConstraint(
            ["customer_id", "tenant_id"],
            ["prince_customers.id", "prince_customers.tenant_id"],
            name="fk_prince_job_customer_tenant",
            ondelete="RESTRICT",
        ),
        db.ForeignKeyConstraint(
            ["vehicle_id", "tenant_id"],
            ["prince_vehicles.id", "prince_vehicles.tenant_id"],
            name="fk_prince_job_vehicle_tenant",
            ondelete="RESTRICT",
        ),
        db.CheckConstraint(
            "status IN ('draft', 'awaiting_approval', 'approved', 'in_progress', 'closed')",
            name="ck_prince_job_status",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("prince_tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    customer_id = db.Column(db.Integer, nullable=False)
    vehicle_id = db.Column(db.Integer, nullable=False)
    assigned_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    reported_concern = db.Column(db.Text)
    status = db.Column(db.String(24), nullable=False, default="draft")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class PrinceJobEntry(db.Model):
    """Append-only, source-labelled workshop statement; never proof of repair."""
    __tablename__ = "prince_job_entries"
    __table_args__ = (
        db.UniqueConstraint("id", "tenant_id", name="uq_prince_entry_id_tenant"),
        db.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_entry_job_tenant", ondelete="RESTRICT",
        ),
        db.CheckConstraint(
            "category IN ('customer_complaint', 'technician_observation', "
            "'diagnostic_finding', 'temporary_intervention', 'reported_work', "
            "'outstanding_concern', 'next_action')",
            name="ck_prince_entry_category",
        ),
        db.CheckConstraint(
            "review_state = 'unverified'",
            name="ck_prince_entry_review_state",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey(
        "prince_tenants.id", ondelete="RESTRICT"
    ), nullable=False, index=True)
    job_id = db.Column(db.Integer, nullable=False, index=True)
    category = db.Column(db.String(36), nullable=False)
    body = db.Column(db.Text, nullable=False)
    review_state = db.Column(db.String(16), nullable=False, default="unverified")
    recorded_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrinceEntryReview(db.Model):
    """Append-only principal attestation about an existing workshop entry."""
    __tablename__ = "prince_entry_reviews"
    __table_args__ = (
        db.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_review_job_tenant", ondelete="RESTRICT",
        ),
        db.ForeignKeyConstraint(
            ["entry_id", "tenant_id"],
            ["prince_job_entries.id", "prince_job_entries.tenant_id"],
            name="fk_prince_review_entry_tenant", ondelete="RESTRICT",
        ),
        db.CheckConstraint(
            "decision IN ('acknowledged', 'needs_evidence', 'work_confirmed', 'not_confirmed')",
            name="ck_prince_review_decision",
        ),
        db.CheckConstraint(
            "basis IN ('direct_inspection', 'documented_evidence', 'technician_demonstration', 'not_applicable')",
            name="ck_prince_review_basis",
        ),
        db.UniqueConstraint("entry_id", name="uq_prince_single_entry_review"),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey(
        "prince_tenants.id", ondelete="RESTRICT"
    ), nullable=False, index=True)
    job_id = db.Column(db.Integer, nullable=False)
    entry_id = db.Column(db.Integer, nullable=False, index=True)
    decision = db.Column(db.String(24), nullable=False)
    basis = db.Column(db.String(36), nullable=False)
    review_note = db.Column(db.Text, nullable=False)
    reviewed_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reviewed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrinceAudit(db.Model):
    __tablename__ = "prince_audit_events"
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("prince_tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    action = db.Column(db.String(48), nullable=False)
    target_kind = db.Column(db.String(48), nullable=False)
    target_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
