"""Wave 1.3 extensions for Aura's existing memory models.

`models.py` is currently a large legacy module.  The architecture audit already
identified model decomposition as desirable, so Wave 1.3 adds the new columns
through SQLAlchemy's supported declarative post-mapping column attachment
instead of duplicating ChatMessage/ConversationRecord into parallel tables.

The Alembic migration remains the production schema source of truth.  These
attributes keep ORM metadata and local `db.create_all()` test databases aligned
with that schema while preserving the existing canonical model classes.
"""

from __future__ import annotations

from extensions import db
from models import ChatMessage, ConversationRecord


def _attach_column(model, name: str, column) -> None:
    if hasattr(model, name):
        return
    setattr(model, name, column)


def apply_rina_memory_model_extensions() -> None:
    _attach_column(
        ChatMessage,
        "car_id",
        db.Column(
            db.Integer,
            db.ForeignKey(
                "cars.id",
                name="fk_chat_messages_car_id",
                ondelete="CASCADE",
            ),
            nullable=True,
        ),
    )
    _attach_column(
        ChatMessage,
        "conversation_id",
        db.Column(db.String(64), nullable=True),
    )
    _attach_column(
        ChatMessage,
        "channel",
        db.Column(db.String(32), nullable=True),
    )
    _attach_column(
        ChatMessage,
        "visibility",
        db.Column(db.String(20), nullable=True),
    )

    _attach_column(
        ConversationRecord,
        "conversation_id",
        db.Column(db.String(64), nullable=True),
    )
    _attach_column(
        ConversationRecord,
        "visibility",
        db.Column(db.String(20), nullable=True),
    )
    _attach_column(
        ConversationRecord,
        "source",
        db.Column(db.String(64), nullable=True),
    )
    _attach_column(
        ConversationRecord,
        "provenance",
        db.Column(db.String(32), nullable=True),
    )
    _attach_column(
        ConversationRecord,
        "verification_state",
        db.Column(db.String(32), nullable=True),
    )
    _attach_column(
        ConversationRecord,
        "client_summary",
        db.Column(db.Text, nullable=True),
    )


apply_rina_memory_model_extensions()
