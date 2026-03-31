from models import db, ChatMessage, UserMemory, Car, EscalationLog
from flask_login import current_user
from datetime import datetime

from .ai_brain import generate_rina_response


def build_rina_context(user_message: str) -> dict:
    """
    Build full intelligence context for Rina
    """

    user = current_user

    # -------------------------
    # USER MEMORY
    # -------------------------
    memory = UserMemory.query.filter_by(user_id=user.id).first()

    name = (
        memory.name if memory and memory.name else getattr(user, "first_name", "there")
    )

    # -------------------------
    # VEHICLE CONTEXT
    # -------------------------
    car = Car.query.filter_by(owner_id=user.id).first()

    vehicle_info = None
    if car:
        vehicle_info = {
            "name": f"{car.make} {car.model} {car.year}",
            "last_service": str(getattr(car, "lasr_service_date", "Unknown")),
        }

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
    # SIMPLE INTENT DETECTION
    # -------------------------
    intent = detect_intent(user_message)

    return {
        "user_name": name,
        "vehicle": vehicle_info,
        "history": history,
        "message": user_message,
        "intent": intent,
    }


def detect_intent(message: str) -> str:
    message = message.lower()

    if any(x in message for x in ["book", "appointment", "check my car"]):
        return "booking"

    if any(x in message for x in ["problem", "issue", "noise", "fault"]):
        return "diagnostic"

    if any(x in message for x in ["thanks", "okay"]):
        return "casual"

    return "general"


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


def rina_chat(user_message: str) -> str:
    """
    Main brain entry point
    """

    # Save user message
    save_message("user", user_message)

    # Complaint detection
    if is_complaint(user_message):
        escalation = EscalationLog(
            user_id=current_user.id,
            message=user_message,
        )
        db.session.add(escalation)
        db.session.commit()

    # Build context
    context = build_rina_context(user_message)

    # Generate response
    response = generate_rina_response(context)

    # Save AI response
    save_message("assistant", response)

    return response


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
