"""add live repair progress entries

Revision ID: f2b7c4d9a861
Revises: e1f6c9d4a730
Create Date: 2026-10-07 19:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "f2b7c4d9a861"
down_revision = "e1f6c9d4a730"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "repair_progress_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("treatment_plan_id", sa.Integer(), nullable=True),
        sa.Column("treatment_action_id", sa.Integer(), nullable=True),
        sa.Column("recorded_by_user_id", sa.Integer(), nullable=False),
        sa.Column("entry_key", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("location_text", sa.String(length=255), nullable=True),
        sa.Column("progress_percent", sa.Integer(), nullable=True),
        sa.Column(
            "visibility",
            sa.String(length=20),
            nullable=False,
            server_default="advisor",
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("structured_data", sa.JSON(), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["car_id"],
            ["cars.id"],
            name="fk_repair_progress_car_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["treatment_plan_id"],
            ["treatment_plans.id"],
            name="fk_repair_progress_plan_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["treatment_action_id"],
            ["treatment_actions.id"],
            name="fk_repair_progress_action_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["users.id"],
            name="fk_repair_progress_recorded_by",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("entry_key", name="uq_repair_progress_entry_key"),
        sa.CheckConstraint(
            "category IN ('custody','inspection','work_progress','parts','location','quality_check','handover','client_decision','general')",
            name="ck_repair_progress_category",
        ),
        sa.CheckConstraint(
            "stage IN ('received','inspection','dismantling','awaiting_parts','bodywork','paint','reassembly','testing','ready_for_delivery','delivered','paused','general')",
            name="ck_repair_progress_stage",
        ),
        sa.CheckConstraint(
            "visibility IN ('client','advisor')",
            name="ck_repair_progress_visibility",
        ),
        sa.CheckConstraint(
            "source IN ('manual','rina','whatsapp')",
            name="ck_repair_progress_source",
        ),
        sa.CheckConstraint(
            "progress_percent IS NULL OR (progress_percent >= 0 AND progress_percent <= 100)",
            name="ck_repair_progress_percent",
        ),
        sa.CheckConstraint(
            "length(trim(entry_key)) > 0",
            name="ck_repair_progress_entry_key_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_repair_progress_title_nonblank",
        ),
    )
    op.create_index(
        "ix_repair_progress_entries_car_id",
        "repair_progress_entries",
        ["car_id"],
    )
    op.create_index(
        "ix_repair_progress_entries_treatment_plan_id",
        "repair_progress_entries",
        ["treatment_plan_id"],
    )
    op.create_index(
        "ix_repair_progress_entries_treatment_action_id",
        "repair_progress_entries",
        ["treatment_action_id"],
    )
    op.create_index(
        "ix_repair_progress_car_time",
        "repair_progress_entries",
        ["car_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_repair_progress_plan_time",
        "repair_progress_entries",
        ["treatment_plan_id", "occurred_at", "id"],
    )


def downgrade():
    op.drop_index("ix_repair_progress_plan_time", table_name="repair_progress_entries")
    op.drop_index("ix_repair_progress_car_time", table_name="repair_progress_entries")
    op.drop_index("ix_repair_progress_entries_treatment_action_id", table_name="repair_progress_entries")
    op.drop_index("ix_repair_progress_entries_treatment_plan_id", table_name="repair_progress_entries")
    op.drop_index("ix_repair_progress_entries_car_id", table_name="repair_progress_entries")
    op.drop_table("repair_progress_entries")
