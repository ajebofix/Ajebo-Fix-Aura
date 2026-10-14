"""Restricted Gulf pilot operations records; deliberately distinct from owner Car.

An unregistered campaign visitor is NOT a User, Car or CarOwnership. The
voucher and customer's contact details are encrypted with Aura's existing
rotatable Fernet field keys. Voucher digest is keyed for duplicate detection.
"""
from datetime import datetime

from extensions import db
from security.field_encryption import encrypt_profile_value, decrypt_profile_value


class GulfTechnician(db.Model):
    __tablename__ = "gulf_technicians"
    id = db.Column(db.Integer, primary_key=True)
    display_name = db.Column(db.String(100), nullable=False)
    phone_cipher = db.Column(db.Text)
    qualified_classes = db.Column(db.String(100), nullable=False, default="standard")
    is_intern = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def phone(self):
        return decrypt_profile_value(self.phone_cipher)

    @phone.setter
    def phone(self, value):
        self.phone_cipher = encrypt_profile_value(value)


class GulfShift(db.Model):
    __tablename__ = "gulf_shifts"
    id = db.Column(db.Integer, primary_key=True)
    technician_id = db.Column(db.Integer, db.ForeignKey("gulf_technicians.id"), nullable=False, index=True)
    starts_at = db.Column(db.DateTime, nullable=False)
    ends_at = db.Column(db.DateTime, nullable=False)
    accepted = db.Column(db.Boolean, nullable=False, default=False)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    technician = db.relationship("GulfTechnician")


class GulfBusySlot(db.Model):
    """Existing Ajebo Fix work blocked manually until unified scheduling ships."""
    __tablename__ = "gulf_busy_slots"
    id = db.Column(db.Integer, primary_key=True)
    technician_id = db.Column(db.Integer, db.ForeignKey("gulf_technicians.id"))
    customer_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    bay = db.Column(db.String(32))
    starts_at = db.Column(db.DateTime, nullable=False)
    ends_at = db.Column(db.DateTime, nullable=False)
    is_oil_change = db.Column(db.Boolean, nullable=False, default=False)
    reason = db.Column(db.String(100), nullable=False)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)


class GulfDayPlan(db.Model):
    __tablename__ = "gulf_day_plans"
    id = db.Column(db.Integer, primary_key=True)
    service_day = db.Column(db.Date, nullable=False, unique=True)
    daily_cap = db.Column(db.Integer, nullable=False, default=6)
    concurrent_cap = db.Column(db.Integer, nullable=False, default=2)
    saturday_verified = db.Column(db.Boolean, nullable=False, default=False)
    sunday_open = db.Column(db.Boolean, nullable=False, default=False)
    reason = db.Column(db.String(240), nullable=False)
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)


class GulfJob(db.Model):
    __tablename__ = "gulf_jobs"
    id = db.Column(db.Integer, primary_key=True)
    voucher_digest = db.Column(db.String(64), nullable=False, unique=True)
    voucher_cipher = db.Column(db.Text, nullable=False)
    voucher_tail = db.Column(db.String(8), nullable=False)
    name_cipher = db.Column(db.Text, nullable=False)
    phone_cipher = db.Column(db.Text, nullable=False)
    plate_cipher = db.Column(db.Text, nullable=False)
    make = db.Column(db.String(70), nullable=False)
    model = db.Column(db.String(90), nullable=False)
    model_year = db.Column(db.Integer)
    fuel = db.Column(db.String(30))
    service_class = db.Column(db.String(20), nullable=False, default="car")
    requested_day = db.Column(db.Date)
    state = db.Column(db.String(25), nullable=False, default="new", index=True)
    voucher_verified_at = db.Column(db.DateTime)
    verification_reference = db.Column(db.String(120))
    technical_approved_at = db.Column(db.DateTime)
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    oil_spec = db.Column(db.String(100))
    oil_sku = db.Column(db.String(100))
    oil_litres = db.Column(db.String(12))
    filter_part = db.Column(db.String(100))
    dispatch_state = db.Column(db.String(20), nullable=False, default="not_requested")
    dispatch_reference = db.Column(db.String(100))
    products_received_at = db.Column(db.DateTime)
    slot_start = db.Column(db.DateTime)
    slot_end = db.Column(db.DateTime)
    hold_expires_at = db.Column(db.DateTime)
    technician_id = db.Column(db.Integer, db.ForeignKey("gulf_technicians.id"))
    bay = db.Column(db.String(32))
    commission_kobo = db.Column(db.Integer)
    labour_fee_kobo = db.Column(db.Integer)
    actual_start = db.Column(db.DateTime)
    actual_finish = db.Column(db.DateTime)
    odometer_km = db.Column(db.Integer)
    oil_batch = db.Column(db.String(80))
    work_result = db.Column(db.String(500))
    inspection = db.Column(db.JSON)
    handover_at = db.Column(db.DateTime)
    claim_state = db.Column(db.String(20), nullable=False, default="unsubmitted")
    claim_submitted_at = db.Column(db.DateTime)
    paid_kobo = db.Column(db.Integer, nullable=False, default=0)
    commission_paid_kobo = db.Column(db.Integer, nullable=False, default=0)
    payment_reference = db.Column(db.String(100))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    technician = db.relationship("GulfTechnician")

    @property
    def voucher(self):
        return decrypt_profile_value(self.voucher_cipher)

    @voucher.setter
    def voucher(self, value):
        self.voucher_cipher = encrypt_profile_value(value)

    @property
    def customer_name(self):
        return decrypt_profile_value(self.name_cipher)

    @customer_name.setter
    def customer_name(self, value):
        self.name_cipher = encrypt_profile_value(value)

    @property
    def phone(self):
        return decrypt_profile_value(self.phone_cipher)

    @phone.setter
    def phone(self, value):
        self.phone_cipher = encrypt_profile_value(value)

    @property
    def plate(self):
        return decrypt_profile_value(self.plate_cipher)

    @plate.setter
    def plate(self, value):
        self.plate_cipher = encrypt_profile_value(value)


class GulfPhoto(db.Model):
    __tablename__ = "gulf_photos"
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("gulf_jobs.id"), nullable=False, index=True)
    kind = db.Column(db.String(30), nullable=False)
    object_key = db.Column(db.String(300), nullable=False, unique=True)
    mime_type = db.Column(db.String(40), nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class GulfAudit(db.Model):
    __tablename__ = "gulf_audit"
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("gulf_jobs.id"), index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action = db.Column(db.String(65), nullable=False)
    note = db.Column(db.String(240))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class GulfPayment(db.Model):
    """Immutable individual sponsor/contractor transfers, not a mutable total."""
    __tablename__ = "gulf_payments"
    __table_args__ = (
        db.UniqueConstraint("job_id", "kind", "reference", name="uq_gulf_payment_reference"),
    )
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("gulf_jobs.id"), nullable=False, index=True)
    kind = db.Column(db.String(16), nullable=False)
    amount_kobo = db.Column(db.Integer, nullable=False)
    reference = db.Column(db.String(100), nullable=False)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
