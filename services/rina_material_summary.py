"""Persist only material, rules-derived Rina conversation summaries.

Raw conversational continuity belongs in ChatMessage. This module creates a
small durable ConversationRecord only when the chat produced a material workflow
signal such as a consultation request or advisor-review escalation. It does not
infer emotion, diagnosis, urgency or free-text meaning beyond explicit route
intent/state rules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

import rina.memory_model_extensions  # noqa: F401

from extensions import db
from models import ConversationRecord
from services.rina_authority import (
    AUTHORITY_ADMINISTRATOR,
    AUTHORITY_ADVISOR,
    resolve_rina_authority,
)


MATERIAL_BOOKING_REQUEST: Final = "booking_request"
MATERIAL_ADVISOR_REVIEW: Final = "advisor_review_required"

_MATERIAL_TYPES: Final = frozenset(
    {MATERIAL_BOOKING_REQUEST, MATERIAL_ADVISOR_REVIEW}
)

_CLIENT_SUMMARIES: Final = {
    MATERIAL_BOOKING_REQUEST: (
        "A consultation booking request was raised through A.J. Rina."
    ),
    MATERIAL_ADVISOR_REVIEW: (
        "A question requiring advisor review was recorded through A.J. Rina."
    ),
}

_ADVISOR_SUMMARIES: Final = {
    MATERIAL_BOOKING_REQUEST: (
        "Rina recorded a rules-detected consultation booking request."
    ),
    MATERIAL_ADVISOR_REVIEW: (
        "Rina escalated the conversation for advisor review without making a "
        "driving-safety or diagnostic claim."
    ),
}

_RECOMMENDED_ACTIONS: Final = {
    MATERIAL_BOOKING_REQUEST: "request_consultation",
    MATERIAL_ADVISOR_REVIEW: "advisor_review",
}


def record_rina_material_summary(
    *,
    user_id: int,
    car_id: int,
    conversation_id: str,
    material_type: str,
    commit: bool = False,
) -> ConversationRecord:
    """Create a privacy-minimized durable record for one material chat outcome."""

    if material_type not in _MATERIAL_TYPES:
        raise ValueError("unsupported Rina material summary type")

    clean_conversation_id = (conversation_id or "").strip()
    if not clean_conversation_id or len(clean_conversation_id) > 64:
        raise ValueError("valid conversation_id is required")

    authority = resolve_rina_authority(user_id=user_id, car_id=car_id)
    privileged = authority.authority in {
        AUTHORITY_ADVISOR,
        AUTHORITY_ADMINISTRATOR,
    }

    row = ConversationRecord(
        user_id=user_id,
        vehicle_id=car_id,
        conversation_id=clean_conversation_id,
        concern=None,
        advisor_summary=_ADVISOR_SUMMARIES[material_type],
        client_summary=(
            None if privileged else _CLIENT_SUMMARIES[material_type]
        ),
        emotional_state=None,
        urgency_level=None,
        recommended_action=_RECOMMENDED_ACTIONS[material_type],
        escalation_level=(
            "advisor_review"
            if material_type == MATERIAL_ADVISOR_REVIEW
            else None
        ),
        consultation_related=material_type == MATERIAL_BOOKING_REQUEST,
        visibility="advisor" if privileged else "client",
        source="rina.chat",
        provenance="rules",
        verification_state="not_applicable",
        status="logged",
        created_at=datetime.utcnow(),
    )
    db.session.add(row)
    db.session.flush()

    if commit:
        db.session.commit()

    return row
