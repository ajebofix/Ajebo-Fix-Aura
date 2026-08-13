"""Add vehicle scope and visibility metadata to Rina memory records.

Revision ID: a31c7d2e9f10
Revises: f24c8d1e6a90
Create Date: 2026-08-13

Wave 1.3 keeps ChatMessage as raw conversation continuity and
ConversationRecord as durable operational/clinical summary.  This migration
adds the fields required to retrieve both safely by vehicle and visibility.

Historical ChatMessage rows cannot be assigned to a vehicle without guessing,
so car_id/conversation_id remain NULL for legacy rows.  Legacy visibility is
backfilled conservatively to internal so old unscoped content can never become
client memory merely because a new retrieval service exists.
"""

from alembic import op
import sqlalchemy as sa


revision = "a31c7d2e9f10"
down_revision = "f24c8d1e6a90"
branch_labels = None
depends_on = None


VISIBILITIES = ("client", "advisor", "internal")
CHAT_CHANNELS = ("in_app", "whatsapp", "email", "system", "legacy")
PROVENANCE = ("rules", "provider", "advisor", "legacy")
VERIFICATION_STATES = (
    "unverified",
    "advisor_verified",
    "disputed",
    "not_applicable",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _create_postgres_checks() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return

    op.create_check_constraint(
        "ck_chat_messages_visibility",
        "chat_messages",
        f"visibility IS NULL OR visibility IN ({_quoted(VISIBILITIES)})",
    )
    op.create_check_constraint(
        "ck_chat_messages_channel",
        "chat_messages",
        f"channel IS NULL OR channel IN ({_quoted(CHAT_CHANNELS)})",
    )
    op.create_check_constraint(
        "ck_conversation_records_visibility",
        "conversation_records",
        f"visibility IS NULL OR visibility IN ({_quoted(VISIBILITIES)})",
    )
    op.create_check_constraint(
        "ck_conversation_records_provenance",
        "conversation_records",
        f"provenance IS NULL OR provenance IN ({_quoted(PROVENANCE)})",
    )
    op.create_check_constraint(
        "ck_conversation_records_verification_state",
        "conversation_records",
        (
            "verification_state IS NULL OR verification_state IN "
            f"({_quoted(VERIFICATION_STATES)})"
        ),
    )


def upgrade():
    with op.batch_alter_table("chat_messages") as batch_op:
        batch_op.add_column(sa.Column("car_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("conversation_id", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("channel", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("visibility", sa.String(length=20), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_chat_messages_car_id",
            "cars",
            ["car_id"],
            ["id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table("conversation_records") as batch_op:
        batch_op.add_column(
            sa.Column("conversation_id", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("visibility", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(sa.Column("source", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("provenance", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column("verification_state", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(sa.Column("client_summary", sa.Text(), nullable=True))

    # Do not invent vehicle scope.  Mark legacy records conservatively so they
    # cannot be retrieved as client-visible memory until explicitly reviewed.
    op.execute(
        sa.text(
            "UPDATE chat_messages SET visibility = 'internal', channel = 'legacy' "
            "WHERE visibility IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE conversation_records SET visibility = 'internal', "
            "provenance = 'legacy', verification_state = 'unverified' "
            "WHERE visibility IS NULL"
        )
    )

    _create_postgres_checks()

    op.create_index(
        "ix_chat_messages_user_vehicle_time",
        "chat_messages",
        ["user_id", "car_id", "timestamp", "id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_messages_conversation_id",
        "chat_messages",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_records_user_vehicle_visibility_time",
        "conversation_records",
        ["user_id", "vehicle_id", "visibility", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_records_conversation_id",
        "conversation_records",
        ["conversation_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_conversation_records_conversation_id",
        table_name="conversation_records",
    )
    op.drop_index(
        "ix_conversation_records_user_vehicle_visibility_time",
        table_name="conversation_records",
    )
    op.drop_index(
        "ix_chat_messages_conversation_id",
        table_name="chat_messages",
    )
    op.drop_index(
        "ix_chat_messages_user_vehicle_time",
        table_name="chat_messages",
    )

    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "ck_conversation_records_verification_state",
            "conversation_records",
            type_="check",
        )
        op.drop_constraint(
            "ck_conversation_records_provenance",
            "conversation_records",
            type_="check",
        )
        op.drop_constraint(
            "ck_conversation_records_visibility",
            "conversation_records",
            type_="check",
        )
        op.drop_constraint(
            "ck_chat_messages_channel",
            "chat_messages",
            type_="check",
        )
        op.drop_constraint(
            "ck_chat_messages_visibility",
            "chat_messages",
            type_="check",
        )

    with op.batch_alter_table("conversation_records") as batch_op:
        batch_op.drop_column("client_summary")
        batch_op.drop_column("verification_state")
        batch_op.drop_column("provenance")
        batch_op.drop_column("source")
        batch_op.drop_column("visibility")
        batch_op.drop_column("conversation_id")

    with op.batch_alter_table("chat_messages") as batch_op:
        batch_op.drop_constraint("fk_chat_messages_car_id", type_="foreignkey")
        batch_op.drop_column("visibility")
        batch_op.drop_column("channel")
        batch_op.drop_column("conversation_id")
        batch_op.drop_column("car_id")
