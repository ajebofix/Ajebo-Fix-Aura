"""Extend canonical VehicleEvent PostgreSQL checks for consultation events.

Revision ID: e8f5c1a7b240
Revises: d7e4a9c2f160
Create Date: 2026-08-21

Wave 2.2A added application-level consultation event semantics after the Wave 1.4
PostgreSQL subject/event constraint had already been installed. This migration
widens that database contract without rewriting historical rows.
"""

from alembic import op
import sqlalchemy as sa


revision = "e8f5c1a7b240"
down_revision = "d7e4a9c2f160"
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


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _previous_pair_condition() -> str:
    return (
        "subject_type IS NULL "
        "OR (subject_type = 'reported_concern' AND event_type IN "
        f"({_quoted(CONCERN_EVENT_TYPES)})) "
        "OR (subject_type = 'vehicle_evidence' AND event_type IN "
        f"({_quoted(EVIDENCE_EVENT_TYPES)}))"
    )


def _canonical_pair_condition() -> str:
    return (
        _previous_pair_condition()
        + " OR (subject_type = 'consultation' AND event_type IN "
        + f"({_quoted(CONSULTATION_EVENT_TYPES)}))"
    )


def _consultation_contract_condition() -> str:
    return (
        "subject_type IS NULL "
        "OR subject_type <> 'consultation' "
        "OR ("
        "progression_direction IS NOT NULL "
        "AND progression_direction = 'not_applicable' "
        "AND ("
        "(event_type = 'consultation.requested' "
        "AND previous_state IS NULL "
        "AND new_state = 'requested') "
        "OR (event_type = 'consultation.scheduled' "
        "AND (previous_state IS NULL OR previous_state IN ('requested', 'deferred')) "
        "AND new_state = 'scheduled') "
        "OR (event_type = 'consultation.started' "
        "AND previous_state = 'scheduled' "
        "AND new_state = 'in_progress') "
        "OR (event_type = 'consultation.completed' "
        "AND previous_state = 'in_progress' "
        "AND new_state = 'completed')"
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

    invalid_consultations = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*)
            FROM vehicle_events
            WHERE NOT ({_consultation_contract_condition()})
            """
        )
    ).scalar_one()
    if invalid_consultations:
        raise RuntimeError(
            "Cannot add consultation event contract: "
            f"found {invalid_consultations} incompatible consultation row(s)"
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
        "ck_vehicle_events_consultation_contract",
        "vehicle_events",
        _consultation_contract_condition(),
    )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        return

    op.drop_constraint(
        "ck_vehicle_events_consultation_contract",
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
