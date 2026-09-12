"""Persistence for provenance-aware Aura maintenance knowledge and service normalization.

Maintenance knowledge is reusable schedule/rule information. It is deliberately
separate from vehicle service-history facts and from derived due-state results.
Service classifications are separate advisor-governed facts that map one saved
service event to a stable maintenance item identity without rewriting the
original service record.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from extensions import db


MAINTENANCE_VERIFICATION_STATUSES = (
    "unverified",
    "source_verified",
    "advisor_verified",
    "superseded",
    "rejected",
)

MAINTENANCE_SOURCE_TYPES = (
    "oem",
    "advisor_reference",
    "provider",
    "manual_reference",
    "test",
)

MAINTENANCE_SERVICE_CLASSIFICATION_STATUSES = (
    "active",
    "superseded",
    "removed",
)

MAINTENANCE_SERVICE_CLASSIFICATION_SOURCES = (
    "advisor_service_entry",
    "advisor_review",
)


class MaintenanceKnowledgeRule(db.Model):
    """Versioned candidate/verified maintenance schedule knowledge.

    A row never means that work was performed. It only describes a sourced
    maintenance rule that may become eligible for deterministic evaluation after
    explicit verification.
    """

    __tablename__ = "maintenance_knowledge_rules"

    __table_args__ = (
        db.CheckConstraint(
            "verification_status IN "
            "('unverified', 'source_verified', 'advisor_verified', "
            "'superseded', 'rejected')",
            name="ck_maintenance_knowledge_verification_status",
        ),
        db.CheckConstraint(
            "source_type IN "
            "('oem', 'advisor_reference', 'provider', 'manual_reference', 'test')",
            name="ck_maintenance_knowledge_source_type",
        ),
        db.CheckConstraint(
            "interval_km IS NOT NULL OR interval_months IS NOT NULL",
            name="ck_maintenance_knowledge_has_interval",
        ),
        db.CheckConstraint(
            "interval_km IS NULL OR interval_km > 0",
            name="ck_maintenance_knowledge_interval_km_positive",
        ),
        db.CheckConstraint(
            "interval_months IS NULL OR interval_months > 0",
            name="ck_maintenance_knowledge_interval_months_positive",
        ),
        db.CheckConstraint(
            "year_start IS NULL OR year_end IS NULL OR year_start <= year_end",
            name="ck_maintenance_knowledge_year_range",
        ),
        db.CheckConstraint(
            "car_id IS NOT NULL OR brand IS NOT NULL",
            name="ck_maintenance_knowledge_has_applicability",
        ),
        db.CheckConstraint(
            "verification_status = 'unverified' OR "
            "(verified_by IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_maintenance_knowledge_review_metadata",
        ),
        db.Index(
            "ix_maintenance_knowledge_item_status",
            "maintenance_item_key",
            "verification_status",
        ),
        db.Index(
            "ix_maintenance_knowledge_vehicle_override",
            "car_id",
            "verification_status",
        ),
        db.Index(
            "ix_maintenance_knowledge_applicability",
            "brand",
            "model",
            "year_start",
            "year_end",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    maintenance_item_key = db.Column(db.String(100), nullable=False)
    display_name = db.Column(db.String(150), nullable=False)

    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="CASCADE"),
        nullable=True,
    )
    brand = db.Column(db.String(100), nullable=True)
    model = db.Column(db.String(120), nullable=True)
    year_start = db.Column(db.Integer, nullable=True)
    year_end = db.Column(db.Integer, nullable=True)
    trim = db.Column(db.String(120), nullable=True)
    engine_type = db.Column(db.String(120), nullable=True)
    fuel_type = db.Column(db.String(80), nullable=True)
    transmission = db.Column(db.String(120), nullable=True)
    drive_type = db.Column(db.String(80), nullable=True)

    interval_km = db.Column(db.Integer, nullable=True)
    interval_months = db.Column(db.Integer, nullable=True)

    source_type = db.Column(db.String(40), nullable=False)
    source_name = db.Column(db.String(150), nullable=False)
    source_reference = db.Column(db.String(500), nullable=False)
    source_version = db.Column(db.String(120), nullable=True)

    verification_status = db.Column(
        db.String(30),
        nullable=False,
        default="unverified",
        server_default="unverified",
    )
    verified_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    verified_at = db.Column(db.DateTime, nullable=True)

    superseded_by_id = db.Column(
        db.Integer,
        db.ForeignKey("maintenance_knowledge_rules.id", ondelete="RESTRICT"),
        nullable=True,
    )

    fingerprint = db.Column(db.String(64), nullable=False, unique=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    car = db.relationship("Car", foreign_keys=[car_id])
    verifier = db.relationship("User", foreign_keys=[verified_by])
    superseded_by = db.relationship(
        "MaintenanceKnowledgeRule",
        remote_side=[id],
        foreign_keys=[superseded_by_id],
        uselist=False,
    )

    @property
    def is_production_active(self) -> bool:
        """Only advisor-verified knowledge may drive future production state."""

        return self.verification_status == "advisor_verified"

    def __repr__(self) -> str:
        return (
            "<MaintenanceKnowledgeRule "
            f"item={self.maintenance_item_key!r} "
            f"status={self.verification_status!r}>"
        )


class MaintenanceServiceClassification(db.Model):
    """Advisor-governed maintenance identity for one canonical service event.

    The service event remains the durable fact that work was recorded. This row
    records only the professional classification needed to match that fact to a
    verified maintenance item. Classification corrections are append-preserving:
    the prior active row is superseded or removed rather than deleted.
    """

    __tablename__ = "maintenance_service_classifications"

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('active', 'superseded', 'removed')",
            name="ck_maintenance_service_classification_status",
        ),
        db.CheckConstraint(
            "classification_source IN ('advisor_service_entry', 'advisor_review')",
            name="ck_maintenance_service_classification_source",
        ),
        db.Index(
            "ix_maintenance_service_classification_event_status",
            "service_event_id",
            "status",
        ),
        db.Index(
            "ix_maintenance_service_classification_car_item",
            "car_id",
            "maintenance_item_key",
            "status",
        ),
        db.Index(
            "uq_maintenance_service_classification_active_event",
            "service_event_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    service_event_id = db.Column(
        db.Integer,
        db.ForeignKey("vehicle_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="CASCADE"),
        nullable=False,
    )
    maintenance_item_key = db.Column(db.String(100), nullable=False)
    knowledge_rule_id = db.Column(
        db.Integer,
        db.ForeignKey("maintenance_knowledge_rules.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status = db.Column(
        db.String(20),
        nullable=False,
        default="active",
        server_default="active",
    )
    classification_source = db.Column(db.String(40), nullable=False)
    classified_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    classified_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    superseded_by_id = db.Column(
        db.Integer,
        db.ForeignKey("maintenance_service_classifications.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    service_event = db.relationship("VehicleEvent", foreign_keys=[service_event_id])
    car = db.relationship("Car", foreign_keys=[car_id])
    knowledge_rule = db.relationship("MaintenanceKnowledgeRule", foreign_keys=[knowledge_rule_id])
    classifier = db.relationship("User", foreign_keys=[classified_by])
    superseded_by = db.relationship(
        "MaintenanceServiceClassification",
        remote_side=[id],
        foreign_keys=[superseded_by_id],
        uselist=False,
    )

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def __repr__(self) -> str:
        return (
            "<MaintenanceServiceClassification "
            f"event={self.service_event_id} item={self.maintenance_item_key!r} "
            f"status={self.status!r}>"
        )
