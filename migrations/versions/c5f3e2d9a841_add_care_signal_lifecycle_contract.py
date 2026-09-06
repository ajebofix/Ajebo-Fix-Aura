"""Add Wave 2.4C recurrence-safe care-signal and system-actor contract.

Revision ID: c5f3e2d9a841
Revises: b4e2d1c8a730
Create Date: 2026-09-06

No historical care-signal VehicleEvents are synthesized. Existing alert status
metadata is reconciled only where its explicit timestamps/active flag determine
the semantic state without inference.
"""

from alembic import op
import sqlalchemy as sa


revision = "c5f3e2d9a841"
down_revision = "b4e2d1c8a730"
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


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _canonical_pair_condition(*, include_care_signals: bool) -> str:
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
    ]
    if include_care_signals:
        parts.append(
            "(subject_type = 'vehicle_health_alert' AND event_type IN "
            f"({_quoted(CARE_SIGNAL_EVENT_TYPES)}))"
        )
    return " OR ".join(parts)


def _care_signal_event_contract() -> str:
    advisor_actor = (
        "actor_type IS NOT NULL AND actor_type = 'user' "
        "AND actor_user_id IS NOT NULL "
        "AND created_by IS NOT NULL AND created_by = actor_user_id "
        "AND actor_authority IS NOT NULL "
        "AND actor_authority IN ('advisor', 'administrator')"
    )
    system_actor = (
        "actor_type IS NOT NULL AND actor_type = 'system' "
        "AND actor_user_id IS NULL "
        "AND created_by IS NULL "
        "AND actor_authority IS NOT NULL AND actor_authority = 'system'"
    )
    return (
        "subject_type IS NULL "
        "OR subject_type <> 'vehicle_health_alert' "
        "OR ("
        "progression_direction IS NOT NULL "
        "AND progression_direction = 'not_applicable' "
        "AND ("
        "(event_type = 'care_signal.raised' "
        "AND previous_state IS NULL "
        "AND new_state IS NOT NULL AND new_state = 'new' "
        "AND visibility IS NOT NULL AND visibility = 'client' "
        f"AND (({advisor_actor}) OR ({system_actor}))) "
        "OR (event_type = 'care_signal.acknowledged' "
        "AND previous_state IS NOT NULL AND previous_state = 'new' "
        "AND new_state IS NOT NULL AND new_state = 'acknowledged' "
        "AND visibility IS NOT NULL AND visibility = 'advisor' "
        f"AND ({advisor_actor})) "
        "OR (event_type = 'care_signal.resolved' "
        "AND previous_state IS NOT NULL "
        "AND previous_state IN ('new', 'acknowledged') "
        "AND new_state IS NOT NULL AND new_state = 'resolved' "
        "AND visibility IS NOT NULL AND visibility = 'client' "
        f"AND (({advisor_actor}) OR ({system_actor})))"
        ")"
        ")"
    )


def _system_actor_scope_contract() -> str:
    return (
        "actor_type IS NULL "
        "OR actor_type <> 'system' "
        "OR ("
        "subject_type IS NOT NULL AND subject_type = 'vehicle_health_alert' "
        "AND event_type IS NOT NULL "
        "AND event_type IN ('care_signal.raised', 'care_signal.resolved') "
        "AND actor_user_id IS NULL "
        "AND created_by IS NULL "
        "AND actor_authority IS NOT NULL AND actor_authority = 'system'"
        ")"
    )


def _alert_state_contract() -> str:
    return (
        "status IS NOT NULL "
        "AND status IN ('new', 'acknowledged', 'resolved') "
        "AND ((acknowledged_at IS NULL AND acknowledged_by_id IS NULL) "
        "OR (acknowledged_at IS NOT NULL AND acknowledged_by_id IS NOT NULL)) "
        "AND ("
        "(status = 'new' "
        "AND is_active IS TRUE "
        "AND resolved_at IS NULL "
        "AND acknowledged_at IS NULL "
        "AND acknowledged_by_id IS NULL) "
        "OR (status = 'acknowledged' "
        "AND is_active IS TRUE "
        "AND resolved_at IS NULL "
        "AND acknowledged_at IS NOT NULL "
        "AND acknowledged_by_id IS NOT NULL) "
        "OR (status = 'resolved' "
        "AND is_active IS FALSE "
        "AND resolved_at IS NOT NULL)"
        ")"
    )


def _preflight_and_reconcile_alert_states(bind) -> None:
    duplicate_active = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT car_id, ownership_id, alert_type "
            "FROM vehicle_health_alerts WHERE is_active IS TRUE "
            "GROUP BY car_id, ownership_id, alert_type HAVING COUNT(*) > 1"
            ") AS duplicates"
        )
    ).scalar_one()
    if duplicate_active:
        raise RuntimeError(
            "Cannot install recurrence-safe care signals: "
            f"found {duplicate_active} duplicate active occurrence group(s)"
        )

    ambiguous_ack = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_health_alerts "
            "WHERE (acknowledged_at IS NULL) <> (acknowledged_by_id IS NULL)"
        )
    ).scalar_one()
    if ambiguous_ack:
        raise RuntimeError(
            "Cannot reconcile care-signal status: "
            f"found {ambiguous_ack} row(s) with ambiguous acknowledgement metadata"
        )

    invalid_resolved_storage = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_health_alerts "
            "WHERE (is_active IS FALSE AND resolved_at IS NULL) "
            "OR (is_active IS TRUE AND resolved_at IS NOT NULL)"
        )
    ).scalar_one()
    if invalid_resolved_storage:
        raise RuntimeError(
            "Cannot reconcile care-signal status without inventing history: "
            f"found {invalid_resolved_storage} inconsistent active/resolved row(s)"
        )

    # Explicit storage facts determine the semantic state. No timestamp, actor,
    # occurrence or VehicleEvent is manufactured here.
    bind.execute(
        sa.text(
            "UPDATE vehicle_health_alerts SET status = CASE "
            "WHEN is_active IS FALSE THEN 'resolved' "
            "WHEN acknowledged_at IS NOT NULL THEN 'acknowledged' "
            "ELSE 'new' END"
        )
    )


def _preflight_event_upgrade(bind) -> None:
    existing_care_subjects = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_events "
            "WHERE subject_type = 'vehicle_health_alert' "
            "OR event_type IN ('care_signal.raised', 'care_signal.acknowledged', 'care_signal.resolved')"
        )
    ).scalar_one()
    if existing_care_subjects:
        raise RuntimeError(
            "Cannot install Wave 2.4C over pre-existing care-signal canonical rows"
        )

    existing_system_events = bind.execute(
        sa.text("SELECT COUNT(*) FROM vehicle_events WHERE actor_type = 'system'")
    ).scalar_one()
    if existing_system_events:
        raise RuntimeError(
            "Cannot narrow system actor scope while pre-existing VehicleEvent system actors exist"
        )


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    _preflight_and_reconcile_alert_states(bind)
    _preflight_event_upgrade(bind)

    op.drop_constraint(
        "uq_active_health_alert",
        "vehicle_health_alerts",
        type_="unique",
    )
    op.create_index(
        "uq_vehicle_health_alert_active_occurrence",
        "vehicle_health_alerts",
        ["car_id", "ownership_id", "alert_type"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
    )
    op.create_check_constraint(
        "ck_vehicle_health_alert_state_contract",
        "vehicle_health_alerts",
        _alert_state_contract(),
    )

    op.alter_column(
        "vehicle_events",
        "created_by",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.drop_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        _canonical_pair_condition(include_care_signals=True),
    )
    op.create_check_constraint(
        "ck_vehicle_events_care_signal_contract",
        "vehicle_events",
        _care_signal_event_contract(),
    )
    op.create_check_constraint(
        "ck_vehicle_events_system_actor_scope",
        "vehicle_events",
        _system_actor_scope_contract(),
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    published_care_events = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_events "
            "WHERE subject_type = 'vehicle_health_alert' "
            "OR event_type IN ('care_signal.raised', 'care_signal.acknowledged', 'care_signal.resolved')"
        )
    ).scalar_one()
    if published_care_events:
        raise RuntimeError(
            "Cannot downgrade Wave 2.4C while published care-signal canonical history exists"
        )

    duplicate_legacy_groups = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT car_id, ownership_id, alert_type, is_active "
            "FROM vehicle_health_alerts "
            "GROUP BY car_id, ownership_id, alert_type, is_active "
            "HAVING COUNT(*) > 1"
            ") AS duplicates"
        )
    ).scalar_one()
    if duplicate_legacy_groups:
        raise RuntimeError(
            "Cannot restore legacy care-signal uniqueness while recurrent occurrences exist"
        )

    null_created_by = bind.execute(
        sa.text("SELECT COUNT(*) FROM vehicle_events WHERE created_by IS NULL")
    ).scalar_one()
    if null_created_by:
        raise RuntimeError(
            "Cannot restore legacy created_by NOT NULL while null event actors exist"
        )

    op.drop_constraint(
        "ck_vehicle_events_system_actor_scope",
        "vehicle_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_vehicle_events_care_signal_contract",
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
        _canonical_pair_condition(include_care_signals=False),
    )
    op.alter_column(
        "vehicle_events",
        "created_by",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.drop_constraint(
        "ck_vehicle_health_alert_state_contract",
        "vehicle_health_alerts",
        type_="check",
    )
    op.drop_index(
        "uq_vehicle_health_alert_active_occurrence",
        table_name="vehicle_health_alerts",
    )
    op.create_unique_constraint(
        "uq_active_health_alert",
        "vehicle_health_alerts",
        ["car_id", "ownership_id", "alert_type", "is_active"],
    )
