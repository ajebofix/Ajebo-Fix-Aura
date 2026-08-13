"""Vehicle-scoped, visibility-aware memory service for A.J. Rina.

Memory retrieval happens only after authority resolution.  Raw chat continuity,
durable conversation summaries and advisor-only notes remain separate layers;
this service does not merge them into a generic provider memory blob.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Final

# Ensure the existing canonical model classes expose the additive Wave 1.3
# columns even in local db.create_all() test databases.
import rina.memory_model_extensions  # noqa: F401

from extensions import db
from models import AdvisorNote, ChatMessage, ConversationRecord
from services.rina_authority import (
    ACTION_READ_ADVISOR_MEMORY,
    ACTION_READ_CHAT_HISTORY,
    ACTION_READ_CLIENT_SUMMARY,
    AUTHORITY_ADMINISTRATOR,
    AUTHORITY_ADVISOR,
    AUTHORITY_DRIVER,
    AUTHORITY_OWNER,
    RinaAuthorityContext,
    require_rina_action,
    resolve_rina_authority,
)


VISIBILITY_CLIENT: Final = "client"
VISIBILITY_ADVISOR: Final = "advisor"
VISIBILITY_INTERNAL: Final = "internal"
VISIBILITIES: Final = frozenset(
    {VISIBILITY_CLIENT, VISIBILITY_ADVISOR, VISIBILITY_INTERNAL}
)

CHANNEL_IN_APP: Final = "in_app"
CHANNEL_WHATSAPP: Final = "whatsapp"
CHANNEL_EMAIL: Final = "email"
CHANNEL_SYSTEM: Final = "system"
CHANNELS: Final = frozenset(
    {CHANNEL_IN_APP, CHANNEL_WHATSAPP, CHANNEL_EMAIL, CHANNEL_SYSTEM}
)

CHAT_ROLES: Final = frozenset({"user", "assistant"})


class RinaMemoryError(ValueError):
    """Base error for safe Rina memory operations."""


class RinaMemoryPolicyError(RinaMemoryError):
    """The requested memory operation violates the authority policy."""


@dataclass(frozen=True)
class RinaChatTurn:
    message_id: int
    role: str
    content: str
    timestamp: datetime | None
    conversation_id: str
    channel: str
    visibility: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat() if self.timestamp else None
        return payload


@dataclass(frozen=True)
class RinaSummaryMemory:
    record_id: int
    visibility: str
    provenance: str | None
    verification_state: str | None
    created_at: datetime | None
    concern: str | None
    summary: str | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat() if self.created_at else None
        return payload


@dataclass(frozen=True)
class RinaAdvisorMemory:
    note_id: int
    advisor_id: int
    created_at: datetime | None
    content: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat() if self.created_at else None
        return payload


@dataclass(frozen=True)
class RinaMemoryBundle:
    user_id: int
    car_id: int
    authority: str
    chat_history: tuple[RinaChatTurn, ...]
    summaries: tuple[RinaSummaryMemory, ...]
    advisor_memory: tuple[RinaAdvisorMemory, ...]

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "car_id": self.car_id,
            "authority": self.authority,
            "chat_history": [item.to_dict() for item in self.chat_history],
            "summaries": [item.to_dict() for item in self.summaries],
            "advisor_memory": [item.to_dict() for item in self.advisor_memory],
        }


def _visibility_scope(authority: str) -> tuple[str, ...]:
    if authority in {AUTHORITY_ADVISOR, AUTHORITY_ADMINISTRATOR}:
        return (VISIBILITY_CLIENT, VISIBILITY_ADVISOR, VISIBILITY_INTERNAL)
    return (VISIBILITY_CLIENT,)


def _default_chat_visibility(authority: str) -> str:
    if authority in {AUTHORITY_ADVISOR, AUTHORITY_ADMINISTRATOR}:
        return VISIBILITY_ADVISOR
    return VISIBILITY_CLIENT


def _validate_visibility(value: str) -> str:
    if value not in VISIBILITIES:
        raise RinaMemoryPolicyError("unsupported memory visibility")
    return value


def _validate_channel(value: str) -> str:
    if value not in CHANNELS:
        raise RinaMemoryPolicyError("unsupported Rina channel")
    return value


def _validate_conversation_id(value: str) -> str:
    clean = (value or "").strip()
    if not clean or len(clean) > 64:
        raise RinaMemoryPolicyError("valid conversation_id is required")
    return clean


def save_rina_chat_turn(
    *,
    user_id: int,
    car_id: int,
    conversation_id: str,
    role: str,
    content: str,
    channel: str = CHANNEL_IN_APP,
    visibility: str | None = None,
    commit: bool = False,
) -> ChatMessage:
    """Persist one raw chat turn inside the caller's vehicle authority boundary.

    The caller owns transaction control by default.  This lets a later
    orchestration cutover persist user turn, material summary/audit and response
    atomically where the workflow requires it.
    """

    authority = resolve_rina_authority(user_id=user_id, car_id=car_id)
    require_rina_action(
        authority_context=authority,
        action=ACTION_READ_CHAT_HISTORY,
    )

    if role not in CHAT_ROLES:
        raise RinaMemoryPolicyError("unsupported chat role")

    clean_content = (content or "").strip()
    if not clean_content:
        raise RinaMemoryPolicyError("chat content cannot be empty")

    resolved_visibility = _validate_visibility(
        visibility or _default_chat_visibility(authority.authority)
    )
    if (
        authority.authority in {AUTHORITY_OWNER, AUTHORITY_DRIVER}
        and resolved_visibility != VISIBILITY_CLIENT
    ):
        raise RinaMemoryPolicyError(
            "owner/driver chat turns cannot be persisted as hidden advisor memory"
        )

    row = ChatMessage(
        user_id=user_id,
        car_id=car_id,
        conversation_id=_validate_conversation_id(conversation_id),
        role=role,
        message=clean_content,
        channel=_validate_channel(channel),
        visibility=resolved_visibility,
        timestamp=datetime.utcnow(),
    )
    db.session.add(row)
    db.session.flush()

    if commit:
        db.session.commit()

    return row


def load_rina_chat_history(
    *,
    user_id: int,
    car_id: int,
    conversation_id: str | None = None,
    limit: int = 12,
) -> tuple[RinaChatTurn, ...]:
    authority = resolve_rina_authority(user_id=user_id, car_id=car_id)
    require_rina_action(
        authority_context=authority,
        action=ACTION_READ_CHAT_HISTORY,
    )

    bounded_limit = max(1, min(int(limit), 50))
    query = ChatMessage.query.filter(
        ChatMessage.user_id == user_id,
        ChatMessage.car_id == car_id,
        ChatMessage.visibility.in_(_visibility_scope(authority.authority)),
    )

    if conversation_id is not None:
        query = query.filter(
            ChatMessage.conversation_id == _validate_conversation_id(conversation_id)
        )

    rows = (
        query.order_by(ChatMessage.timestamp.desc(), ChatMessage.id.desc())
        .limit(bounded_limit)
        .all()
    )

    # Return chronological conversation order even though the bounded SQL query
    # selects the newest turns first.
    return tuple(
        RinaChatTurn(
            message_id=row.id,
            role=row.role,
            content=row.message,
            timestamp=row.timestamp,
            conversation_id=row.conversation_id,
            channel=row.channel or CHANNEL_IN_APP,
            visibility=row.visibility or VISIBILITY_INTERNAL,
        )
        for row in reversed(rows)
    )


def _summary_query(
    *,
    authority: RinaAuthorityContext,
    limit: int,
):
    require_rina_action(
        authority_context=authority,
        action=ACTION_READ_CLIENT_SUMMARY,
    )

    query = ConversationRecord.query.filter(
        ConversationRecord.vehicle_id == authority.car_id,
        ConversationRecord.visibility.in_(_visibility_scope(authority.authority)),
    )

    # Drivers must not automatically inherit owner/private client history.
    if authority.authority == AUTHORITY_DRIVER:
        query = query.filter(ConversationRecord.user_id == authority.user_id)

    return (
        query.order_by(ConversationRecord.created_at.desc(), ConversationRecord.id.desc())
        .limit(max(1, min(int(limit), 25)))
        .all()
    )


def load_rina_summaries(
    *,
    user_id: int,
    car_id: int,
    limit: int = 8,
) -> tuple[RinaSummaryMemory, ...]:
    authority = resolve_rina_authority(user_id=user_id, car_id=car_id)
    rows = _summary_query(authority=authority, limit=limit)

    return tuple(
        RinaSummaryMemory(
            record_id=row.id,
            visibility=row.visibility or VISIBILITY_INTERNAL,
            provenance=row.provenance,
            verification_state=row.verification_state,
            created_at=row.created_at,
            concern=row.concern,
            summary=row.advisor_summary,
        )
        for row in rows
    )


def load_rina_advisor_memory(
    *,
    user_id: int,
    car_id: int,
    limit: int = 6,
) -> tuple[RinaAdvisorMemory, ...]:
    authority = resolve_rina_authority(user_id=user_id, car_id=car_id)
    require_rina_action(
        authority_context=authority,
        action=ACTION_READ_ADVISOR_MEMORY,
    )

    rows = (
        AdvisorNote.query.filter_by(car_id=car_id)
        .order_by(AdvisorNote.created_at.desc(), AdvisorNote.id.desc())
        .limit(max(1, min(int(limit), 20)))
        .all()
    )

    return tuple(
        RinaAdvisorMemory(
            note_id=row.id,
            advisor_id=row.advisor_id,
            created_at=row.created_at,
            content=row.note,
        )
        for row in rows
    )


def load_rina_memory_bundle(
    *,
    user_id: int,
    car_id: int,
    conversation_id: str | None = None,
    chat_limit: int = 12,
    summary_limit: int = 8,
    advisor_limit: int = 6,
) -> RinaMemoryBundle:
    authority = resolve_rina_authority(user_id=user_id, car_id=car_id)

    chat_history = load_rina_chat_history(
        user_id=user_id,
        car_id=car_id,
        conversation_id=conversation_id,
        limit=chat_limit,
    )
    summaries = load_rina_summaries(
        user_id=user_id,
        car_id=car_id,
        limit=summary_limit,
    )

    advisor_memory: tuple[RinaAdvisorMemory, ...] = ()
    if authority.authority in {AUTHORITY_ADVISOR, AUTHORITY_ADMINISTRATOR}:
        advisor_memory = load_rina_advisor_memory(
            user_id=user_id,
            car_id=car_id,
            limit=advisor_limit,
        )

    return RinaMemoryBundle(
        user_id=user_id,
        car_id=car_id,
        authority=authority.authority,
        chat_history=chat_history,
        summaries=summaries,
        advisor_memory=advisor_memory,
    )
