"""Immutable additive corrections for finalized Vehicle Assessments.

Wave 2.2B3 deliberately keeps correction records outside the original
VehicleAssessment row. Published addenda are append-only professional records:
a later change is represented by another addendum, never an update or delete.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import event

from extensions import db


ADDENDUM_CATEGORIES = ("correction", "clarification", "additional_information")
ADDENDUM_VISIBILITIES = ("client", "advisor", "internal")


class VehicleAssessmentAddendum(db.Model):
    __tablename__ = "vehicle_assessment_addenda"

    __table_args__ = (
        db.CheckConstraint(
            "category IN ('correction', 'clarification', 'additional_information')",
            name="ck_vehicle_assessment_addenda_category",
        ),
        db.CheckConstraint(
            "visibility IN ('client', 'advisor', 'internal')",
            name="ck_vehicle_assessment_addenda_visibility",
        ),
        db.CheckConstraint(
            "(client_text IS NOT NULL AND length(trim(client_text)) > 0) "
            "OR (internal_text IS NOT NULL AND length(trim(internal_text)) > 0)",
            name="ck_vehicle_assessment_addenda_has_text",
        ),
        db.CheckConstraint(
            "visibility <> 'client' "
            "OR (client_text IS NOT NULL AND length(trim(client_text)) > 0)",
            name="ck_vehicle_assessment_addenda_client_text",
        ),
        db.UniqueConstraint(
            "idempotency_key",
            name="uq_vehicle_assessment_addenda_idempotency_key",
        ),
        db.Index(
            "ix_vehicle_assessment_addenda_assessment_created",
            "assessment_id",
            "created_at",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    assessment_id = db.Column(
        db.Integer,
        db.ForeignKey("vehicle_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )

    created_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )

    category = db.Column(db.String(40), nullable=False)
    reason = db.Column(db.String(240), nullable=False)
    visibility = db.Column(db.String(20), nullable=False)

    client_text = db.Column(db.Text, nullable=True)
    internal_text = db.Column(db.Text, nullable=True)

    idempotency_key = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    assessment = db.relationship("VehicleAssessment")
    creator = db.relationship("User", foreign_keys=[created_by])


@event.listens_for(VehicleAssessmentAddendum, "before_update")
def _prevent_addendum_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "Published Vehicle Assessment addenda are immutable; record another addendum instead"
    )


@event.listens_for(VehicleAssessmentAddendum, "before_delete")
def _prevent_addendum_delete(_mapper, _connection, _target) -> None:
    raise ValueError(
        "Published Vehicle Assessment addenda cannot be deleted; preserve the audit record"
    )
