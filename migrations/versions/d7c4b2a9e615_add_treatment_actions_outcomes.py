"""Add Treatment Actions, Outcomes and Wave 2.3C canonical events.

Revision ID: d7c4b2a9e615
Revises: c2f7a8e4d910
Create Date: 2026-08-29

Wave 2.3C adds durable professional intervention and outcome records without
synthesizing history from legacy Treatment Plans or service events.
"""

from alembic import op
import sqlalchemy as sa


revision = "d7c4b2a9e615"
down_revision = "c2f7a8e4d910"
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
TREATMENT_PLAN_EVENT_TYPES_BEFORE = (
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
TREATMENT_PLAN_EVENT_TYPES_AFTER = (
    *TREATMENT_PLAN_EVENT_TYPES_BEFORE,
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
OUTCOME_DIRECTIONS = (
    "improving",
    "stable",
    "deteriorating",
    "resolved",
    "insufficient_evidence",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _base_pair_condition(*, include_wave_2_3c: bool) -> str:
    treatment_events = (
        TREATMENT_PLAN_EVENT_TYPES_AFTER
        if include_wave_2_3c
        else TREATMENT_PLAN_EVENT_TYPES_BEFORE
    )
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
        f"({_quoted(treatment_events)}))",
    ]
    if include_wave_2_3c:
        parts.append(
            "(subject_type = 'treatment_action' AND event_type IN "
            f"({_quoted(TREATMENT_ACTION_EVENT_TYPES)}))"
        )
    return " OR ".join(parts)


def _treatment_plan_contract_condition(*, include_outcome: bool) -> str:
    nonterminal_states = (
        "'proposed', 'authorized', 'scheduled', 'in_progress', "
        "'monitoring', 'deferred', 'approved'"
    )
    lifecycle = (
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
    )

    branches = [f"({lifecycle})"]
    if include_outcome:
        branches.append(
            "(event_type = 'treatment.outcome_recorded' "
            "AND previous_state IS NOT NULL "
            "AND previous_state IN ('in_progress', 'monitoring', 'completed') "
            "AND new_state IS NOT NULL "
            "AND new_state = previous_state "
            "AND progression_direction IS NOT NULL "
            f"AND progression_direction IN ({_quoted(OUTCOME_DIRECTIONS)}))"
        )

    return (
        "subject_type IS NULL "
        "OR subject_type <> 'treatment_plan' "
        "OR ("
        + " OR ".join(branches)
        + ")"
    )


def _treatment_action_contract_condition() -> str:
    return (
        "subject_type IS NULL "
        "OR subject_type <> 'treatment_action' "
        "OR ("
        "progression_direction IS NOT NULL "
        "AND progression_direction = 'not_applicable' "
        "AND ("
        "(event_type = 'treatment_action.created' "
        "AND previous_state IS NULL "
        "AND new_state IS NOT NULL "
        "AND new_state = 'planned') "
        "OR (event_type = 'treatment_action.scheduled' "
        "AND previous_state IS NOT NULL "
        "AND previous_state IN ('planned', 'deferred') "
        "AND new_state IS NOT NULL "
        "AND new_state = 'scheduled') "
        "OR (event_type = 'treatment_action.started' "
        "AND previous_state IS NOT NULL "
        "AND previous_state = 'scheduled' "
        "AND new_state IS NOT NULL "
        "AND new_state = 'in_progress') "
        "OR (event_type = 'treatment_action.completed' "
        "AND previous_state IS NOT NULL "
        "AND previous_state = 'in_progress' "
        "AND new_state IS NOT NULL "
        "AND new_state = 'completed') "
        "OR (event_type = 'treatment_action.deferred' "
        "AND previous_state IS NOT NULL "
        "AND previous_state IN ('planned', 'scheduled') "
        "AND new_state IS NOT NULL "
        "AND new_state = 'deferred') "
        "OR (event_type = 'treatment_action.cancelled' "
        "AND previous_state IS NOT NULL "
        "AND previous_state IN ('planned', 'scheduled', 'deferred') "
        "AND new_state IS NOT NULL "
        "AND new_state = 'cancelled')"
        ")"
        ")"
    )


def _preflight_event_upgrade() -> None:
    bind = op.get_bind()

    invalid_pairs = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_events "
            f"WHERE NOT ({_base_pair_condition(include_wave_2_3c=True)})"
        )
    ).scalar_one()
    if invalid_pairs:
        raise RuntimeError(
            "Cannot extend Wave 2.3C canonical subjects/events: "
            f"found {invalid_pairs} incompatible canonical row(s)"
        )

    invalid_treatments = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_events "
            "WHERE subject_type = 'treatment_plan' "
            f"AND NOT ({_treatment_plan_contract_condition(include_outcome=True)})"
        )
    ).scalar_one()
    if invalid_treatments:
        raise RuntimeError(
            "Cannot extend Treatment Plan outcome contract: "
            f"found {invalid_treatments} incompatible treatment row(s)"
        )

    invalid_actions = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_events "
            "WHERE subject_type = 'treatment_action' "
            f"AND NOT ({_treatment_action_contract_condition()})"
        )
    ).scalar_one()
    if invalid_actions:
        raise RuntimeError(
            "Cannot add Treatment Action event contract: "
            f"found {invalid_actions} incompatible action row(s)"
        )


def _create_domain_tables() -> None:
    op.create_table(
        "treatment_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("treatment_plan_id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("creation_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("client_summary", sa.Text(), nullable=True),
        sa.Column("internal_instructions", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="planned",
        ),
        sa.Column(
            "visibility",
            sa.String(length=20),
            nullable=False,
            server_default="client",
        ),
        sa.Column("scheduled_for", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("deferred_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["treatment_plan_id"],
            ["treatment_plans.id"],
            name="fk_treatment_actions_treatment_plan_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["car_id"],
            ["cars.id"],
            name="fk_treatment_actions_car_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_treatment_actions_created_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "treatment_plan_id",
            "creation_key",
            name="uq_treatment_actions_plan_creation_key",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'scheduled', 'in_progress', 'completed', 'deferred', 'cancelled')",
            name="ck_treatment_actions_status",
        ),
        sa.CheckConstraint(
            "visibility IN ('client', 'advisor')",
            name="ck_treatment_actions_visibility",
        ),
        sa.CheckConstraint(
            "length(trim(creation_key)) > 0",
            name="ck_treatment_actions_creation_key_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_treatment_actions_title_nonblank",
        ),
    )
    op.create_index(
        "ix_treatment_actions_treatment_plan_id",
        "treatment_actions",
        ["treatment_plan_id"],
    )
    op.create_index("ix_treatment_actions_car_id", "treatment_actions", ["car_id"])
    op.create_index(
        "ix_treatment_actions_plan_status",
        "treatment_actions",
        ["treatment_plan_id", "status"],
    )
    op.create_index(
        "ix_treatment_actions_car_created",
        "treatment_actions",
        ["car_id", "created_at", "id"],
    )

    op.create_table(
        "treatment_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("treatment_plan_id", sa.Integer(), nullable=False),
        sa.Column("treatment_action_id", sa.Integer(), nullable=True),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("recorded_by_user_id", sa.Integer(), nullable=False),
        sa.Column("recording_key", sa.String(length=128), nullable=False),
        sa.Column("progression_direction", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("advisor_note", sa.Text(), nullable=True),
        sa.Column(
            "visibility",
            sa.String(length=20),
            nullable=False,
            server_default="client",
        ),
        sa.Column("provenance_kind", sa.String(length=40), nullable=False),
        sa.Column("provenance_data", sa.JSON(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["treatment_plan_id"],
            ["treatment_plans.id"],
            name="fk_treatment_outcomes_treatment_plan_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["treatment_action_id"],
            ["treatment_actions.id"],
            name="fk_treatment_outcomes_treatment_action_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["car_id"],
            ["cars.id"],
            name="fk_treatment_outcomes_car_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["users.id"],
            name="fk_treatment_outcomes_recorded_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "treatment_plan_id",
            "recording_key",
            name="uq_treatment_outcomes_plan_recording_key",
        ),
        sa.CheckConstraint(
            "progression_direction IN ('improving', 'stable', 'deteriorating', 'resolved', 'insufficient_evidence')",
            name="ck_treatment_outcomes_progression",
        ),
        sa.CheckConstraint(
            "visibility IN ('client', 'advisor')",
            name="ck_treatment_outcomes_visibility",
        ),
        sa.CheckConstraint(
            "provenance_kind IN ('reviewed_evidence', 'professional_observation', 'insufficient_evidence')",
            name="ck_treatment_outcomes_provenance",
        ),
        sa.CheckConstraint(
            "(progression_direction = 'insufficient_evidence') OR (provenance_kind <> 'insufficient_evidence')",
            name="ck_treatment_outcomes_progression_provenance",
        ),
        sa.CheckConstraint(
            "(provenance_kind <> 'professional_observation') OR (provenance_data IS NOT NULL)",
            name="ck_treatment_outcomes_observation_provenance",
        ),
        sa.CheckConstraint(
            "length(trim(recording_key)) > 0",
            name="ck_treatment_outcomes_recording_key_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(summary)) > 0",
            name="ck_treatment_outcomes_summary_nonblank",
        ),
    )
    op.create_index(
        "ix_treatment_outcomes_treatment_plan_id",
        "treatment_outcomes",
        ["treatment_plan_id"],
    )
    op.create_index(
        "ix_treatment_outcomes_treatment_action_id",
        "treatment_outcomes",
        ["treatment_action_id"],
    )
    op.create_index("ix_treatment_outcomes_car_id", "treatment_outcomes", ["car_id"])
    op.create_index(
        "ix_treatment_outcomes_plan_time",
        "treatment_outcomes",
        ["treatment_plan_id", "observed_at", "id"],
    )
    op.create_index(
        "ix_treatment_outcomes_car_time",
        "treatment_outcomes",
        ["car_id", "observed_at", "id"],
    )


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        _preflight_event_upgrade()

    _create_domain_tables()

    if bind.dialect.name == "sqlite":
        return

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
        _base_pair_condition(include_wave_2_3c=True),
    )
    op.create_check_constraint(
        "ck_vehicle_events_treatment_plan_contract",
        "vehicle_events",
        _treatment_plan_contract_condition(include_outcome=True),
    )
    op.create_check_constraint(
        "ck_vehicle_events_treatment_action_contract",
        "vehicle_events",
        _treatment_action_contract_condition(),
    )


def downgrade():
    bind = op.get_bind()

    action_count = bind.execute(sa.text("SELECT COUNT(*) FROM treatment_actions")).scalar_one()
    outcome_count = bind.execute(sa.text("SELECT COUNT(*) FROM treatment_outcomes")).scalar_one()
    if action_count or outcome_count:
        raise RuntimeError(
            "Cannot downgrade Wave 2.3C while Treatment Action/Outcome history exists"
        )

    if bind.dialect.name != "sqlite":
        new_event_count = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM vehicle_events "
                "WHERE subject_type = 'treatment_action' "
                "OR event_type = 'treatment.outcome_recorded'"
            )
        ).scalar_one()
        if new_event_count:
            raise RuntimeError(
                "Cannot downgrade Wave 2.3C while canonical action/outcome history exists"
            )

        op.drop_constraint(
            "ck_vehicle_events_treatment_action_contract",
            "vehicle_events",
            type_="check",
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
            _base_pair_condition(include_wave_2_3c=False),
        )
        op.create_check_constraint(
            "ck_vehicle_events_treatment_plan_contract",
            "vehicle_events",
            _treatment_plan_contract_condition(include_outcome=False),
        )

    op.drop_table("treatment_outcomes")
    op.drop_table("treatment_actions")
