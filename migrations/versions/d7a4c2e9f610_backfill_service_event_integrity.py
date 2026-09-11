"""Backfill post-envelope service events into the canonical event envelope.

Revision ID: d7a4c2e9f610
Revises: c3e1f6a9d842
Create Date: 2026-09-11

The legacy service-history helper continued writing VehicleEvent rows after the
Wave 1.2 envelope migration without populating required canonical fields. This
migration repairs only deterministic service-record facts and expands the
subject/event compatibility constraint so future service records can be stored
as integrity-complete legacy service records.
"""

from alembic import op
import sqlalchemy as sa


revision = "d7a4c2e9f610"
down_revision = "c3e1f6a9d842"
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
TREATMENT_PLAN_EVENT_TYPES = (
    "treatment.proposed",
    "treatment.authorized",
    "treatment.scheduled",
    "treatment.started",
    "treatment.monitoring_started",
    "treatment.completed",
    "treatment.deferred",
    "treatment.cancelled",
    "treatment.escalated",
    "treatment.outcome_recorded",
)
TREATMENT_ACTION_EVENT_TYPES = (
    "treatment_action.created",
    "treatment_action.scheduled",
    "treatment_action.started",
    "treatment_action.completed",
    "treatment_action.deferred",
    "treatment_action.cancelled",
)
DRIVER_OBSERVATION_EVENT_TYPES = ("driver_observation.checkin_recorded",)
CARE_SIGNAL_EVENT_TYPES = (
    "care_signal.raised",
    "care_signal.acknowledged",
    "care_signal.resolved",
)
PRIORITY_EVENT_TYPES = (
    "priority.requested",
    "priority.review_started",
    "priority.accepted",
    "priority.deferred",
    "priority.resolved",
    "priority.cancelled",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _canonical_pair_condition(*, include_service: bool) -> str:
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
        "(subject_type = 'treatment_plan' AND event_type IN "
        f"({_quoted(TREATMENT_PLAN_EVENT_TYPES)}))",
        "(subject_type = 'treatment_action' AND event_type IN "
        f"({_quoted(TREATMENT_ACTION_EVENT_TYPES)}))",
        "(subject_type = 'driver_checkin' AND event_type IN "
        f"({_quoted(DRIVER_OBSERVATION_EVENT_TYPES)}))",
        "(subject_type = 'vehicle_health_alert' AND event_type IN "
        f"({_quoted(CARE_SIGNAL_EVENT_TYPES)}))",
        "(subject_type = 'priority_request' AND event_type IN "
        f"({_quoted(PRIORITY_EVENT_TYPES)}))",
    ]
    if include_service:
        parts.append("(subject_type = 'service_record' AND event_type = 'service')")
    return " OR ".join(parts)


def _preflight(bind) -> None:
    invalid_subjects = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM vehicle_events
            WHERE event_type = 'service'
              AND subject_type IS NOT NULL
              AND subject_type <> 'service_record'
            """
        )
    ).scalar_one()
    if invalid_subjects:
        raise RuntimeError(
            "Cannot backfill service event integrity: service rows already use an "
            "unexpected subject_type"
        )

    invalid_directions = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM vehicle_events
            WHERE event_type = 'service'
              AND progression_direction IS NOT NULL
              AND progression_direction <> 'not_applicable'
            """
        )
    ).scalar_one()
    if invalid_directions:
        raise RuntimeError(
            "Cannot backfill service event integrity: service rows already use an "
            "unexpected progression_direction"
        )

    missing_occurrence_source = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM vehicle_events
            WHERE event_type = 'service'
              AND occurred_at IS NULL
              AND created_at IS NULL
            """
        )
    ).scalar_one()
    if missing_occurrence_source:
        raise RuntimeError(
            "Cannot backfill service event integrity: service rows are missing both "
            "occurred_at and the legacy created_at service timestamp"
        )


def _backfill(bind) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE vehicle_events
            SET schema_version = COALESCE(schema_version, 1),
                occurred_at = COALESCE(occurred_at, created_at),
                recorded_at = COALESCE(recorded_at, created_at),
                subject_type = COALESCE(subject_type, 'service_record'),
                subject_id = COALESCE(subject_id, id),
                actor_user_id = COALESCE(actor_user_id, created_by),
                actor_type = CASE
                    WHEN actor_type IS NULL
                         AND COALESCE(actor_user_id, created_by) IS NOT NULL
                    THEN 'user'
                    ELSE actor_type
                END,
                visibility = COALESCE(visibility, 'internal'),
                progression_direction = COALESCE(
                    progression_direction,
                    'not_applicable'
                )
            WHERE event_type = 'service'
            """
        )
    )

    remaining = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM vehicle_events
            WHERE event_type = 'service'
              AND (
                    car_id IS NULL
                 OR event_type IS NULL
                 OR subject_type IS NULL
                 OR subject_id IS NULL
                 OR occurred_at IS NULL
                 OR schema_version IS NULL
                 OR source IS NULL
                 OR progression_direction IS NULL
                 OR fingerprint IS NULL
              )
            """
        )
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            "Service event integrity backfill left required canonical fields missing"
        )


def upgrade():
    bind = op.get_bind()
    _preflight(bind)

    if bind.dialect.name != "sqlite":
        op.drop_constraint(
            "ck_vehicle_events_canonical_subject_event",
            "vehicle_events",
            type_="check",
        )

    _backfill(bind)

    if bind.dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_vehicle_events_canonical_subject_event",
            "vehicle_events",
            _canonical_pair_condition(include_service=True),
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    service_rows = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM vehicle_events
            WHERE event_type = 'service'
              AND subject_type = 'service_record'
            """
        )
    ).scalar_one()
    if service_rows:
        raise RuntimeError(
            "Cannot downgrade service event integrity while service_record history exists; "
            "use a forward-fix migration instead"
        )

    op.drop_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        _canonical_pair_condition(include_service=False),
    )
