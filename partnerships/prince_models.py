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


class PrinceClinicalHandoff(db.Model):
    """Closed-job, synthetic consent-bound clinical candidate, not a VehicleEvent."""
    __tablename__ = "prince_clinical_handoffs"
    __table_args__ = (
        db.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_handoff_job_tenant", ondelete="RESTRICT",
        ),
        db.UniqueConstraint("job_id", name="uq_prince_handoff_job"),
        db.CheckConstraint(
            "consent_channel IN ('written_message', 'signed_document')",
            name="ck_prince_handoff_consent_channel",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("prince_tenants.id", ondelete="RESTRICT"),
                          nullable=False, index=True)
    job_id = db.Column(db.Integer, nullable=False, index=True)
    snapshot = db.Column(db.JSON, nullable=False)
    consent_channel = db.Column(db.String(24), nullable=False)
    consent_reference = db.Column(db.Text, nullable=False)
    consent_notice_version = db.Column(db.String(48), nullable=False)
    recorded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
                                    nullable=False)
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrinceClinicalWithdrawal(db.Model):
    """Separate immutable withdrawal; blocks active use without erasing audit history."""
    __tablename__ = "prince_clinical_withdrawals"
    id = db.Column(db.Integer, primary_key=True)
    handoff_id = db.Column(db.Integer, db.ForeignKey("prince_clinical_handoffs.id", ondelete="RESTRICT"),
                           nullable=False, unique=True)
    recorded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
                                    nullable=False)
    reason = db.Column(db.Text, nullable=False)
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrinceReceipt(db.Model):
    """One training receipt per verified payment. Reversal never deletes it."""
    __tablename__ = "prince_receipts"
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("prince_payments.id", ondelete="RESTRICT"),
                           nullable=False, unique=True)
    issued_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
                                   nullable=False)
    issued_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrincePaymentReversal(db.Model):
    """Full allocation reversal, not a refund or money transfer."""
    __tablename__ = "prince_payment_reversals"
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("prince_payments.id", ondelete="RESTRICT"),
                           nullable=False, unique=True)
    reversed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
                                    nullable=False)
    reason = db.Column(db.Text, nullable=False)
    reversed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrincePayment(db.Model):
    """Synthetic payment report with a one-time principal review; not a bank feed."""
    __tablename__ = "prince_payments"
    __table_args__ = (
        db.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_payment_job_tenant", ondelete="RESTRICT",
        ),
        db.ForeignKeyConstraint(
            ["job_id"], ["prince_invoices.job_id"],
            name="fk_prince_payment_invoice_job", ondelete="RESTRICT",
        ),
        db.UniqueConstraint("tenant_id", "reference", name="uq_prince_payment_reference"),
        db.CheckConstraint("amount_kobo > 0", name="ck_prince_payment_amount"),
        db.CheckConstraint("method IN ('transfer', 'cash')", name="ck_prince_payment_method"),
        db.CheckConstraint(
            "(decision IS NULL AND reviewed_by_user_id IS NULL AND reviewed_at IS NULL AND review_note IS NULL) OR "
            "(decision IS NOT NULL AND decision IN ('verified', 'rejected') AND "
            "reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL AND review_note IS NOT NULL)",
            name="ck_prince_payment_review",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("prince_tenants.id", ondelete="RESTRICT"),
                          nullable=False, index=True)
    job_id = db.Column(db.Integer, nullable=False, index=True)
    reference = db.Column(db.String(64), nullable=False)
    method = db.Column(db.String(12), nullable=False)
    amount_kobo = db.Column(db.BigInteger, nullable=False)
    reported_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
                                    nullable=False)
    reported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    decision = db.Column(db.String(12))
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_at = db.Column(db.DateTime)
    review_note = db.Column(db.Text)


class PrinceInvoice(db.Model):
    """Fictional pilot invoice snapshot; never a payment or work confirmation."""
    __tablename__ = "prince_invoices"
    __table_args__ = (
        db.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_invoice_job_tenant", ondelete="RESTRICT",
        ),
        db.ForeignKeyConstraint(
            ["job_id", "estimate_version", "authorization_sequence"],
            ["prince_customer_authorizations.job_id",
             "prince_customer_authorizations.estimate_version",
             "prince_customer_authorizations.sequence"],
            name="fk_prince_invoice_authorization", ondelete="RESTRICT",
        ),
        db.UniqueConstraint("job_id", name="uq_prince_invoice_job"),
        db.CheckConstraint("estimate_version > 0 AND authorization_sequence > 0",
                           name="ck_prince_invoice_source"),
        db.CheckConstraint(
            "subtotal_kobo >= 0 AND discount_kobo >= 0 AND discount_kobo <= subtotal_kobo "
            "AND total_kobo = subtotal_kobo - discount_kobo",
            name="ck_prince_invoice_totals",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("prince_tenants.id", ondelete="RESTRICT"),
                          nullable=False, index=True)
    job_id = db.Column(db.Integer, nullable=False, index=True)
    estimate_version = db.Column(db.Integer, nullable=False)
    authorization_sequence = db.Column(db.Integer, nullable=False)
    items = db.Column(db.JSON, nullable=False)
    subtotal_kobo = db.Column(db.BigInteger, nullable=False)
    discount_kobo = db.Column(db.BigInteger, nullable=False)
    total_kobo = db.Column(db.BigInteger, nullable=False)
    issued_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
                                  nullable=False)
    issue_reason = db.Column(db.Text, nullable=False)
    issued_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrinceCustomerAuthorization(db.Model):
    """Principal-recorded customer decision, distinct from internal approval."""
    __tablename__ = "prince_customer_authorizations"
    __table_args__ = (
        db.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_authorization_job_tenant", ondelete="RESTRICT",
        ),
        db.ForeignKeyConstraint(
            ["job_id", "estimate_version"], ["prince_estimates.job_id", "prince_estimates.version"],
            name="fk_prince_authorization_estimate_version", ondelete="RESTRICT",
        ),
        db.UniqueConstraint("job_id", "estimate_version", "sequence",
                            name="uq_prince_authorization_sequence"),
        db.CheckConstraint("sequence > 0 AND total_kobo >= 0",
                           name="ck_prince_authorization_amount_sequence"),
        db.CheckConstraint("decision IN ('accepted', 'declined', 'withdrawn')",
                           name="ck_prince_authorization_decision"),
        db.CheckConstraint("channel IN ('written_message', 'signed_document', 'phone', 'in_person')",
                           name="ck_prince_authorization_channel"),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("prince_tenants.id", ondelete="RESTRICT"),
                          nullable=False, index=True)
    job_id = db.Column(db.Integer, nullable=False, index=True)
    estimate_version = db.Column(db.Integer, nullable=False)
    sequence = db.Column(db.Integer, nullable=False)
    decision = db.Column(db.String(16), nullable=False)
    channel = db.Column(db.String(24), nullable=False)
    evidence_reference = db.Column(db.Text, nullable=False)
    total_kobo = db.Column(db.BigInteger, nullable=False)
    recorded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
                                    nullable=False)
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrinceEstimate(db.Model):
    """Versioned NGN quotation, not an invoice or proof of authorization."""
    __tablename__ = "prince_estimates"
    __table_args__ = (
        db.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_estimate_job_tenant", ondelete="RESTRICT",
        ),
        db.UniqueConstraint("job_id", "version", name="uq_prince_estimate_job_version"),
        db.CheckConstraint("version > 0", name="ck_prince_estimate_version"),
        db.CheckConstraint(
            "subtotal_kobo >= 0 AND discount_kobo >= 0 AND "
            "discount_kobo <= subtotal_kobo AND total_kobo = subtotal_kobo - discount_kobo",
            name="ck_prince_estimate_amounts",
        ),
        db.CheckConstraint(
            "(approved_by_user_id IS NULL AND approved_at IS NULL AND approval_note IS NULL) OR "
            "(approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL AND approval_note IS NOT NULL)",
            name="ck_prince_estimate_approval",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("prince_tenants.id", ondelete="RESTRICT"),
                          nullable=False, index=True)
    job_id = db.Column(db.Integer, nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    items = db.Column(db.JSON, nullable=False)
    subtotal_kobo = db.Column(db.BigInteger, nullable=False)
    discount_kobo = db.Column(db.BigInteger, nullable=False)
    total_kobo = db.Column(db.BigInteger, nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
                                   nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"))
    approved_at = db.Column(db.DateTime)
    approval_note = db.Column(db.Text)


class PrinceJobTransition(db.Model):
    """Append-only principal decision; status is not repair or payment proof."""
    __tablename__ = "prince_job_transitions"
    __table_args__ = (
        db.ForeignKeyConstraint(
            ["job_id", "tenant_id"], ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_transition_job_tenant", ondelete="RESTRICT",
        ),
        db.CheckConstraint(
            "(previous_status = 'draft' AND new_status = 'awaiting_approval') OR "
            "(previous_status = 'awaiting_approval' AND new_status = 'approved') OR "
            "(previous_status = 'approved' AND new_status = 'in_progress') OR "
            "(previous_status = 'in_progress' AND new_status = 'closed')",
            name="ck_prince_transition_path",
        ),
        db.CheckConstraint(
            "(new_status = 'closed' AND closure_disposition IS NOT NULL AND "
            "closure_disposition IN ('follow_up_required', 'no_follow_up_identified')) OR "
            "(new_status <> 'closed' AND closure_disposition IS NULL)",
            name="ck_prince_transition_closure",
        ),
        db.CheckConstraint(
            "(closure_disposition = 'follow_up_required' AND follow_up_plan IS NOT NULL) OR "
            "((closure_disposition IS NULL OR closure_disposition <> 'follow_up_required') "
            "AND follow_up_plan IS NULL)",
            name="ck_prince_transition_follow_up",
        ),
        db.UniqueConstraint("job_id", "new_status", name="uq_prince_transition_job_stage"),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey(
        "prince_tenants.id", ondelete="RESTRICT"
    ), nullable=False, index=True)
    job_id = db.Column(db.Integer, nullable=False, index=True)
    previous_status = db.Column(db.String(24), nullable=False)
    new_status = db.Column(db.String(24), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    closure_disposition = db.Column(db.String(32))
    follow_up_plan = db.Column(db.Text)
    changed_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PrinceJobAssignment(db.Model):
    """Immutable reassignment event; no customer contacts or banking data."""
    __tablename__ = "prince_job_assignments"
    __table_args__ = (
        db.ForeignKeyConstraint(
            ["job_id", "tenant_id"],
            ["prince_jobs.id", "prince_jobs.tenant_id"],
            name="fk_prince_assignment_job_tenant", ondelete="RESTRICT",
        ),
        db.ForeignKeyConstraint(
            ["tenant_id", "new_user_id"],
            ["prince_memberships.tenant_id", "prince_memberships.user_id"],
            name="fk_prince_assignment_member_tenant", ondelete="RESTRICT",
        ),
        db.CheckConstraint(
            "previous_user_id <> new_user_id",
            name="ck_prince_assignment_different_users",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("prince_tenants.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    job_id = db.Column(db.Integer, nullable=False, index=True)
    previous_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    new_user_id = db.Column(db.Integer, nullable=False)
    changed_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reason = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


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
