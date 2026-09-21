"""Historical service episodes anchored to advisor-reviewed source evidence."""

from __future__ import annotations

from datetime import datetime

from extensions import db


HISTORICAL_EPISODE_STATUSES = ("active", "archived")


class HistoricalServiceEpisode(db.Model):
    """One historical vehicle-care episode with immutable source provenance."""

    __tablename__ = "historical_service_episodes"

    id = db.Column(db.Integer, primary_key=True)

    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    anchor_evidence_id = db.Column(
        db.Integer,
        db.ForeignKey("vehicle_evidence.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    anchor_extraction_id = db.Column(
        db.Integer,
        db.ForeignKey("evidence_extractions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    title = db.Column(db.String(255), nullable=False)
    job_reference = db.Column(db.String(120), nullable=True, index=True)
    episode_date = db.Column(db.DateTime, nullable=True, index=True)
    status = db.Column(
        db.String(24),
        nullable=False,
        default="active",
        server_default="active",
        index=True,
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    car = db.relationship("Car", foreign_keys=[car_id])
    anchor_evidence = db.relationship(
        "VehicleEvidence",
        foreign_keys=[anchor_evidence_id],
    )
    anchor_extraction = db.relationship(
        "EvidenceExtraction",
        foreign_keys=[anchor_extraction_id],
    )
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        db.Index(
            "ix_historical_service_episodes_car_date",
            "car_id",
            "episode_date",
            "id",
        ),
    )
