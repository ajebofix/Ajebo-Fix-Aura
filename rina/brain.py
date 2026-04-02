from models import db, ChatMessage, UserMemory, Car, EscalationLog
from flask_login import current_user
from datetime import datetime
import re

from .ai_brain import generate_rina_response


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
    memory = UserMemory.query.filter_by(user_id=user.id).first()

    name = None
    if memory and memory.name:
        name = memory.name
    elif user.first_name:
        name = user.first_name

    # -------------------------
    # VEHICLE CONTEXT
    # -------------------------
    cars = Car.query.filter_by(owner_id=user.id).all()

    vehicle_info = []
    if cars:
        vehicle_info = [f"{c.make} {c.model} {c.year}" for c in cars]

    # -------------------------
    # CHAT HISTORY (last 10)
    # -------------------------
    messages = (
        ChatMessage.query.filter_by(user_id=user.id)
        .order_by(ChatMessage.timestamp.desc())
        .limit(10)
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

        if hasattr(primary_car, "mileage") and primary_car.mileage > 8000:
            health_alert = "Service may be due soon."

    # -------------------------
    # PROACTIVE BEHAVVIOUR
    # -------------------------
    proactive__note = None

    if health_alert:
        proactive__note = f"Note: {health_alert}"

    return {
        "user_name": name,
        "vehicles": vehicle_info,
        "history": history,
        "last_user_message": last_user_message,
        "message": user_message,
        "intent": intent,
        "health_alert": health_alert,
        "proactive_note": proactive__note,
    }


# ====================
# PROACTIVE MESSAGES
# ====================
def get_proactive_message(user):
    car = Car.query.filter_by(owner_id=user.id).first()

    if car and car.mileage > 8000:
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
# MAIN ENTRY POINT
# ====================
def rina_chat(user_message: str) -> str:
    """
    Main brain entry point
    """

    # Save user message
    save_message("user", user_message)

    # Auto save name
    name = extract_name(user_message)

    if name:
        memory = UserMemory.query.filter_by(user_id=current_user.id).first()
        if not memory:
            memory = UserMemory(user_id=current_user.id)

            memory.name = name
            db.session.add(memory)
            db.session.commit()

    # Complaint detection
    if is_complaint(user_message):
        escalation = EscalationLog(
            user_id=current_user.id,
            message=user_message,
        )
        db.session.add(escalation)
        db.session.commit()

        notify_admin(user_message)

    # Build context
    context = build_rina_context(user_message)

    # Generate response
    response = generate_rina_response(context)

    # Save AI response
    save_message("assistant", response)

    return response


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

    # Later:
    # - send email to admin
    # - push notification
    # - dashboard alert
    # - etc
