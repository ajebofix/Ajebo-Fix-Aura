"""add historical service episodes and case attribution extraction

Revision ID: c9e4a7b2d510
Revises: b8f3d2a6c410
Create Date: 2026-09-21 14:05:00
"""

from alembic import op
import sqlalchemy as sa


revision = "c9e4a7b2d510"
down_revision = "b8f3d2a6c410"
branch_labels = None
depends_on = None


OLD_EXTRACTION_TYPES = (
    "image_observation",
    "document_text",
    "document_understanding",
    "archive_manifest",
    "transcription",
    "structured_fields",
)
NEW_EXTRACTION_TYPES = (
    *OLD_EXTRACTION_TYPES,
    "historical_case_attribution",
)
EPISODE_STATUSES = ("active", "archived")


def _quoted(values):
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    op.create_table(
        "historical_service_episodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("anchor_evidence_id", sa.Integer(), nullable=False),
        sa.Column("anchor_extraction_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("job_reference", sa.String(length=120), nullable=True),
        sa.Column("episode_date", sa.DateTime(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["car_id"],
            ["cars.id"],
            name="fk_historical_service_episodes_car_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["anchor_evidence_id"],
            ["vehicle_evidence.id"],
            name="fk_historical_service_episodes_anchor_evidence_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["anchor_extraction_id"],
            ["evidence_extractions.id"],
            name="fk_historical_service_episodes_anchor_extraction_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_historical_service_episodes_created_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "anchor_evidence_id",
            name="uq_historical_service_episode_anchor_evidence",
        ),
        sa.UniqueConstraint(
            "anchor_extraction_id",
            name="uq_historical_service_episode_anchor_extraction",
        ),
    )
    op.create_index(
        "ix_historical_service_episodes_car_id",
        "historical_service_episodes",
        ["car_id"],
        unique=False,
    )
    op.create_index(
        "ix_historical_service_episodes_anchor_evidence_id",
        "historical_service_episodes",
        ["anchor_evidence_id"],
        unique=True,
    )
    op.create_index(
        "ix_historical_service_episodes_anchor_extraction_id",
        "historical_service_episodes",
        ["anchor_extraction_id"],
        unique=True,
    )
    op.create_index(
        "ix_historical_service_episodes_job_reference",
        "historical_service_episodes",
        ["job_reference"],
        unique=False,
    )
    op.create_index(
        "ix_historical_service_episodes_episode_date",
        "historical_service_episodes",
        ["episode_date"],
        unique=False,
    )
    op.create_index(
        "ix_historical_service_episodes_status",
        "historical_service_episodes",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_historical_service_episodes_car_date",
        "historical_service_episodes",
        ["car_id", "episode_date", "id"],
        unique=False,
    )

    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_historical_service_episodes_status",
            "historical_service_episodes",
            f"status IN ({_quoted(EPISODE_STATUSES)})",
        )
        op.drop_constraint(
            "ck_evidence_extractions_type",
            "evidence_extractions",
            type_="check",
        )
        op.create_check_constraint(
            "ck_evidence_extractions_type",
            "evidence_extractions",
            f"extraction_type IN ({_quoted(NEW_EXTRACTION_TYPES)})",
        )


def downgrade():
    bind = op.get_bind()

    attribution_count = bind.execute(
        sa.text(
            "SELECT count(*) FROM evidence_extractions "
            "WHERE extraction_type = 'historical_case_attribution'"
        )
    ).scalar_one()
    episode_count = bind.execute(
        sa.text("SELECT count(*) FROM historical_service_episodes")
    ).scalar_one()
    if attribution_count or episode_count:
        raise RuntimeError(
            "Refusing to downgrade while historical case attribution data exists."
        )

    if bind.dialect.name != "sqlite":
        op.drop_constraint(
            "ck_evidence_extractions_type",
            "evidence_extractions",
            type_="check",
        )
        op.create_check_constraint(
            "ck_evidence_extractions_type",
            "evidence_extractions",
            f"extraction_type IN ({_quoted(OLD_EXTRACTION_TYPES)})",
        )
        op.drop_constraint(
            "ck_historical_service_episodes_status",
            "historical_service_episodes",
            type_="check",
        )

    op.drop_index(
        "ix_historical_service_episodes_car_date",
        table_name="historical_service_episodes",
    )
    op.drop_index(
        "ix_historical_service_episodes_status",
        table_name="historical_service_episodes",
    )
    op.drop_index(
        "ix_historical_service_episodes_episode_date",
        table_name="historical_service_episodes",
    )
    op.drop_index(
        "ix_historical_service_episodes_job_reference",
        table_name="historical_service_episodes",
    )
    op.drop_index(
        "ix_historical_service_episodes_anchor_extraction_id",
        table_name="historical_service_episodes",
    )
    op.drop_index(
        "ix_historical_service_episodes_anchor_evidence_id",
        table_name="historical_service_episodes",
    )
    op.drop_index(
        "ix_historical_service_episodes_car_id",
        table_name="historical_service_episodes",
    )
    op.drop_table("historical_service_episodes")
