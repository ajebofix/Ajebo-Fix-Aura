"""Add Wave 2.4B driver-observation uniqueness and canonical event contract.

Revision ID: b4e2d1c8a730
Revises: e6a1c9f4b720
Create Date: 2026-09-06

No historical driver-observation events are synthesized. Existing check-ins are
used only to preflight the one-driver/vehicle/day uniqueness invariant.
"""

from alembic import op
import sqlalchemy as sa


revision = "b4e2d1c8a730"
down_revision = "e6a1c9f4b720"
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


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _canonical_pair_condition(*, include_driver: bool) -> str:
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
    ]
    if include_driver:
        parts.append(
            "(subject_type = 'driver_checkin' "
            "AND event_type IS NOT NULL "
            f"AND event_type IN ({_quoted(DRIVER_OBSERVATION_EVENT_TYPES)}))"
        )
    return " OR ".join(parts)


def _driver_event_contract_condition() -> str:
    return (
        "subject_type IS NULL "
        "OR subject_type <> 'driver_checkin' "
        "OR ("
        "event_type IS NOT NULL "
        "AND event_type = 'driver_observation.checkin_recorded' "
        "AND previous_state IS NULL "
        "AND new_state IS NOT NULL "
        "AND new_state = 'recorded' "
        "AND progression_direction IS NOT NULL "
        "AND progression_direction = 'insufficient_evidence' "
        "AND actor_type IS NOT NULL "
        "AND actor_type = 'user' "
        "AND actor_user_id IS NOT NULL "
        "AND actor_authority IS NOT NULL "
        "AND actor_authority = 'driver' "
        "AND visibility IS NOT NULL "
        "AND visibility = 'advisor'"
        ")"
    )


def _preflight_driver_checkins(bind) -> None:
    null_timestamps = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM driver_checkins WHERE created_at IS NULL"
        )
    ).scalar_one()
    if null_timestamps:
        raise RuntimeError(
            "Cannot install driver operational-day uniqueness: "
            f"found {null_timestamps} check-in row(s) without created_at"
        )

    duplicate_days = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT driver_id, car_id, DATE(created_at) AS operational_day "
            "FROM driver_checkins "
            "GROUP BY driver_id, car_id, DATE(created_at) "
            "HAVING COUNT(*) > 1"
            ") AS duplicates"
        )
    ).scalar_one()
    if duplicate_days:
        raise RuntimeError(
            "Cannot install driver operational-day uniqueness: "
            f"found {duplicate_days} duplicate driver/vehicle/day group(s)"
        )


def upgrade():
    bind = op.get_bind()
    _preflight_driver_checkins(bind)

    if bind.dialect.name == "sqlite":
        op.execute(
            "CREATE UNIQUE INDEX uq_driver_checkins_driver_car_operational_day "
            "ON driver_checkins (driver_id, car_id, date(created_at))"
        )
        return

    op.alter_column(
        "driver_checkins",
        "created_at",
        existing_type=sa.DateTime(),
        nullable=False,
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_driver_checkins_driver_car_operational_day "
        "ON driver_checkins (driver_id, car_id, (created_at::date))"
    )

    invalid_driver_pairs = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_events "
            "WHERE subject_type = 'driver_checkin'"
        )
    ).scalar_one()
    if invalid_driver_pairs:
        raise RuntimeError(
            "Cannot install Wave 2.4B event contract over pre-existing "
            "driver_checkin canonical rows"
        )

    op.drop_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        _canonical_pair_condition(include_driver=True),
    )
    op.create_check_constraint(
        "ck_vehicle_events_driver_observation_contract",
        "vehicle_events",
        _driver_event_contract_condition(),
    )


def downgrade():
    bind = op.get_bind()

    if bind.dialect.name == "sqlite":
        op.execute("DROP INDEX uq_driver_checkins_driver_car_operational_day")
        return

    published_driver_events = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_events "
            "WHERE subject_type = 'driver_checkin' "
            "OR event_type = 'driver_observation.checkin_recorded'"
        )
    ).scalar_one()
    if published_driver_events:
        raise RuntimeError(
            "Cannot downgrade Wave 2.4B while published driver-observation "
            "canonical history exists"
        )

    op.drop_constraint(
        "ck_vehicle_events_driver_observation_contract",
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
        _canonical_pair_condition(include_driver=False),
    )

    op.execute("DROP INDEX uq_driver_checkins_driver_car_operational_day")
    op.alter_column(
        "driver_checkins",
        "created_at",
        existing_type=sa.DateTime(),
        nullable=True,
    )
