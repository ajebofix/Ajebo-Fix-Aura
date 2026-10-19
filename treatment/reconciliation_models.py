"""Append-only retrospective reconciliation attestations for previously performed work.

Does NOT mutate Treatment Plan/Action lifecycle statuses. An advisor attestation
does not prove successful outcome, resolve a concern, or alter billing/custody.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import event
from extensions import db


class TreatmentReconciliation(db.Model):
    __tablename__ = "treatment_reconciliations"

    id = db.Column(db.Integer, primary_key=True)
    treatment_plan_id = db.Column(
        db.Integer, db.ForeignKey("treatment_plans.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    car_id = db.Column(
        db.Integer, db.ForeignKey("cars.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    treatment_action_id = db.Column(
        db.Integer, db.ForeignKey("treatment_actions.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    actor_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind = db.Column(db.String(16), nullable=False)
    finding = db.Column(db.String(24), nullable=False)
    provenance = db.Column(db.String(24), nullable=False)
    occurred_on = db.Column(db.Date, nullable=True)
    description = db.Column(db.Text, nullable=False)
    recipient = db.Column(db.String(200), nullable=True)
    location = db.Column(db.String(240), nullable=True)
    evidence_id = db.Column(
        db.Integer, db.ForeignKey("vehicle_evidence.id", ondelete="SET NULL"),
        nullable=True,
    )
    operation_key = db.Column(db.String(128), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.CheckConstraint("kind IN ('action', 'handover')", name="ck_treatment_reconcile_kind"),
        db.CheckConstraint(
            "finding IN ('performed', 'not_performed', 'unverified', 'handed_over')",
            name="ck_treatment_reconcile_finding",
        ),
        db.CheckConstraint(
            "provenance IN ('advisor_firsthand', 'reviewed_evidence', 'client_report')",
            name="ck_treatment_reconcile_provenance",
        ),
        db.CheckConstraint(
            "(kind = 'action' AND treatment_action_id IS NOT NULL AND finding <> 'handed_over') "
            "OR (kind = 'handover' AND treatment_action_id IS NULL AND finding = 'handed_over')",
            name="ck_treatment_reconcile_scope",
        ),
        db.CheckConstraint("length(trim(description)) > 0", name="ck_treatment_reconcile_description"),
        db.CheckConstraint("length(trim(operation_key)) > 0", name="ck_treatment_reconcile_key"),
    )


@event.listens_for(TreatmentReconciliation, "before_update")
def _no_reconciliation_update(_mapper, _connection, _target):
    raise ValueError("Reconciliation records are immutable. Add a corrected entry instead.")


@event.listens_for(TreatmentReconciliation, "before_delete")
def _no_reconciliation_delete(_mapper, _connection, _target):
    raise ValueError("Reconciliation records cannot be deleted; preserve the audit trail.")
