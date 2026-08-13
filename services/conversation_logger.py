# services/conversation_logger.py

from datetime import datetime

import rina.memory_model_extensions  # noqa: F401

from models import ConversationRecord, db
from services.conversation_analysis import analyze_conversation


MEMORY_VISIBILITIES = {"client", "advisor", "internal"}
MEMORY_PROVENANCE = {"rules", "provider", "advisor", "legacy"}
MEMORY_VERIFICATION_STATES = {
    "unverified",
    "advisor_verified",
    "disputed",
    "not_applicable",
}


# =========================
# SIMPLE DETECTORS
# =========================


def detect_emotion(message: str) -> str:
    """Legacy communication-state heuristic retained for compatibility.

    Wave 1.3 does not treat this field as psychological or professional truth,
    and client-safe memory retrieval never exposes it.
    """

    msg = message.lower()

    if any(x in msg for x in ["urgent", "asap", "immediately"]):
        return "urgent"

    if any(x in msg for x in ["worried", "scared", "concerned"]):
        return "anxious"

    if any(x in msg for x in ["annoying", "frustrating", "again"]):
        return "frustrated"

    return "calm"


def detect_urgency(message: str) -> str:
    msg = message.lower()

    if any(x in msg for x in ["urgent", "asap", "now", "immediately"]):
        return "high"

    if any(x in msg for x in ["soon", "warning light", "issue"]):
        return "moderate"

    return "low"


def detect_escalation(message: str) -> str:
    msg = message.lower()

    if any(x in msg for x in ["safe to drive", "can i drive"]):
        return "unsafe_operation"

    if any(x in msg for x in ["urgent", "immediately"]):
        return "priority_review"

    if any(x in msg for x in ["check", "inspect", "review"]):
        return "review_advised"

    return "monitor"


# =========================
# SUMMARY GENERATOR
# =========================


def generate_summary(message, escalation, urgency):
    return (
        f"Client reported: '{message[:120]}'. "
        f"Escalation state: {escalation}. "
        f"Urgency assessed as {urgency}."
    )


def _validate_memory_metadata(
    *,
    visibility: str,
    provenance: str,
    verification_state: str,
    conversation_id: str | None,
) -> None:
    if visibility not in MEMORY_VISIBILITIES:
        raise ValueError("unsupported conversation-record visibility")
    if provenance not in MEMORY_PROVENANCE:
        raise ValueError("unsupported conversation-record provenance")
    if verification_state not in MEMORY_VERIFICATION_STATES:
        raise ValueError("unsupported conversation-record verification state")
    if conversation_id is not None and (
        not conversation_id.strip() or len(conversation_id.strip()) > 64
    ):
        raise ValueError("conversation_id must be a non-empty value up to 64 chars")


# =========================
# MAIN LOGGER
# =========================


def log_conversation_record(
    user_id,
    vehicle_id,
    message,
    *,
    conversation_id: str | None = None,
    visibility: str = "internal",
    client_summary: str | None = None,
    source: str = "conversation_logger",
    provenance: str = "rules",
    verification_state: str = "unverified",
    commit: bool = True,
):
    """Create a durable, vehicle-scoped operational conversation record.

    Existing callers retain their current behavior through ``commit=True`` and
    an internal default visibility. Wave 1.3 orchestration can set
    ``commit=False`` so chat turns, summaries and material audit records share a
    caller-owned transaction.

    ``advisor_summary`` and legacy communication-state fields remain internal
    operational data. A client-visible memory row must provide a separate
    ``client_summary``; retrieval never falls back to the advisor summary.
    """

    _validate_memory_metadata(
        visibility=visibility,
        provenance=provenance,
        verification_state=verification_state,
        conversation_id=conversation_id,
    )
    if visibility == "client" and not (client_summary or "").strip():
        raise ValueError("client-visible conversation records require client_summary")

    emotion = detect_emotion(message)
    escalation = detect_escalation(message)
    analysis = analyze_conversation(message)

    record = ConversationRecord(
        user_id=user_id,
        vehicle_id=vehicle_id,
        conversation_id=(conversation_id.strip() if conversation_id else None),
        concern=message[:255],
        advisor_summary=analysis["summary"],
        client_summary=(client_summary.strip() if client_summary else None),
        emotional_state=analysis["emotion"] or emotion,
        urgency_level=analysis["urgency"],
        recommended_action=analysis["action"],
        escalation_level=escalation,
        consultation_related=False,
        visibility=visibility,
        source=source,
        provenance=provenance,
        verification_state=verification_state,
        created_at=datetime.utcnow(),
    )

    db.session.add(record)
    db.session.flush()

    if commit:
        db.session.commit()

    return record
