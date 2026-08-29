"""Extend canonical VehicleEvent checks for Treatment Plan events.

Revision ID: c2f7a8e4d910
Revises: a9d3e6f1c420
Create Date: 2026-08-29

Wave 2.3B adds canonical Treatment Plan lifecycle facts without synthesizing
history for legacy `approved` TreatmentPlan rows.
"""

from alembic import op
import sqlalchemy as sa


revision = "c2f7a8e4d910"
down_revision = "a9d3e6f1c420"
branch_labels = None
depends_on = None


CONCERN_EVENT_TYPES = (
    "concern.reported",
    "concern.review_started",
    "concern.monitoring_started",
    "concern.resolved",
    "concern.reopened",
    "concern.corrected",
)
EVIDENCE_EVENT_TYPES = ("evidence.reviewed", "evidence.linked")
CONSULTATION_EVENT_TYPES = (
    "consultation.requested",
    "consultation.scheduled",
    "consultation.started",
    "consultation.completed",
)
ASSESSMENT_EVENT_TYPES = (
    "assessment.created",
    "assessment.finalized",
    "assessment.corrected",
)
TREATMENT_EVENT_TYPES = (
    "treatment.proposed",
    "treatment.authorized",
    "treatment.scheduled",
    "treatment.started",
    "treatment.monitoring_started",
    "treatment.completed",
    "treatment.deferred",
    "treatment.cancelled",
    "treatment.escalated",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _base_pair_condition(*, include_treatment: bool) -> str:
    parts = [
        "subject_type IS NULL",
        "(subject_type = 'reported_concern' AND event_type IN "
        f"({_quoted(CONCERN_EVENT_TYPES)}))",
        "(subject_type = 'vehicle_evidence' AND event_type IN "
        f"({_quoted(EVIDENCE_EVENT_TYPES)}))",
        "(subject_type = 'consultation' AND event_type IN "
        f"({_quoted(CONSULTATION_EVENT_TYPES)}))",
        "(subject_type = 'vehicle_assessment' AND event_type IN "
        f"({_quoted(ASSESSMENT_EVENT_TYPES)}))",
    ]
    if include_treatment:
        parts.append(
            "(subject_type = 'treatment_plan' AND event_type IN "
            f"({_quoted(TREATMENT_EVENT_TYPES)}))"
        )
    return " OR ".join(parts)


def _treatment_contract_condition() -> str:
    nonterminal_states = (
        "'proposed', 'authorized', 'scheduled', 'in_progress', "
        "'monitoring', 'deferred', 'approved'"
    )
    return (
        "subject_type IS NULL "
        "OR subject_type <> 'treatment_plan' "
        "OR ("
        "progression_direction IS NOT NULL "
        "AND progression_direction = 'not_applicable' "
        "AND ("
        "(event_type = 'treatment.proposed' "
        "AND previous_state IS NULL "
        "AND new_state IS NOT NULL "
        "AND new_state = 'proposed') "
        "OR (event_type = 'treatment.authorized' "
        "AND previous_state IS NOT NULL "
        "AND previous_state IN ('proposed', 'deferred') "
        "AND new_state IS NOT NULL "
        "AND new_state = 'authorized') "
        "OR (event_type = 'treatment.scheduled' "
        "AND previous_state IS NOT NULL "
        "AND previous_state IN ('authorized', 'deferred', 'approved') "
        "AND new_state IS NOT NULL "
        "AND new_state = 'scheduled') "
        "OR (event_type = 'treatment.started' "
        "AND previous_state IS NOT NULL "
        "AND previous_state IN ('authorized', 'scheduled', 'approved', 'monitoring') "
        "AND new_state IS NOT NULL "
        "AND new_state = 'in_progress') "
        "OR (event_type = 'treatment.monitoring_started' "
        "AND previous_state IS NOT NULL "
        "AND previous_state = 'in_progress' "
        "AND new_state IS NOT NULL "
        "AND new_state = 'monitoring') "
        "OR (event_type = 'treatment.completed' "
        "AND previous_state IS NOT NULL "
        "AND previous_state IN ('in_progress', 'monitoring') "
        "AND new_state IS NOT NULL "
        "AND new_state = 'completed') "
        "OR (event_type = 'treatment.deferred' "
        "AND previous_state IS NOT NULL "
        "AND previous_state IN ('proposed', 'authorized', 'scheduled', 'approved') "
        "AND new_state IS NOT NULL "
        "AND new_state = 'deferred') "
        "OR (event_type = 'treatment.cancelled' "
        "AND previous_state IS NOT NULL "
        "AND previous_state IN ('proposed', 'authorized', 'scheduled', 'deferred', 'approved') "
        "AND new_state IS NOT NULL "
        "AND new_state = 'cancelled') "
        "OR (event_type = 'treatment.escalated' "
        "AND previous_state IS NOT NULL "
        f"AND previous_state IN ({nonterminal_states}) "
        "AND new_state IS NOT NULL "
        "AND new_state = previous_state)"
        ")"
        ")"
    )


def _preflight_upgrade() -> None:
    bind = op.get_bind()
    invalid_pairs = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_events "
            f"WHERE NOT ({_base_pair_condition(include_treatment=True)})"
        )
    ).scalar_one()
    if invalid_pairs:
        raise RuntimeError(
            "Cannot extend Treatment Plan event contract: "
            f"found {invalid_pairs} incompatible canonical row(s)"
        )

    invalid_treatments = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_events "
            "WHERE subject_type = 'treatment_plan' "
            f"AND NOT ({_treatment_contract_condition()})"
        )
    ).scalar_one()
    if invalid_treatments:
        raise RuntimeError(
            "Cannot add Treatment Plan event contract: "
            f"found {invalid_treatments} incompatible treatment row(s)"
        )


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        return

    _preflight_upgrade()
    op.drop_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        _base_pair_condition(include_treatment=True),
    )
    op.create_check_constraint(
        "ck_vehicle_events_treatment_plan_contract",
        "vehicle_events",
        _treatment_contract_condition(),
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    treatment_event_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_events "
            "WHERE subject_type = 'treatment_plan' "
            f"AND event_type IN ({_quoted(TREATMENT_EVENT_TYPES)})"
        )
    ).scalar_one()
    if treatment_event_count:
        raise RuntimeError(
            "Cannot downgrade Wave 2.3B while canonical Treatment Plan history exists"
        )

    op.drop_constraint(
        "ck_vehicle_events_treatment_plan_contract",
        "vehicle_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        _base_pair_condition(include_treatment=False),
    )
