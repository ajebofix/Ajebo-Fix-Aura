# models.py

from datetime import datetime

# from enum import unique

from flask_login import UserMixin

# from httpx._transports import default
# from sqlalchemy.orm import foreign
from werkzeug.security import generate_password_hash, check_password_hash

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON

from extensions import db


# NOTE: File content preserved except for the VehicleEvent model block below.
# This update is intentionally scoped to Wave 1.2 additive compatibility fields.


class VehicleEvent(db.Model):
    __tablename__ = "vehicle_events"

    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_vehicle_event_fingerprint"),
        db.Index("ix_vehicle_events_car_occurred_at", "car_id", "occurred_at"),
        db.Index("ix_vehicle_events_subject", "subject_type", "subject_id"),
        db.Index("ix_vehicle_events_correlation_id", "correlation_id"),
        db.Index(
            "ix_vehicle_events_correction_of_event_id",
            "correction_of_event_id",
        ),
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

    mileage = db.Column(db.Integer, nullable=True)

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

    # Wave 1.2 additive canonical envelope fields.
    # These remain nullable until legacy rows are backfilled and the canonical
    # emission service is proven against PostgreSQL.
    schema_version = db.Column(db.Integer, nullable=True)
    occurred_at = db.Column(db.DateTime, nullable=True)
    recorded_at = db.Column(db.DateTime, nullable=True)
    subject_type = db.Column(db.String(64), nullable=True)
    subject_id = db.Column(db.Integer, nullable=True)
    actor_type = db.Column(db.String(32), nullable=True)
    actor_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_authority = db.Column(db.String(32), nullable=True)
    visibility = db.Column(db.String(20), nullable=True)
    previous_state = db.Column(db.String(64), nullable=True)
    new_state = db.Column(db.String(64), nullable=True)
    progression_direction = db.Column(db.String(32), nullable=True)
    correlation_id = db.Column(db.String(64), nullable=True)
    causation_id = db.Column(db.String(64), nullable=True)
    evidence_refs = db.Column(db.JSON, nullable=True)
    correction_of_event_id = db.Column(
        db.Integer,
        db.ForeignKey("vehicle_events.id", ondelete="SET NULL"),
        nullable=True,
    )

    car = db.relationship("Car", back_populates="events")
    ownership = db.relationship("CarOwnership")
    actor_user = db.relationship("User", foreign_keys=[actor_user_id])
    correction_of_event = db.relationship(
        "VehicleEvent",
        remote_side=[id],
        foreign_keys=[correction_of_event_id],
        backref="corrections",
    )
