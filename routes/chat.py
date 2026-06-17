# routes/chat.py

from flask import (
    Blueprint,
    request,
    jsonify,
    session,
    current_app,
    render_template,
)
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload

from models import Car, CarOwnership, ChatMessage, db, CarDriver
from services.vehicle_intelligence import calculate_vehicle_health
from services.rina_chat_engine import RinaChatEngine
from services.rina_context_service import RinaContextService
from services.conversation_logger import log_conversation_record
from datetime import datetime, timedelta

import re

chat_bp = Blueprint("chat", __name__)


# ======================================================
# Helpers
# ======================================================


def get_user_name(user):
    """
    Safely extracts user's display name without breaking.
    """
    return (
        getattr(user, "first_name", None)
        or getattr(user, "name", None)
        or getattr(user, "username", None)
        or (user.email.split("@")[0] if getattr(user, "email", None) else None)
        or "there"
    )


def _build_vehicle_list(ownerships):
    lines = []
    for idx, ownership in enumerate(ownerships, start=1):
        car = ownership.car
        lines.append(f"{idx}. {car.brand} {car.model} {car.year}")
    return "\n".join(lines)


def _match_vehicle_from_message(message, ownerships):
    msg = message.lower().strip()

    if msg.isdigit():
        index = int(msg) - 1
        if 0 <= index < len(ownerships):
            return ownerships[index]

    for ownership in ownerships:
        car = ownership.car
        identity = f"{car.brand} {car.model} {car.year}".lower()
        if msg in identity:
            return ownership

    return None


def save_message(role, text):
    chat = ChatMessage(
        user_id=current_user.id,
        role=role,
        message=text,
        timestamp=datetime.utcnow(),
    )

    db.session.add(chat)
    db.session.commit()


def extract_topics(text):
    topics = []

    mapping = {
        "warning": "warning_lights",
        "engine": "engine",
        "transmission": "transmission",
        "brake": "brakes",
    }

    for k, v in mapping.items():
        if k in text.lower():
            topics.append(v)

    return topics


def detect_intent(message: str) -> str:
    msg = message.lower().strip()

    if re.search(r"\b(book|consult|appointment|schedule|reserve|assessment)\b", msg):
        return "booking"

    if any(x in msg for x in ["problem", "issue", "noise", "fault"]):
        return "diagnostic"

    if any(x in msg for x in ["thanks", "okay", "thank you"]):
        return "casual"

    return "general"


# ======================================================
# Chat Route
# ======================================================


@chat_bp.route("/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return (
            jsonify(
                {
                    "reply": "I’m here with you. Ask me about your vehicle.",
                    "intent": "general",
                }
            ),
            200,
        )

    try:
        # Save user message
        save_message("user", message)

        # intent = detect_intent(message)

        # REMEMBER BOOKING INTENT
        intent = detect_intent(message)

        # IF USER WAS PREVIOUSLY BOOKING, KEEP IT ACTIVE
        session["topic"] = {
            "type": "intent",
            "updated_at": datetime.utcnow().isoformat(),
        }

        topic = session.get("topic")

        if topic:
            updated = datetime.fromisoformat(topic["updated_at"])
            if (datetime.utcnow() - updated) > timedelta(minutes=10):
                session.pop("topic", None)

        # --------------------------------
        # VEHICLES
        # --------------------------------

        role = current_user.role if hasattr(current_user, "role") else "owner"

        ownerships = []
        driver_links = []

        if role == "driver":
            driver_links = (
                CarDriver.query.options(joinedload(CarDriver.car))
                .filter(
                    CarDriver.user_id == current_user.id,
                    CarDriver.is_active.is_(True),
                )
                .all()
            )

            if not driver_links:
                return (
                    jsonify(
                        {
                            "reply": "You're not assigned to any vehicle yet. Check with your owner.",
                            "intent": intent,
                        }
                    ),
                    200,
                )

            # Normalize into ownership-like structure
            ownerships = driver_links

        else:
            ownerships = (
                CarOwnership.query.options(joinedload(CarOwnership.car))
                .filter(
                    CarOwnership.user_id == current_user.id,
                    CarOwnership.is_active.is_(True),
                )
                .order_by(CarOwnership.start_date.asc())
                .all()
            )

        if not ownerships:
            return (
                jsonify(
                    {
                        "reply": "I don’t see any vehicles under your care yet.",
                        "intent": intent,
                    }
                ),
                200,
            )

        # --------------------------------
        # VEHICLE RESOLUTION
        # --------------------------------
        ownership = None

        active_vehicle_id = session.get("active_vehicle_id")
        if active_vehicle_id:
            ownership = next(
                (o for o in ownerships if o.car and o.car.id == active_vehicle_id),
                None,
            )

        if ownership is None:
            selected_vehicle_id = session.get("selected_vehicle_id")
            if selected_vehicle_id:
                ownership = next(
                    (
                        o
                        for o in ownerships
                        if o.car and o.car.id == selected_vehicle_id
                    ),
                    None,
                )

        if ownership is None and len(ownerships) == 1:
            ownership = ownerships[0]
            session["active_vehicle_id"] = ownership.car.id

        if ownership is None:
            matched = _match_vehicle_from_message(message, ownerships)

            if matched:
                ownership = matched
                session["selected_vehicle_id"] = ownership.car.id

            else:
                # AUTO SELECT FIRST VEHICLE
                ownership = ownerships[0]
                session["active_vehicle_id"] = ownership.car.id

        car = ownership.car

        # ==========================
        # CLINICAL RECORD LOGGING
        # ==========================

        log_conversation_record(
            user_id=current_user.id,
            vehicle_id=car.id if "car" in locals() else None,
            message=message,
        )

        # --------------------------------
        # HEALTH
        # --------------------------------
        try:
            health = calculate_vehicle_health(car, ownership) or {}
        except Exception:
            current_app.logger.exception("Health calc failed")
            health = {
                "health_status": "unknown",
                "health_score": None,
                "risk_reasons": [],
            }

        # Normalize keys (VERY IMPORTANT)
        health_context = {
            "health_status": health.get("health_status"),
            "health_score": health.get("health_score"),
            "risk_reasons": health.get("risk_reasons", []),
            "guidance": health.get("guidance"),
            "care_context": health.get("care_context"),
            "escalation": health.get("escalation"),
        }

        # --------------------------------
        # BUILD HISTORY
        # --------------------------------
        messages = (
            ChatMessage.query.filter_by(user_id=current_user.id)
            .order_by(ChatMessage.timestamp.desc())
            .limit(8)
            .all()
        )

        history = [{"role": m.role, "content": m.message} for m in reversed(messages)]

        # --------------------------------
        # RINA RESPONSE (FIXED CONTEXT)
        # --------------------------------
        try:
            # LOAD MEMORY
            memory = session.get("rina_context_full", {})

            context = {
                **memory,  # restore pending + active vehicle
            }

            # =========================
            # BUILD FULL CONTEXT
            # =========================
            context = RinaContextService.build(
                user=current_user,
                message=message,
                active_car_id=car.id if ownership else None,
            )

            context["explained_topics"] = memory.get("explained_topics", [])

            context["last_topics"] = [
                f"{m['role']}: {m['content'][:100]}"
                for m in context.get("history", [])[-3:]
            ]

            if not context.get("vehicle_identity"):
                context["vehicle_identity"] = memory.get("vehicle_identity")

            context["pending_vehicle"] = memory.get("pending_vehicle")

            context.update(health_context)
            context["intent"] = intent

            context.update(session.get("rina_context_full", {}))

            # =========================
            # RESPONSE
            # =========================

            # CALL AI WITH FULL CONTEXT
            # =========================
            # HARD BOOKING OVERRIDE
            # =========================
            if intent == "booking":
                reply = (
                    f"Good decision. Let's get your {car.brand} {car.model} scheduled."
                )

                # --------------------------------
                # WHATSAPP TRIGGER
                # --------------------------------

            else:
                reply = RinaChatEngine.respond(
                    user_id=current_user.id,
                    car_id=car.id,
                    message=message,
                    context=context,
                )

            # =========================
            # TOPIC DETECTION
            # =========================

            topics_detected = extract_topics(reply + " " + message)

            # merge with existing memory
            existing = context.get("explained_topics", [])
            updated_topics = list(set(existing + topics_detected))

            # SAVE MEMORY BACK
            session["rina_context_full"] = {
                "vehicle_identity": context.get("vehicle_identity"),
                "pending_vehicle": context.get("pending_vehicle"),
                "explained_topics": updated_topics,
            }

            if not isinstance(reply, str):
                raise ValueError("Invalid response")

        except Exception:
            current_app.logger.exception("Rina failed")
            reply = "I’m having trouble responding right now. Please try again shortly."

        # Save AI response
        save_message("assistant", reply)

        # --------------------------------
        # SESSION CONTEXT
        # --------------------------------
        session["rina_context"] = {
            "vehicle_id": car.id,
            "vehicle": f"{car.brand} {car.model} {car.year}",
            "health_status": health_context.get("health_status"),
            "health_score": health_context.get("health_score"),
            "intent": intent,
        }

        return jsonify({"reply": reply, "intent": intent, "car_id": car.id}), 200

    except Exception:
        current_app.logger.exception("Chat route failed")
        return (
            jsonify({"reply": "Something went wrong. Please try again shortly."}),
            200,
        )


# ======================================================
# CHAT HISTORY
# ======================================================


@chat_bp.route("/chat/history", methods=["GET"])
@login_required
def chat_history():
    try:
        messages = (
            ChatMessage.query.filter_by(user_id=current_user.id)
            .order_by(ChatMessage.timestamp.asc())
            .limit(8)
            .all()
        )

        return {
            "messages": [{"role": m.role, "message": m.message} for m in messages]
        }, 200

    except Exception:
        current_app.logger.exception("Chat history failed")
        return {"messages": []}, 200
