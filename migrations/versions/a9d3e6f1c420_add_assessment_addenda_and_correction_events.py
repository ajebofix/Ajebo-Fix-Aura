"""Add immutable assessment addenda and canonical correction events.

Revision ID: a9d3e6f1c420
Revises: f4c8a2d9b731
Create Date: 2026-08-24

Wave 2.2B3 preserves finalized VehicleAssessment immutability. Corrections are
new attributed rows and `assessment.corrected` facts; the assessment remains
finalized and historical lifecycle events are never synthesized.
"""

from alembic import op
import sqlalchemy as sa


revision = "a9d3e6f1c420"
down_revision = "f4c8a2d9b731"
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
B2_ASSESSMENT_EVENT_TYPES = (
    "assessment.created",
    "assessment.finalized",
)
B3_ASSESSMENT_EVENT_TYPES = B2_ASSESSMENT_EVENT_TYPES + ("assessment.corrected",)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _base_pair_condition(assessment_events: tuple[str, ...]) -> str:
    return (
        "subject_type IS NULL "
        "OR (subject_type = 'reported_concern' AND event_type IN "
        f"({_quoted(CONCERN_EVENT_TYPES)})) "
        "OR (subject_type = 'vehicle_evidence' AND event_type IN "
        f"({_quoted(EVIDENCE_EVENT_TYPES)})) "
        "OR (subject_type = 'consultation' AND event_type IN "
        f"({_quoted(CONSULTATION_EVENT_TYPES)})) "
        "OR (subject_type = 'vehicle_assessment' AND event_type IN "
        f"({_quoted(assessment_events)}))"
    )


def _b2_assessment_contract_condition() -> str:
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


def _b3_assessment_contract_condition() -> str:
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
        "AND new_state = 'finalized') "
        "OR (event_type = 'assessment.corrected' "
        "AND previous_state IS NOT NULL "
        "AND previous_state = 'finalized' "
        "AND new_state IS NOT NULL "
        "AND new_state = 'finalized')"
        ")"
        ")"
    )


def _preflight_upgrade() -> None:
    bind = op.get_bind()
    invalid_pairs = bind.execute(
        sa.text(
            f"SELECT COUNT(*) FROM vehicle_events "
            f"WHERE NOT ({_base_pair_condition(B3_ASSESSMENT_EVENT_TYPES)})"
        )
    ).scalar_one()
    if invalid_pairs:
        raise RuntimeError(
            "Cannot extend assessment correction event contract: "
            f"found {invalid_pairs} incompatible canonical row(s)"
        )

    invalid_assessments = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM vehicle_events "
            "WHERE subject_type = 'vehicle_assessment' "
            f"AND NOT ({_b3_assessment_contract_condition()})"
        )
    ).scalar_one()
    if invalid_assessments:
        raise RuntimeError(
            "Cannot add assessment correction contract: "
            f"found {invalid_assessments} incompatible assessment row(s)"
        )


def upgrade():
    op.create_table(
        "vehicle_assessment_addenda",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("client_text", sa.Text(), nullable=True),
        sa.Column("internal_text", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "category IN ('correction', 'clarification', 'additional_information')",
            name="ck_vehicle_assessment_addenda_category",
        ),
        sa.CheckConstraint(
            "visibility IN ('client', 'advisor', 'internal')",
            name="ck_vehicle_assessment_addenda_visibility",
        ),
        sa.CheckConstraint(
            "(client_text IS NOT NULL AND length(trim(client_text)) > 0) "
            "OR (internal_text IS NOT NULL AND length(trim(internal_text)) > 0)",
            name="ck_vehicle_assessment_addenda_has_text",
        ),
        sa.CheckConstraint(
            "visibility <> 'client' "
            "OR (client_text IS NOT NULL AND length(trim(client_text)) > 0)",
            name="ck_vehicle_assessment_addenda_client_text",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["vehicle_assessments.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_vehicle_assessment_addenda_idempotency_key",
        ),
    )
    op.create_index(
        "ix_vehicle_assessment_addenda_assessment_created",
        "vehicle_assessment_addenda",
        ["assessment_id", "created_at"],
        unique=False,
    )

    if op.get_bind().dialect.name == "sqlite":
        return

    _preflight_upgrade()
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
        _base_pair_condition(B3_ASSESSMENT_EVENT_TYPES),
    )
    op.create_check_constraint(
        "ck_vehicle_events_assessment_contract",
        "vehicle_events",
        _b3_assessment_contract_condition(),
    )


def downgrade():
    bind = op.get_bind()

    if bind.dialect.name != "sqlite":
        corrected_count = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM vehicle_events "
                "WHERE subject_type = 'vehicle_assessment' "
                "AND event_type = 'assessment.corrected'"
            )
        ).scalar_one()
        if corrected_count:
            raise RuntimeError(
                "Cannot downgrade Wave 2.2B3 while assessment.corrected history exists"
            )

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
            _base_pair_condition(B2_ASSESSMENT_EVENT_TYPES),
        )
        op.create_check_constraint(
            "ck_vehicle_events_assessment_contract",
            "vehicle_events",
            _b2_assessment_contract_condition(),
        )

    op.drop_index(
        "ix_vehicle_assessment_addenda_assessment_created",
        table_name="vehicle_assessment_addenda",
    )
    op.drop_table("vehicle_assessment_addenda")
