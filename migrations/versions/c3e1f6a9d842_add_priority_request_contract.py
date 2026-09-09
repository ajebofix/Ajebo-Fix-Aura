"""Add Wave 2.4D durable PriorityRequest and canonical priority events.

Revision ID: c3e1f6a9d842
Revises: b2d0f5e8c731
Create Date: 2026-09-09

No historical priority requests or priority VehicleEvents are synthesized.
"""

from alembic import op
import sqlalchemy as sa


revision = "c3e1f6a9d842"
down_revision = "b2d0f5e8c731"
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


def _canonical_pair_condition(*, include_priority: bool) -> str:
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
    ]
    if include_priority:
        parts.append(
            "(subject_type = 'priority_request' AND event_type IN "
            f"({_quoted(PRIORITY_EVENT_TYPES)}))"
        )
    return " OR ".join(parts)


def _priority_event_contract() -> str:
    human = (
        "actor_type IS NOT NULL AND actor_type = 'user' "
        "AND actor_user_id IS NOT NULL "
        "AND created_by IS NOT NULL AND created_by = actor_user_id "
        "AND actor_authority IS NOT NULL"
    )
    owner_or_advisor = f"({human} AND actor_authority IN ('owner','advisor','administrator'))"
    advisor = f"({human} AND actor_authority IN ('advisor','administrator'))"
    return (
        "subject_type IS NULL "
        "OR subject_type <> 'priority_request' "
        "OR (progression_direction IS NOT NULL "
        "AND progression_direction = 'not_applicable' "
        "AND ("
        "(event_type = 'priority.requested' "
        "AND previous_state IS NULL AND new_state = 'requested' "
        "AND visibility = 'client' AND " + owner_or_advisor + ") "
        "OR (event_type = 'priority.review_started' "
        "AND previous_state IN ('requested','deferred') "
        "AND new_state = 'under_review' "
        "AND visibility = 'advisor' AND " + advisor + ") "
        "OR (event_type = 'priority.accepted' "
        "AND previous_state = 'under_review' AND new_state = 'accepted' "
        "AND visibility = 'client' AND " + advisor + ") "
        "OR (event_type = 'priority.deferred' "
        "AND previous_state IN ('requested','under_review') "
        "AND new_state = 'deferred' "
        "AND visibility = 'client' AND " + advisor + ") "
        "OR (event_type = 'priority.resolved' "
        "AND previous_state = 'accepted' AND new_state = 'resolved' "
        "AND visibility = 'client' AND " + advisor + ") "
        "OR (event_type = 'priority.cancelled' "
        "AND previous_state IN ('requested','under_review','deferred') "
        "AND new_state = 'cancelled' "
        "AND visibility = 'client' AND " + owner_or_advisor + ")"
        "))"
    )


def _priority_state_contract() -> str:
    return (
        "status IS NOT NULL AND status IN "
        "('requested','under_review','accepted','deferred','resolved','cancelled') "
        "AND requested_at IS NOT NULL "
        "AND ((status = 'requested' AND resolved_at IS NULL AND cancelled_at IS NULL) "
        "OR (status = 'under_review' AND review_started_at IS NOT NULL "
        "AND resolved_at IS NULL AND cancelled_at IS NULL) "
        "OR (status = 'accepted' AND accepted_at IS NOT NULL "
        "AND resolved_at IS NULL AND cancelled_at IS NULL) "
        "OR (status = 'deferred' AND deferred_at IS NOT NULL "
        "AND resolved_at IS NULL AND cancelled_at IS NULL) "
        "OR (status = 'resolved' AND accepted_at IS NOT NULL AND resolved_at IS NOT NULL "
        "AND cancelled_at IS NULL) "
        "OR (status = 'cancelled' AND cancelled_at IS NOT NULL AND resolved_at IS NULL))"
    )


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "priority_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("ownership_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("resolved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("consultation_id", sa.Integer(), nullable=True),
        sa.Column("request_source", sa.String(length=32), nullable=False),
        sa.Column("request_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="requested"),
        sa.Column("reason_summary", sa.String(length=500), nullable=False),
        sa.Column("advisor_review_note", sa.Text(), nullable=True),
        sa.Column("eligibility_at_request", sa.Boolean(), nullable=False),
        sa.Column("care_plan_snapshot", sa.String(length=50), nullable=True),
        sa.Column("health_status_snapshot", sa.String(length=50), nullable=True),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("review_started_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("deferred_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ownership_id"], ["car_ownership.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["consultation_id"], ["consultations.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("request_key", name="uq_priority_request_key"),
        sa.CheckConstraint(
            "request_source IN ('owner','advisor','rina_structured_request')",
            name="ck_priority_request_source",
        ),
        sa.CheckConstraint(
            "request_kind IN ('priority','emergency_review')",
            name="ck_priority_request_kind",
        ),
        sa.CheckConstraint(_priority_state_contract(), name="ck_priority_request_state"),
    )
    op.create_index("idx_priority_request_car", "priority_requests", ["car_id"])
    op.create_index("idx_priority_request_ownership", "priority_requests", ["ownership_id"])
    op.create_index("idx_priority_request_status", "priority_requests", ["status"])
    op.create_index("idx_priority_request_consultation", "priority_requests", ["consultation_id"])
    if bind.dialect.name == "postgresql":
        op.create_index(
            "uq_priority_request_active_occurrence",
            "priority_requests",
            ["car_id", "ownership_id", "request_kind"],
            unique=True,
            postgresql_where=sa.text(
                "status IN ('requested','under_review','accepted','deferred')"
            ),
        )
    else:
        op.create_index(
            "idx_priority_request_active_lookup",
            "priority_requests",
            ["car_id", "ownership_id", "request_kind", "status"],
            unique=False,
        )

    if bind.dialect.name != "sqlite":
        existing_priority = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM vehicle_events "
                "WHERE subject_type = 'priority_request' OR event_type LIKE 'priority.%'"
            )
        ).scalar_one()
        if existing_priority:
            raise RuntimeError(
                "Cannot install Wave 2.4D over pre-existing priority canonical rows"
            )

        op.drop_constraint(
            "ck_vehicle_events_canonical_subject_event",
            "vehicle_events",
            type_="check",
        )
        op.create_check_constraint(
            "ck_vehicle_events_canonical_subject_event",
            "vehicle_events",
            _canonical_pair_condition(include_priority=True),
        )
        op.create_check_constraint(
            "ck_vehicle_events_priority_contract",
            "vehicle_events",
            _priority_event_contract(),
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        published = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM vehicle_events "
                "WHERE subject_type = 'priority_request' OR event_type LIKE 'priority.%'"
            )
        ).scalar_one()
        rows = bind.execute(sa.text("SELECT COUNT(*) FROM priority_requests")).scalar_one()
        if published or rows:
            raise RuntimeError(
                "Cannot downgrade Wave 2.4D while durable priority history exists"
            )

        op.drop_constraint(
            "ck_vehicle_events_priority_contract",
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
            _canonical_pair_condition(include_priority=False),
        )
        op.drop_index("uq_priority_request_active_occurrence", table_name="priority_requests")
    else:
        op.drop_index("idx_priority_request_active_lookup", table_name="priority_requests")

    op.drop_index("idx_priority_request_consultation", table_name="priority_requests")
    op.drop_index("idx_priority_request_status", table_name="priority_requests")
    op.drop_index("idx_priority_request_ownership", table_name="priority_requests")
    op.drop_index("idx_priority_request_car", table_name="priority_requests")
    op.drop_table("priority_requests")
