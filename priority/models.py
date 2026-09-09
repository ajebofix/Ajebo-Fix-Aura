"""Durable priority-request model for Aura Wave 2.4D."""

from __future__ import annotations

from datetime import datetime

from extensions import db


PRIORITY_REQUEST_STATUSES = (
    "requested",
    "under_review",
    "accepted",
    "deferred",
    "resolved",
    "cancelled",
)
PRIORITY_REQUEST_KINDS = ("priority", "emergency_review")
PRIORITY_REQUEST_SOURCES = ("owner", "advisor", "rina_structured_request")


class PriorityRequest(db.Model):
    __tablename__ = "priority_requests"

    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ownership_id = db.Column(
        db.Integer,
        db.ForeignKey("car_ownership.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    requested_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reviewed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    consultation_id = db.Column(
        db.Integer,
        db.ForeignKey("consultations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    request_source = db.Column(db.String(32), nullable=False)
    request_kind = db.Column(db.String(32), nullable=False)
    status = db.Column(
        db.String(32),
        nullable=False,
        default="requested",
        server_default="requested",
        index=True,
    )

    reason_summary = db.Column(db.String(500), nullable=False)
    advisor_review_note = db.Column(db.Text, nullable=True)

    eligibility_at_request = db.Column(db.Boolean, nullable=False)
    care_plan_snapshot = db.Column(db.String(50), nullable=True)
    health_status_snapshot = db.Column(db.String(50), nullable=True)

    request_key = db.Column(db.String(128), nullable=False, unique=True)

    requested_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    review_started_at = db.Column(db.DateTime, nullable=True)
    accepted_at = db.Column(db.DateTime, nullable=True)
    deferred_at = db.Column(db.DateTime, nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    car = db.relationship("Car", foreign_keys=[car_id])
    ownership = db.relationship("CarOwnership", foreign_keys=[ownership_id])
    requester = db.relationship("User", foreign_keys=[requested_by_user_id])
    reviewing_advisor = db.relationship("User", foreign_keys=[reviewed_by_user_id])
    resolving_advisor = db.relationship("User", foreign_keys=[resolved_by_user_id])
    consultation = db.relationship("Consultation", foreign_keys=[consultation_id])

    @property
    def is_terminal(self) -> bool:
        return self.status in {"resolved", "cancelled"}

    @property
    def is_active(self) -> bool:
        return not self.is_terminal
