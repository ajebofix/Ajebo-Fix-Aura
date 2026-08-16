"""Add canonical evidence event taxonomy and PostgreSQL contract checks.

Revision ID: d7e4a9c2f160
Revises: c62f1a4e8d30
Create Date: 2026-08-16

Legacy VehicleEvent rows with no canonical subject remain untouched. Canonical
rows with a subject are constrained to an approved subject/event pairing. The
first Wave 1.4 evidence events are review and same-vehicle care linkage only.
"""

from alembic import op
import sqlalchemy as sa


revision = "d7e4a9c2f160"
down_revision = "c62f1a4e8d30"
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


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _canonical_pair_condition() -> str:
    return (
        "subject_type IS NULL "
        "OR (subject_type = 'reported_concern' AND event_type IN "
        f"({_quoted(CONCERN_EVENT_TYPES)})) "
        "OR (subject_type = 'vehicle_evidence' AND event_type IN "
        f"({_quoted(EVIDENCE_EVENT_TYPES)}))"
    )


def _evidence_contract_condition() -> str:
    return (
        "subject_type IS NULL "
        "OR subject_type <> 'vehicle_evidence' "
        "OR ("
        "progression_direction = 'not_applicable' "
        "AND ("
        "(event_type = 'evidence.reviewed' "
        "AND previous_state = 'pending_review' "
        "AND new_state IN ('accepted', 'rejected')) "
        "OR (event_type = 'evidence.linked' "
        "AND previous_state IS NULL "
        "AND new_state IS NULL)"
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
            "Cannot add canonical subject/event constraint: "
            f"found {invalid_pairs} incompatible canonical row(s)"
        )

    invalid_evidence_contracts = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*)
            FROM vehicle_events
            WHERE NOT ({_evidence_contract_condition()})
            """
        )
    ).scalar_one()
    if invalid_evidence_contracts:
        raise RuntimeError(
            "Cannot add evidence event contract: "
            f"found {invalid_evidence_contracts} incompatible row(s)"
        )


def upgrade():
    _preflight()

    if op.get_bind().dialect.name == "sqlite":
        return

    op.create_check_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        _canonical_pair_condition(),
    )
    op.create_check_constraint(
        "ck_vehicle_events_evidence_contract",
        "vehicle_events",
        _evidence_contract_condition(),
    )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        return

    op.drop_constraint(
        "ck_vehicle_events_evidence_contract",
        "vehicle_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_vehicle_events_canonical_subject_event",
        "vehicle_events",
        type_="check",
    )
