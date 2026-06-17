# services/conversation_logger.py

from datetime import datetime

from models import ConversationRecord, db

from services.conversation_analysis import analyze_conversation


# =========================
# SIMPLE DETECTORS
# =========================


def detect_emotion(message: str) -> str:
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


# =========================
# MAIN LOGGER
# =========================


def log_conversation_record(
    user_id,
    vehicle_id,
    message,
):
    """
    Creates clinical-style operational record.
    """

    emotion = detect_emotion(message)
    urgency = detect_urgency(message)
    escalation = detect_escalation(message)

    summary = generate_summary(
        message=message,
        escalation=escalation,
        urgency=urgency,
    )

    # =========================
    # ANALYZE CONVERSATION
    # =========================
    analysis = analyze_conversation(message)

    record = ConversationRecord(
        user_id=user_id,
        vehicle_id=vehicle_id,
        concern=message[:255],
        advisor_summary=analysis["summary"],
        emotional_state=analysis["emotion"],
        urgency_level=analysis["urgency"],
        recommended_action=analysis["action"],
        escalation_level=escalation,
        consultation_related=False,
        created_at=datetime.utcnow(),
    )

    db.session.add(record)
    db.session.commit()

    return record
