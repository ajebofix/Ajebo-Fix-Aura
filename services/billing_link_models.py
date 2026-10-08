"""Explicit, audited cross-domain links: Billing is commercial truth, Aura is care."""
from __future__ import annotations

from datetime import datetime

from extensions import db


class BillingVehicleConnection(db.Model):
    __tablename__ = "billing_vehicle_connections"
    __table_args__ = (
        db.UniqueConstraint("aura_car_id", name="uq_billing_vehicle_connections_car"),
        db.UniqueConstraint("billing_vehicle_id", name="uq_billing_vehicle_connections_remote"),
        db.UniqueConstraint("billing_job_id", name="uq_billing_vehicle_connections_pilot_job"),
    )

    id = db.Column(db.Integer, primary_key=True)
    aura_car_id = db.Column(
        db.Integer, db.ForeignKey("cars.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    aura_owner_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    billing_vehicle_id = db.Column(db.String(36), nullable=False)
    billing_client_id = db.Column(db.String(36), nullable=False)
    billing_job_id = db.Column(db.String(36), nullable=False)
    billing_job_reference = db.Column(db.String(80), nullable=False)
    verified_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    verification_basis = db.Column(db.String(240), nullable=False)
    verified_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    client_visible = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    revoked_at = db.Column(db.DateTime, nullable=True)


class BillingConnectionEvent(db.Model):
    __tablename__ = "billing_connection_events"
    __table_args__ = (
        db.CheckConstraint(
            "event_type IN ('verified', 'published', 'unpublished', 'revoked')",
            name="ck_billing_connection_events_type",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(
        db.Integer,
        db.ForeignKey("billing_vehicle_connections.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    actor_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_type = db.Column(db.String(32), nullable=False)
    occurred_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
