# rina/brain.py

from models import db, ChatMessage, UserMemory, Car, EscalationLog
from flask_login import current_user
from datetime import datetime
import re

from .ai_brain import generate_rina_response


# ====================
# HELPERS
# ====================


def get_user_name(user):
    """
    Safely extract user's display name.
    Never returns None.
    """
    return (
        getattr(user, "first_name", None)
        or getattr(user, "name", None)
        or getattr(user, "username", None)
        or (user.email.split("@")[0] if getattr(user, "email", None) else None)
        or "there"
    )


# ====================
# CONTEXT BUILDER
# ====================


def build_rina_context(user_message: str) -> dict:
    """
    Build full intelligence context for Rina
    """

    user = current_user

    # -------------------------
    # USER MEMORY
    # -------------------------
    memory = None
    name = "there"

    if user.is_authenticated:
        memory = UserMemory.query.filter_by(user_id=user.id).first()
        if memory and memory.name:
            name = memory.name
        else:
            name = get_user_name(user)

    # -------------------------
    # VEHICLE CONTEXT
    # -------------------------
    cars = []
    if user.is_authenticated:
        cars = Car.query.filter_by(owner_id=user.id).all()

    vehicle_info = [f"{c.make} {c.model} {c.year}" for c in cars] if cars else []

    # -------------------------
    # CHAT HISTORY (last 100)
    # -------------------------
    messages = []
    if user.is_authenticated:
        messages = (
            ChatMessage.query.filter_by(user_id=user.id)
            .order_by(ChatMessage.timestamp.desc())
            .limit(8)
            .all()
        )

    history = [{"role": m.role, "content": m.message} for m in reversed(messages)]

    # -------------------------
    # INTENT DETECTION
    # -------------------------
    intent = detect_intent(user_message)

    # -------------------------
    # LAST USER MESSAGE
    # -------------------------
    last_user_message = None
    for m in reversed(messages):
        if m.role == "user":
            last_user_message = m.message
            break

    # -------------------------
    # HEALTH SIGNALS
    # -------------------------
    health_alert = None

    if cars:
        primary_car = cars[0]

        if hasattr(primary_car, "mileage") and primary_car.mileage:
            if primary_car.mileage > 8000:
                health_alert = "Service may be due soon."

    # -------------------------
    # PROACTIVE NOTE
    # -------------------------
    proactive_note = None
    if health_alert:
        proactive_note = f"Note: {health_alert}"

    return {
        "user_name": name,
        "vehicles": vehicle_info,
        "history": history,
        "has_history": len(history) > 0,
        "last_user_message": last_user_message,
        "message": user_message,
        "intent": intent,
        "health_alert": health_alert,
        "proactive_note": proactive_note,
    }


# ====================
# PROACTIVE MESSAGES
# ====================


def get_proactive_message(user):
    if not user.is_authenticated:
        return None

    car = Car.query.filter_by(owner_id=user.id).first()

    if car and hasattr(car, "mileage") and car.mileage:
        if car.mileage > 8000:
            return "Your vehicle may be due for service soon."

    return None


# ====================
# INTENT DETECTION
# ====================


def detect_intent(message: str) -> str:
    message = message.lower()

    if any(x in message for x in ["book", "appointment", "check my car"]):
        return "booking"

    if any(x in message for x in ["problem", "issue", "noise", "fault"]):
        return "diagnostic"

    if any(x in message for x in ["thanks", "okay"]):
        return "casual"

    return "general"


# ====================
# MESSAGE SAVING
# ====================


def save_message(role: str, message: str):
    if not current_user.is_authenticated:
        return

    chat = ChatMessage(
        user_id=current_user.id,
        role=role,
        message=message,
        timestamp=datetime.utcnow(),
    )

    db.session.add(chat)
    db.session.commit()


# ====================
# NAME EXTRACTION
# ====================


def extract_name(message: str):
    patterns = [
        r"my name is (\w+)",
        r"i am (\w+)",
        r"call me (\w+)",
    ]

    message = message.lower()

    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return match.group(1).capitalize()

    return None


# ====================
# COMPLAINT DETECTION
# ====================


def is_complaint(message: str) -> bool:
    message = message.lower()

    triggers = [
        "not happy",
        "unhappy",
        "bad service",
        "you messed up",
        "this is wrong",
    ]

    return any(t in message for t in triggers)


# ====================
# ADMIN NOTIFICATION
# ====================


def notify_admin(message: str):
    print(f"ESCALATION ALERT: {message}")

    # Future:
    # - email
    # - push notification
    # - dashboard alerts
