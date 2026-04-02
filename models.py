from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON

from app import db


# =========================================================
# USERS
# =========================================================


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(120), nullable=True)

    email = db.Column(db.String(120), unique=True, nullable=False)

    phone_number = db.Column(db.String(20), unique=True, nullable=False)

    password_hash = db.Column(db.String(255), nullable=False)

    # 🔑 ROLE CONTROL
    role = db.Column(db.String(20), default="user", nullable=False)
    # user | admin

    is_active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    car_ownerships = db.relationship(
        "CarOwnership",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    # --------------------
    # Helpers
    # --------------------
    @property
    def is_admin(self):
        return self.role == "admin"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


# =========================================================
# CARS
# =========================================================


class Car(db.Model):
    __tablename__ = "cars"

    id = db.Column(db.Integer, primary_key=True)

    brand = db.Column(db.String(100), nullable=False)

    model = db.Column(db.String(100), nullable=False)

    year = db.Column(db.Integer, nullable=True)

    vin = db.Column(db.String(50), unique=True, nullable=False)

    engine_number = db.Column(db.String(100), nullable=True)

    engine_type = db.Column(db.String(100), nullable=True)

    transmission_type = db.Column(db.String(100), nullable=True)

    current_mileage = db.Column(db.Integer, nullable=True)

    color = db.Column(db.String(50), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    ownerships = db.relationship(
        "CarOwnership",
        back_populates="car",
        cascade="all, delete-orphan",
    )

    events = db.relationship(
        "VehicleEvent",
        back_populates="car",
        cascade="all, delete-orphan",
    )

    faults = db.relationship(
        "CarFault",
        back_populates="car",
        cascade="all, delete-orphan",
    )

    @property
    def display_name(self):
        return f"{self.brand} {self.model} {self.year}"


# =========================================================
# CAR OWNERSHIP
# =========================================================


class CarOwnership(db.Model):
    __tablename__ = "car_ownership"

    __table_args__ = (
        UniqueConstraint("plate_number", "is_active", name="uq_active_plate"),
    )

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    car_id = db.Column(db.Integer, db.ForeignKey("cars.id"), nullable=False)

    plate_number = db.Column(db.String(20), nullable=True)

    mileage_at_transfer = db.Column(db.Integer, nullable=True)

    start_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    end_date = db.Column(db.DateTime, nullable=True)

    is_active = db.Column(db.Boolean, default=True, nullable=False)

    user = db.relationship("User", back_populates="car_ownerships")

    car = db.relationship("Car", back_populates="ownerships")


# =========================================================
# CAR FAULTS (OBSERVATIONS)
# =========================================================


class CarFault(db.Model):
    """
    CLIENT-FACING: Reported Concerns

    IMPORTANT:
    - This model represents observations, NOT diagnoses
    - Language and structure are intentionally calm
    - Health impact is informational only
    """

    __tablename__ = "car_faults"  # ⛔ DO NOT RENAME YET (safe migration later)

    id = db.Column(db.Integer, primary_key=True)

    # =====================================================
    # FOREIGN KEYS
    # =====================================================

    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="CASCADE"),
        nullable=False,
    )

    reported_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )

    resolved_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )

    reviewed_at = db.Column(db.DateTime, nullable=True)

    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # =====================================================
    # REPORTED CONCERN DETAILS
    # =====================================================

    title = db.Column(
        db.String(200),
        nullable=False,
        default="Reported concern",
    )

    category = db.Column(
        db.String(50),
        nullable=False,
        default="observation",
    )
    """
    engine & powertrain
    transmission & drivetrain
    brakes
    electrical & electronics
    fuel & emissions
    body & interior
    cooling & HVAC
    suspension & steering
    warning_light
    other
    """

    description = db.Column(
        db.Text,
        nullable=False,
    )

    # -----------------------------------------------------
    # STATUS (CALM + NON-DIAGNOSTIC)
    # -----------------------------------------------------

    status = db.Column(
        db.String(20),
        nullable=False,
        default="reported",
    )

    source = db.Column(
        db.String(32),
        default="client",
        nullable=False,
    )
    """
    under_review   → newly submitted, awaiting professional review
    monitoring     → observed, no immediate action required
    resolved       → addressed or closed
    """

    # =====================================================
    # TIMESTAMPS
    # =====================================================

    observed_at = db.Column(
        db.DateTime,
        nullable=True,
        doc="When the client first noticed the observation",
    )

    reported_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    resolved_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    # =====================================================
    # RELATIONSHIPS
    # =====================================================

    car = db.relationship(
        "Car",
        back_populates="faults",
    )

    reporter = db.relationship(
        "User",
        foreign_keys=[reported_by],
        backref="reported_concerns",
    )

    resolver = db.relationship(
        "User",
        foreign_keys=[resolved_by],
        backref="resolved_concerns",
    )

    # =====================================================
    # HELPERS
    # =====================================================

    def is_active(self) -> bool:
        """
        Active concerns influence monitoring,
        but do NOT imply failure.
        """
        return self.status in ("under_review", "monitoring")

    def is_resolved(self) -> bool:
        return self.status == "resolved"

    def safe_status_label(self) -> str:
        """
        Client-facing labels (UI-safe)
        """
        return {
            "under_review": "Under Professional Review",
            "monitoring": "Being Monitored",
            "resolved": "Resolved",
        }.get(self.status, "Under Review")

    def __repr__(self):
        return f"<ReportedConcern car_id={self.car_id} status={self.status}>"


# =========================================================
# VEHICLE EVENTS
# =========================================================


class VehicleEvent(db.Model):
    __tablename__ = "vehicle_events"

    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_vehicle_event_fingerprint"),
    )

    id = db.Column(db.Integer, primary_key=True)

    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="CASCADE"),
        nullable=False,
    )

    ownership_id = db.Column(
        db.Integer,
        db.ForeignKey("car_ownership.id"),
        nullable=False,
    )

    event_type = db.Column(db.String(50), nullable=False)

    severity = db.Column(db.String(20), default="low")

    event_date = db.Column(db.Date, nullable=True)

    title = db.Column(db.String(120), nullable=False)

    description = db.Column(db.Text, nullable=True)

    mileage = db.Column(db.Integer, nullable=False)

    source = db.Column(db.String(50), default="manual")

    data = db.Column(JSON)

    fingerprint = db.Column(db.String(64), nullable=False)

    created_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )

    is_deleted = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    resolved_at = db.Column(db.DateTime, nullable=True)

    car = db.relationship("Car", back_populates="events")

    ownership = db.relationship("CarOwnership")


# =========================================================
# EVENT AUDIT LOGS
# =========================================================


class EventAuditLog(db.Model):
    __tablename__ = "event_audit_logs"

    id = db.Column(db.Integer, primary_key=True)

    event_id = db.Column(
        db.Integer,
        db.ForeignKey("vehicle_events.id"),
        nullable=False,
    )

    action = db.Column(db.String(20), nullable=False)
    # create | edit | delete

    old_data = db.Column(JSON)

    new_data = db.Column(JSON)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# =========================================================
# VEHICLE HEALTH SNAPSHOT
# =========================================================


class VehicleHealthSnapshot(db.Model):
    __tablename__ = "vehicle_health_snapshots"

    id = db.Column(db.Integer, primary_key=True)

    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="CASCADE"),
        nullable=False,
    )

    ownership_id = db.Column(
        db.Integer,
        db.ForeignKey("car_ownership.id", ondelete="CASCADE"),
        nullable=False,
    )

    health_score = db.Column(db.Integer, nullable=False)

    health_status = db.Column(db.String(20), nullable=False)
    # excellent | good | fair | poor | critical

    reasons = db.Column(JSON, nullable=True)

    triggered_by = db.Column(db.String(30), nullable=False)
    # event_created | event_updated | ownership_transferred | manual | system

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    car = db.relationship("Car")

    ownership = db.relationship("CarOwnership")

    __table_args__ = (db.Index("idx_health_car_time", "car_id", "created_at"),)


# =========================================================
# VEHICLE HEALTH ALERTS
# =========================================================


class VehicleHealthAlert(db.Model):
    __tablename__ = "vehicle_health_alerts"

    id = db.Column(db.Integer, primary_key=True)

    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id"),
        nullable=False,
    )

    ownership_id = db.Column(
        db.Integer,
        db.ForeignKey("car_ownership.id"),
        nullable=False,
    )

    alert_type = db.Column(db.String(50), nullable=False)
    # rapid_decline | critical_health | predicted_failure | overdue_maintenance

    severity = db.Column(db.String(20), nullable=False)
    # critical | high | medium | low

    message = db.Column(db.Text, nullable=False)

    is_active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    resolved_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "car_id",
            "ownership_id",
            "alert_type",
            "is_active",
            name="uq_active_health_alert",
        ),
    )


# =============================================
# CONSULTATION
# =============================================


class Consultation(db.Model):
    __tablename__ = "consultations"

    id = db.Column(db.Integer, primary_key=True)

    car_id = db.Column(db.Integer, db.ForeignKey("cars.id"), nullable=False)

    notes = db.Column(db.Text, nullable=True)

    ownership_id = db.Column(
        db.Integer,
        db.ForeignKey("car_ownership.id"),
        nullable=False,
    )

    advisor_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    client_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default="scheduled",  # scheduled | in_progress | completed
    )

    scheduled_for = db.Column(db.DateTime, nullable=False)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    summary = db.Column(db.Text)  # internal
    client_visible_summary = db.Column(db.Text)  # sanitized

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    car = db.relationship("Car")
    client = db.relationship("User", foreign_keys=[client_id])
    advisor = db.relationship("User", foreign_keys=[advisor_id])

    # -------------------------
    # Helpers
    # -------------------------
    def is_active(self):
        return self.status == "in_progress"

    def is_completed(self):
        return self.status == "completed"


# ======================================
# VEHICLE ASSESSMENT (root document)
# ======================================


class VehicleAssessment(db.Model):
    __tablename__ = "vehicle_assessments"

    id = db.Column(db.Integer, primary_key=True)

    # ----------------------------
    # Authority & identity
    # ----------------------------
    consultation_id = db.Column(
        db.Integer,
        db.ForeignKey("consultations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="CASCADE"),
        nullable=False,
    )

    advisor_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )

    finalized_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )

    # ----------------------------
    # Lifecycle
    # ----------------------------
    status = db.Column(
        db.String(20),
        nullable=False,
        default="draft",
    )
    # draft | finalized

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    is_finalized = db.Column(db.Boolean, default=False, nullable=False)
    finalized_at = db.Column(db.DateTime, nullable=True)

    # ----------------------------
    # SECTION 1 — Vehicle overview (frozen)
    # ----------------------------
    vin = db.Column(db.String(50), nullable=False)
    mileage_at_assessment = db.Column(db.Integer, nullable=False)

    engine_number = db.Column(db.String(100), nullable=True)
    engine_type = db.Column(db.String(100), nullable=True)

    transmission = db.Column(db.String(100), nullable=True)
    usage_pattern = db.Column(db.String(100), nullable=True)
    ownership_duration = db.Column(db.String(50), nullable=True)

    # ----------------------------
    # SECTION 2 — Current health status
    # ----------------------------
    engine_status = db.Column(db.String(30), nullable=True)
    engine_note = db.Column(db.Text, nullable=True)

    transmission_status = db.Column(db.String(30), nullable=True)
    transmission_note = db.Column(db.Text, nullable=True)

    suspension_status = db.Column(db.String(30), nullable=True)
    suspension_note = db.Column(db.Text, nullable=True)

    electrical_status = db.Column(db.String(30), nullable=True)
    electrical_note = db.Column(db.Text, nullable=True)

    cooling_status = db.Column(db.String(30), nullable=True)
    cooling_note = db.Column(db.Text, nullable=True)

    # ----------------------------
    # SECTION 5 — Cost vs consequence framing
    # ----------------------------
    cost_consequence_analysis = db.Column(db.Text, nullable=True)

    # ----------------------------
    # SECTION 7 — Professional recommendation
    # ----------------------------
    professional_recommendation = db.Column(db.Text, nullable=True)

    # ----------------------------
    # Relationships
    # ----------------------------

    consultation = db.relationship(
        "Consultation",
        backref=db.backref("assessment", uselist=False),
    )

    car = db.relationship(
        "Car",
        backref="assessments",
    )

    advisor = db.relationship(
        "User",
        foreign_keys=[advisor_id],
        backref="assessments_created",
    )

    finalizer = db.relationship(
        "User",
        foreign_keys=[finalized_by],
        backref="assessments_finalized",
    )

    risks = db.relationship(
        "VehicleAssessmentRisk",
        backref="assessment",
        cascade="all, delete-orphan",
    )

    treatment_options = db.relationship(
        "VehicleAssessmentTreatmentOption",
        backref="assessment",
        cascade="all, delete-orphan",
    )


# ===================================
# VEHICLE ASSESSMENT RISK MODEL (Section 3 + 4)
# ===================================
class VehicleAssessmentRisk(db.Model):
    __tablename__ = "vehicle_assessment_risks"

    id = db.Column(db.Integer, primary_key=True)

    assessment_id = db.Column(
        db.Integer,
        db.ForeignKey("vehicle_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )

    description = db.Column(db.Text, nullable=False)
    likely_cause = db.Column(db.Text, nullable=False)
    consequence_if_ignored = db.Column(db.Text, nullable=False)

    urgency = db.Column(
        db.String(30),
        nullable=False,
    )
    # immediate | monitoring | preventive


# ===================================================
# VEHICLE ASSESSMENT TREATMENT OPTION (Section 6)
# ===================================================


class VehicleAssessmentTreatmentOption(db.Model):
    __tablename__ = "vehicle_assessment_treatment_options"

    id = db.Column(db.Integer, primary_key=True)

    assessment_id = db.Column(
        db.Integer,
        db.ForeignKey("vehicle_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )

    option_code = db.Column(
        db.String(1),
        nullable=False,
    )
    # A | B | C

    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=False)


# ================================================
# USER MEMORY (identification + personalization)
# ===================================================


class UserMemory(db.Model):
    __tablename__ = "user_memory"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True)

    name = db.Column(db.String(100))
    preferences = db.Column(db.JSON, nullable=True)

    last_seen = db.Column(db.DateTime, default=db.func.now())
    last_vehicle = db.Column(db.String)
    last_topic = db.Column(db.String)
    created_at = db.Column(db.DateTime, default=db.func.now())

    user = db.relationship("User", backref="memory", uselist=False)


# =========================================================
# CHAT MESSAGES (conversation memory)
# ===========================================================


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    role = db.Column(db.String(20))  # "user" or "assistant"
    message = db.Column(db.Text, nullable=False)

    timestamp = db.Column(db.DateTime, default=db.func.now())

    user = db.relationship("User", backref="chat_messages")


# =================================================
# CONVERSATION SESSION
# =================================================


class ChatSession(db.Model):
    __tablename__ = "chat_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    started_at = db.Column(db.DateTime, default=db.func.now())
    active = db.Column(db.Boolean, default=True)

    user = db.relationship("User", backref="chat_sessions")


# ========================================================
# COMPLAINT / ESCALATION SYSTEM
# =======================================================


class EscalationLog(db.Model):
    __tablename__ = "escalation_logs"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    message = db.Column(db.Text)

    status = db.Column(db.String(50), default="pending")  # pending, resolved
    created_at = db.Column(db.DateTime, default=db.func.now())

    user = db.relationship("User", backref="escalations")
