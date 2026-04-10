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

from models import CarOwnership, ChatMessage, db
from services.vehicle_intelligence import calculate_vehicle_health
from services.rina_chat_engine import RinaChatEngine
from datetime import datetime

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


def detect_intent(message: str) -> str:
    msg = message.lower()

    if any(x in msg for x in ["book", "appointment", "schedule", "reserve"]):
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

        intent = detect_intent(message)

        # --------------------------------
        # VEHICLES
        # --------------------------------
        ownerships = (
            CarOwnership.query.options(joinedload(CarOwnership.car))
            .filter(
                CarOwnership.user_id == current_user.id,
                CarOwnership.is_active.is_(True),
            )
            .order_by(CarOwnership.start_date.asc())
            .all()
        )

        for o in ownerships:
            print("OWNERSHIP:", o.id, "CAR:", o.car)

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
        }

        # --------------------------------
        # BUILD HISTORY
        # --------------------------------
        messages = (
            ChatMessage.query.filter_by(user_id=current_user.id)
            .order_by(ChatMessage.timestamp.desc())
            .limit(200)
            .all()
        )

        history = [{"role": m.role, "content": m.message} for m in reversed(messages)]

        # --------------------------------
        # RINA RESPONSE (FIXED CONTEXT)
        # --------------------------------
        try:
            # LOAD MEMORY
            memory = session.get("rina_context_full", {})

            rina_context = {
                **memory,  # restore pending + active vehicle
            }

            # UPDATE MESSGE
            rina_context["message"] = message

            # CORE DATA
            rina_context.update(
                {
                    "user_name": get_user_name(current_user),
                    "vehicle_id": car.id,
                    "vehicle_identity": f"{car.brand} {car.model} {car.year}",
                    "vehicles": [
                        f"{o.car.brand} {o.car.model} {o.car.year}" for o in ownerships
                    ],
                    "history": history,
                    "intent": intent,
                    **health_context,
                }
            )

            # CALL AI WITH FULL CONTEXT
            reply = RinaChatEngine.respond(message, rina_context)

            # SAVE MEMORY BACK
            session["rina_context_full"] = {
                "vehicle_identity": rina_context.get("vehicle_identity"),
                "pending_vehicle": rina_context.get("pending_vehicle"),
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

        return jsonify({"reply": reply, "intent": intent}), 200

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
            .limit(200)
            .all()
        )

        return {
            "messages": [{"role": m.role, "message": m.message} for m in messages]
        }, 200

    except Exception:
        current_app.logger.exception("Chat history failed")
        return {"messages": []}, 200


# ======================================================
# BOOK CONSULTATION
# ======================================================


@chat_bp.route("/book-consultation", methods=["GET", "POST"])
@login_required
def book_consultation():
    return render_template("chat/book_consultation.html")
