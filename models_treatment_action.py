"""Wave 2.3C Treatment Action and Treatment Outcome domain records.

TreatmentAction records concrete professional interventions under an authorized
Treatment Plan. TreatmentOutcome records additive advisor-reviewed observations
supported by governed evidence. Neither model replaces historical generic
VehicleEvent service/treatment records.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import event

from extensions import db


TREATMENT_ACTION_STATUSES = (
    "planned",
    "scheduled",
    "in_progress",
    "completed",
    "deferred",
    "cancelled",
)

TREATMENT_OUTCOME_DIRECTIONS = (
    "improving",
    "stable",
    "deteriorating",
    "resolved",
    "insufficient_evidence",
)


class TreatmentAction(db.Model):
    """One concrete professional intervention within one Treatment Plan."""

    __tablename__ = "treatment_actions"

    id = db.Column(db.Integer, primary_key=True)

    treatment_plan_id = db.Column(
        db.Integer,
        db.ForeignKey("treatment_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    title = db.Column(db.String(180), nullable=False)
    client_summary = db.Column(db.Text, nullable=True)
    internal_instructions = db.Column(db.Text, nullable=True)

    status = db.Column(
        db.String(24),
        nullable=False,
        default="planned",
        server_default="planned",
    )

    scheduled_for = db.Column(db.DateTime, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    deferred_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)

    idempotency_key = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    treatment_plan = db.relationship("TreatmentPlan", foreign_keys=[treatment_plan_id])
    car = db.relationship("Car", foreign_keys=[car_id])
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        db.CheckConstraint(
            "status IS NOT NULL AND status IN "
            "('planned', 'scheduled', 'in_progress', 'completed', 'deferred', 'cancelled')",
            name="ck_treatment_actions_status",
        ),
        db.CheckConstraint(
            "status <> 'scheduled' OR scheduled_for IS NOT NULL",
            name="ck_treatment_actions_scheduled_timestamp",
        ),
        db.CheckConstraint(
            "status <> 'in_progress' OR started_at IS NOT NULL",
            name="ck_treatment_actions_started_timestamp",
        ),
        db.CheckConstraint(
            "status <> 'completed' OR (started_at IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_treatment_actions_completed_timestamps",
        ),
        db.CheckConstraint(
            "status <> 'deferred' OR deferred_at IS NOT NULL",
            name="ck_treatment_actions_deferred_timestamp",
        ),
        db.CheckConstraint(
            "status <> 'cancelled' OR cancelled_at IS NOT NULL",
            name="ck_treatment_actions_cancelled_timestamp",
        ),
        db.UniqueConstraint(
            "idempotency_key",
            name="uq_treatment_actions_idempotency_key",
        ),
        db.Index(
            "ix_treatment_actions_plan_status",
            "treatment_plan_id",
            "status",
        ),
        db.Index(
            "ix_treatment_actions_car_created",
            "car_id",
            "created_at",
        ),
    )


class TreatmentOutcome(db.Model):
    """Append-only advisor-reviewed treatment observation.

    Supporting governed EvidenceLink rows are created by the outcome-recording
    service. The model deliberately does not embed raw evidence or provider
    extraction output.
    """

    __tablename__ = "treatment_outcomes"

    id = db.Column(db.Integer, primary_key=True)

    treatment_plan_id = db.Column(
        db.Integer,
        db.ForeignKey("treatment_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="CASCADE"),
        nullable=False,
    )
    treatment_action_id = db.Column(
        db.Integer,
        db.ForeignKey("treatment_actions.id", ondelete="SET NULL"),
        nullable=True,
    )
    recorded_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    progression_direction = db.Column(db.String(32), nullable=False)
    client_summary = db.Column(db.Text, nullable=False)
    internal_notes = db.Column(db.Text, nullable=True)
    observed_at = db.Column(db.DateTime, nullable=False)

    idempotency_key = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    treatment_plan = db.relationship("TreatmentPlan", foreign_keys=[treatment_plan_id])
    car = db.relationship("Car", foreign_keys=[car_id])
    treatment_action = db.relationship("TreatmentAction", foreign_keys=[treatment_action_id])
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_user_id])

    __table_args__ = (
        db.CheckConstraint(
            "progression_direction IS NOT NULL AND progression_direction IN "
            "('improving', 'stable', 'deteriorating', 'resolved', 'insufficient_evidence')",
            name="ck_treatment_outcomes_progression",
        ),
        db.CheckConstraint(
            "client_summary IS NOT NULL AND length(trim(client_summary)) > 0",
            name="ck_treatment_outcomes_client_summary",
        ),
        db.UniqueConstraint(
            "idempotency_key",
            name="uq_treatment_outcomes_idempotency_key",
        ),
        db.Index(
            "ix_treatment_outcomes_plan_observed",
            "treatment_plan_id",
            "observed_at",
            "id",
        ),
        db.Index(
            "ix_treatment_outcomes_car_created",
            "car_id",
            "created_at",
        ),
    )


@event.listens_for(TreatmentOutcome, "before_update")
def _prevent_treatment_outcome_update(_mapper, _connection, _target) -> None:
    raise ValueError(
        "Published Treatment Outcomes are append-only; record a later outcome instead"
    )


@event.listens_for(TreatmentOutcome, "before_delete")
def _prevent_treatment_outcome_delete(_mapper, _connection, _target) -> None:
    raise ValueError(
        "Published Treatment Outcomes cannot be deleted; preserve longitudinal history"
    )
