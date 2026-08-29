"""Durable Treatment Action and Treatment Outcome records for Aura Wave 2.3C.

These rows deliberately separate professional intervention activity from the
Treatment Plan authorization/execution container and from later observed
outcomes. They do not imply diagnosis, concern resolution, or Vehicle Health
progression.
"""

from __future__ import annotations

from datetime import datetime

from extensions import db


TREATMENT_ACTION_STATES = (
    "planned",
    "scheduled",
    "in_progress",
    "completed",
    "deferred",
    "cancelled",
)
TREATMENT_ACTION_VISIBILITIES = ("client", "advisor")

TREATMENT_OUTCOME_DIRECTIONS = (
    "improving",
    "stable",
    "deteriorating",
    "resolved",
    "insufficient_evidence",
)
TREATMENT_OUTCOME_PROVENANCE = (
    "reviewed_evidence",
    "professional_observation",
    "insufficient_evidence",
)
TREATMENT_OUTCOME_VISIBILITIES = ("client", "advisor")


class TreatmentAction(db.Model):
    """One concrete professional intervention within one Treatment Plan."""

    __tablename__ = "treatment_actions"

    id = db.Column(db.Integer, primary_key=True)
    treatment_plan_id = db.Column(
        db.Integer,
        db.ForeignKey("treatment_plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Stable per-plan creation key used to make browser/API retries idempotent.
    creation_key = db.Column(db.String(128), nullable=False)

    title = db.Column(db.String(255), nullable=False)
    client_summary = db.Column(db.Text, nullable=True)
    internal_instructions = db.Column(db.Text, nullable=True)

    status = db.Column(
        db.String(32),
        nullable=False,
        default="planned",
        server_default="planned",
    )
    visibility = db.Column(
        db.String(20),
        nullable=False,
        default="client",
        server_default="client",
    )

    scheduled_for = db.Column(db.DateTime, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    deferred_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    plan = db.relationship(
        "TreatmentPlan",
        backref=db.backref("actions", order_by="TreatmentAction.id"),
    )
    car = db.relationship("Car", foreign_keys=[car_id])
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        db.UniqueConstraint(
            "treatment_plan_id",
            "creation_key",
            name="uq_treatment_actions_plan_creation_key",
        ),
        db.CheckConstraint(
            "status IN ('planned', 'scheduled', 'in_progress', 'completed', 'deferred', 'cancelled')",
            name="ck_treatment_actions_status",
        ),
        db.CheckConstraint(
            "visibility IN ('client', 'advisor')",
            name="ck_treatment_actions_visibility",
        ),
        db.CheckConstraint(
            "length(trim(creation_key)) > 0",
            name="ck_treatment_actions_creation_key_nonblank",
        ),
        db.CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_treatment_actions_title_nonblank",
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
            "id",
        ),
    )


class TreatmentOutcome(db.Model):
    """One additive advisor-reviewed observation about treatment outcome."""

    __tablename__ = "treatment_outcomes"

    id = db.Column(db.Integer, primary_key=True)
    treatment_plan_id = db.Column(
        db.Integer,
        db.ForeignKey("treatment_plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    treatment_action_id = db.Column(
        db.Integer,
        db.ForeignKey("treatment_actions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    car_id = db.Column(
        db.Integer,
        db.ForeignKey("cars.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    recorded_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    recording_key = db.Column(db.String(128), nullable=False)
    progression_direction = db.Column(db.String(32), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    advisor_note = db.Column(db.Text, nullable=True)
    visibility = db.Column(
        db.String(20),
        nullable=False,
        default="client",
        server_default="client",
    )

    provenance_kind = db.Column(db.String(40), nullable=False)
    provenance_data = db.Column(db.JSON, nullable=True)

    observed_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    plan = db.relationship(
        "TreatmentPlan",
        backref=db.backref("outcomes", order_by="TreatmentOutcome.id"),
    )
    action = db.relationship(
        "TreatmentAction",
        backref=db.backref("outcomes", order_by="TreatmentOutcome.id"),
    )
    car = db.relationship("Car", foreign_keys=[car_id])
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_user_id])

    __table_args__ = (
        db.UniqueConstraint(
            "treatment_plan_id",
            "recording_key",
            name="uq_treatment_outcomes_plan_recording_key",
        ),
        db.CheckConstraint(
            "progression_direction IN ('improving', 'stable', 'deteriorating', 'resolved', 'insufficient_evidence')",
            name="ck_treatment_outcomes_progression",
        ),
        db.CheckConstraint(
            "visibility IN ('client', 'advisor')",
            name="ck_treatment_outcomes_visibility",
        ),
        db.CheckConstraint(
            "provenance_kind IN ('reviewed_evidence', 'professional_observation', 'insufficient_evidence')",
            name="ck_treatment_outcomes_provenance",
        ),
        db.CheckConstraint(
            "(progression_direction = 'insufficient_evidence') OR (provenance_kind <> 'insufficient_evidence')",
            name="ck_treatment_outcomes_progression_provenance",
        ),
        db.CheckConstraint(
            "(provenance_kind <> 'professional_observation') OR (provenance_data IS NOT NULL)",
            name="ck_treatment_outcomes_observation_provenance",
        ),
        db.CheckConstraint(
            "length(trim(recording_key)) > 0",
            name="ck_treatment_outcomes_recording_key_nonblank",
        ),
        db.CheckConstraint(
            "length(trim(summary)) > 0",
            name="ck_treatment_outcomes_summary_nonblank",
        ),
        db.Index(
            "ix_treatment_outcomes_plan_time",
            "treatment_plan_id",
            "observed_at",
            "id",
        ),
        db.Index(
            "ix_treatment_outcomes_car_time",
            "car_id",
            "observed_at",
            "id",
        ),
    )
