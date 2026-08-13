"""Add privacy-safe Rina AI audit events.

Revision ID: b41d8e3f0a21
Revises: a31c7d2e9f10
Create Date: 2026-08-13

The audit record stores orchestration metadata and evidence identifiers only.
It deliberately has no prompt, user-message, response-body, chain-of-thought or
credential columns.
"""

from alembic import op
import sqlalchemy as sa


revision = "b41d8e3f0a21"
down_revision = "a31c7d2e9f10"
branch_labels = None
depends_on = None


AUTHORITIES = ("owner", "driver", "advisor", "administrator")
STATES = (
    "answered",
    "abstained",
    "vehicle_required",
    "authority_denied",
    "escalation_required",
    "provider_unavailable",
)
OUTCOMES = (
    "answered",
    "abstained",
    "vehicle_required",
    "authority_denied",
    "escalation_required",
    "provider_failed",
    "feature_disabled",
)
PROVIDER_STATUSES = ("not_called", "ok", "unavailable", "rejected", "disabled")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    op.create_table(
        "rina_ai_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("car_id", sa.Integer(), nullable=True),
        sa.Column("authority", sa.String(length=32), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column(
            "action_family",
            sa.String(length=64),
            nullable=False,
            server_default="respond",
        ),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("provider_model", sa.String(length=96), nullable=True),
        sa.Column("provider_status", sa.String(length=32), nullable=False),
        sa.Column("provider_request_id", sa.String(length=128), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=True),
        sa.Column("audit_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_rina_ai_audit_events_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["car_id"],
            ["cars.id"],
            name="fk_rina_ai_audit_events_car_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "request_id",
            name="uq_rina_ai_audit_events_request_id",
        ),
    )

    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_rina_ai_audit_events_authority",
            "rina_ai_audit_events",
            f"authority IS NULL OR authority IN ({_quoted(AUTHORITIES)})",
        )
        op.create_check_constraint(
            "ck_rina_ai_audit_events_state",
            "rina_ai_audit_events",
            f"state IN ({_quoted(STATES)})",
        )
        op.create_check_constraint(
            "ck_rina_ai_audit_events_outcome",
            "rina_ai_audit_events",
            f"outcome IN ({_quoted(OUTCOMES)})",
        )
        op.create_check_constraint(
            "ck_rina_ai_audit_events_provider_status",
            "rina_ai_audit_events",
            f"provider_status IN ({_quoted(PROVIDER_STATUSES)})",
        )

    op.create_index(
        "ix_rina_ai_audit_events_user_car_time",
        "rina_ai_audit_events",
        ["user_id", "car_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_rina_ai_audit_events_outcome_time",
        "rina_ai_audit_events",
        ["outcome", "created_at", "id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_rina_ai_audit_events_outcome_time",
        table_name="rina_ai_audit_events",
    )
    op.drop_index(
        "ix_rina_ai_audit_events_user_car_time",
        table_name="rina_ai_audit_events",
    )

    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "ck_rina_ai_audit_events_provider_status",
            "rina_ai_audit_events",
            type_="check",
        )
        op.drop_constraint(
            "ck_rina_ai_audit_events_outcome",
            "rina_ai_audit_events",
            type_="check",
        )
        op.drop_constraint(
            "ck_rina_ai_audit_events_state",
            "rina_ai_audit_events",
            type_="check",
        )
        op.drop_constraint(
            "ck_rina_ai_audit_events_authority",
            "rina_ai_audit_events",
            type_="check",
        )

    op.drop_table("rina_ai_audit_events")
