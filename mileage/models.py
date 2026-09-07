"""Mileage observation records for Aura's longitudinal vehicle history."""

from __future__ import annotations

from datetime import datetime

from extensions import db


class MileageObservation(db.Model):
    """One observed main-odometer reading with provenance.

    ``Car.current_mileage`` remains Aura's compatibility projection for the
    latest known cumulative odometer. This table preserves the evidence trail
    behind that projection and may also contain legitimate historical snapshots
    such as old service records.
    """

    __tablename__ = "mileage_observations"

    __table_args__ = (
        db.CheckConstraint(
            "odometer_km >= 0 AND odometer_km <= 5000000",
            name="ck_mileage_observation_range",
        ),
        db.Index(
            "idx_mileage_observation_car_observed",
            "car_id",
            "observed_at",
        ),
        db.Index(
            "idx_mileage_observation_source",
            "source",
        ),
        db.UniqueConstraint(
            "car_id",
            "source",
            "source_reference",
            name="uq_mileage_observation_source_reference",
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
        db.ForeignKey("car_ownerships.id", ondelete="SET NULL"),
        nullable=True,
    )

    odometer_km = db.Column(db.Integer, nullable=False)

    observed_at = db.Column(db.DateTime, nullable=False)

    recorded_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    source = db.Column(
        db.String(40),
        nullable=False,
    )

    verification_status = db.Column(
        db.String(40),
        nullable=False,
    )

    recorded_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    is_historical = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
        server_default="false",
    )

    source_reference = db.Column(
        db.String(128),
        nullable=True,
    )

    evidence_reference = db.Column(
        db.String(255),
        nullable=True,
    )

    note = db.Column(db.Text, nullable=True)

    car = db.relationship("Car")
    ownership = db.relationship("CarOwnership")
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_user_id])

    def __repr__(self):
        return (
            f"<MileageObservation car_id={self.car_id} "
            f"odometer_km={self.odometer_km} source={self.source}>"
        )
