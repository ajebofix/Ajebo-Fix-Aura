"""Extend canonical VehicleEvent PostgreSQL checks for assessment events.

Revision ID: f4c8a2d9b731
Revises: e8f5c1a7b240
Create Date: 2026-08-24

Wave 2.2B2 adds only the approved Vehicle Assessment lifecycle facts while
preserving all concern, evidence, consultation, and legacy subject-less rows.
"""

from alembic import op
import sqlalchemy as sa


revision = "f4c8a2d9b731"
down_revision = "e8f5c1a7b240"
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
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _previous_pair_condition() -> str:
    return (
        "subject_type IS NULL "
        "OR (subject_type = 'reported_concern' AND event_type IN "
        f"({_quoted(CONCERN_EVENT_TYPES)})) "
        "OR (subject_type = 'vehicle_evidence' AND event_type IN "
        f"({_quoted(EVIDENCE_EVENT_TYPES)})) "
        "OR (subject_type = 'consultation' AND event_type IN "
        f"({_quoted(CONSULTATION_EVENT_TYPES)}))"
    )


def _canonical_pair_condition() -> str:
    return (
        _previous_pair_condition()
        + " OR (subject_type = 'vehicle_assessment' AND event_type IN "
        + f"({_quoted(ASSESSMENT_EVENT_TYPES)}))"
    )


def _assessment_contract_condition() -> str:
    # PostgreSQL CHECK treats UNKNOWN as passing. Explicit IS [NOT] NULL guards
    # make this contract fail closed instead of allowing a missing transition
    # state to turn the predicate into UNKNOWN.
    return (
        "subject_type IS NULL "
        "OR subject_type <> 'vehicle_assessment' "
        "OR ("
        "progression_direction IS NOT NULL "
        "AND progression_direction = 'not_applicable' "
        "AND ("
        "(event_type = 'assessment.created' "
        "AND previous_state IS NULL "
        "AND new_state IS NOT NULL "
        "AND new_state = 'draft') "
        "OR (event_type = 'assessment.finalized' "
        "AND previous_state IS NOT NULL "
        "AND previous_state = 'draft' "
        "AND new_state IS NOT NULL "
        "AND new_state = 'finalized')"
        ")"
        ")"
    )


def _preflight() -> None:
    bind = op.get_bind()

    invalid_pairs = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*)
            FROM vehicle_events
            WHERE NOT ({_canonical_pair_condition()})
            """
        )
    ).scalar_one()
    if invalid_pairs:
        raise RuntimeError(
            "Cannot extend canonical subject/event constraint: "
            f"found {invalid_pairs} incompatible canonical row(s)"
        )

    invalid_assessments = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*)
            FROM vehicle_events
            WHERE subject_type = 'vehicle_assessment'
              AND NOT ({_assessment_contract_condition()})
            """
        )
    ).scalar_one()
    if invalid_assessments:
        raise RuntimeError(
            "Cannot add assessment event contract: "
            f"found {invalid_assessments} incompatible assessment row(s)"
        )


def upgrade():
    _preflight()

    if op.get_bind().dialect.name == "sqlite":
        return

    op.drop_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        _canonical_pair_condition(),
    )
    op.create_check_constraint(
        "ck_vehicle_events_assessment_contract",
        "vehicle_events",
        _assessment_contract_condition(),
    )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        return

    op.drop_constraint(
        "ck_vehicle_events_assessment_contract",
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
        _previous_pair_condition(),
    )
