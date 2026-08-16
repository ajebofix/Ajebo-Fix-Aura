"""Canonical Wave 1.4 vehicle-evidence records.

These models store metadata and encrypted extraction payloads only. Raw media bytes
remain in private object storage and are never stored in PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime

from extensions import db


EVIDENCE_TYPES = ("image", "document", "audio")
EVIDENCE_PURPOSES = (
    "concern_support",
    "consultation_support",
    "assessment_evidence",
    "treatment_evidence",
    "diagnostic_document",
    "service_document",
    "driver_observation",
)
EVIDENCE_SOURCE_CHANNELS = ("web", "whatsapp", "api")
EVIDENCE_VISIBILITY = ("client", "advisor", "internal")
EVIDENCE_REVIEW_STATUSES = (
    "pending_review",
    "accepted",
    "rejected",
    "superseded",
    "deleted",
)
EVIDENCE_STORAGE_STATES = (
    "pending",
    "available",
    "failed",
    "delete_pending",
    "deleted",
)
CAPTURE_TIME_SOURCES = ("user_declared", "embedded_verified")

EVIDENCE_SUBJECT_TYPES = (
    "reported_concern",
    "consultation",
    "assessment",
    "treatment_plan",
    "vehicle_event",
)
EVIDENCE_RELATIONSHIP_TYPES = ("supports", "documents")

EXTRACTION_TYPES = (
    "image_observation",
    "document_text",
    "transcription",
    "structured_fields",
)
EXTRACTION_STATUSES = ("pending", "processing", "completed", "failed")
EXTRACTION_REVIEW_STATUSES = ("unreviewed", "accepted", "rejected", "corrected")


class VehicleEvidence(db.Model):
    """One uploaded evidence object and its governed lifecycle metadata."""

    __tablename__ = "vehicle_evidence"

    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploaded_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    evidence_type = db.Column(db.String(24), nullable=False)
    purpose = db.Column(db.String(48), nullable=False)
    source_channel = db.Column(db.String(24), nullable=False, default="web")
    visibility = db.Column(db.String(20), nullable=False, default="client")
    review_status = db.Column(
        db.String(24), nullable=False, default="pending_review", index=True
    )

    storage_provider = db.Column(db.String(32), nullable=False, default="r2")
    storage_state = db.Column(
        db.String(24), nullable=False, default="pending", index=True
    )
    storage_failure_reason_code = db.Column(db.String(64), nullable=True)
    object_key = db.Column(db.String(255), nullable=False, unique=True)
    safe_display_name = db.Column(db.String(160), nullable=False)
    content_type = db.Column(db.String(120), nullable=False)
    byte_size = db.Column(db.BigInteger, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)

    captured_at = db.Column(db.DateTime, nullable=True)
    capture_time_source = db.Column(db.String(32), nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    consent_basis = db.Column(db.String(64), nullable=False)
    lawful_purpose = db.Column(db.String(128), nullable=False)
    retention_until = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)

    reviewed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_reason_code = db.Column(db.String(64), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    car = db.relationship("Car", foreign_keys=[car_id])
    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_user_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_user_id])

    links = db.relationship(
        "EvidenceLink",
        back_populates="evidence",
        cascade="all, delete-orphan",
    )
    extractions = db.relationship(
        "EvidenceExtraction",
        back_populates="evidence",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.Index("ix_vehicle_evidence_car_time", "car_id", "uploaded_at", "id"),
        db.Index("ix_vehicle_evidence_car_sha256", "car_id", "sha256"),
    )


class EvidenceLink(db.Model):
    """Controlled linkage between evidence and a same-vehicle Aura care subject."""

    __tablename__ = "evidence_links"

    id = db.Column(db.Integer, primary_key=True)
    evidence_id = db.Column(
        db.Integer,
        db.ForeignKey("vehicle_evidence.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_type = db.Column(db.String(40), nullable=False)
    subject_id = db.Column(db.Integer, nullable=False)
    relationship_type = db.Column(db.String(32), nullable=False, default="supports")
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    evidence = db.relationship("VehicleEvidence", back_populates="links")
    car = db.relationship("Car", foreign_keys=[car_id])
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        db.UniqueConstraint(
            "evidence_id",
            "subject_type",
            "subject_id",
            "relationship_type",
            name="uq_evidence_link_subject_relationship",
        ),
        db.Index(
            "ix_evidence_links_car_subject",
            "car_id",
            "subject_type",
            "subject_id",
        ),
    )


class EvidenceExtraction(db.Model):
    """Provider extraction metadata with encrypted source/corrected payload slots."""

    __tablename__ = "evidence_extractions"

    id = db.Column(db.Integer, primary_key=True)
    evidence_id = db.Column(
        db.Integer,
        db.ForeignKey("vehicle_evidence.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    extraction_type = db.Column(db.String(40), nullable=False)
    provider = db.Column(db.String(40), nullable=False)
    provider_model = db.Column(db.String(120), nullable=True)
    provider_request_id = db.Column(db.String(160), nullable=True, index=True)
    status = db.Column(db.String(24), nullable=False, default="pending", index=True)
    confidence = db.Column(db.Float, nullable=True)

    # Extraction text/structured results may contain personal, vehicle or location data.
    # Keep only encrypted payload slots here; the extraction adapter owns encryption.
    result_ciphertext = db.Column(db.Text, nullable=True)
    result_key_version = db.Column(db.String(64), nullable=True)
    result_sha256 = db.Column(db.String(64), nullable=True)
    provenance = db.Column(db.JSON, nullable=True)

    review_status = db.Column(
        db.String(24), nullable=False, default="unreviewed", index=True
    )
    reviewed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_reason_code = db.Column(db.String(64), nullable=True)

    # Advisor corrections remain additive and never overwrite provider output.
    reviewed_result_ciphertext = db.Column(db.Text, nullable=True)
    reviewed_result_key_version = db.Column(db.String(64), nullable=True)
    reviewed_result_sha256 = db.Column(db.String(64), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    evidence = db.relationship("VehicleEvidence", back_populates="extractions")
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_user_id])

    __table_args__ = (
        db.Index(
            "ix_evidence_extractions_evidence_time",
            "evidence_id",
            "created_at",
            "id",
        ),
    )
