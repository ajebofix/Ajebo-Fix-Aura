from flask import Blueprint, request, jsonify, session, current_app
from flask_login import login_required, current_user
from werkzeug.wrappers import response

from models import CarOwnership, ChatMessage, db
from services.vehicle_intelligence import calculate_vehicle_health
from services.rina_chat_engine import RinaChatEngine
from datetime import datetime

chat_bp = Blueprint("chat", __name__)


# ======================================================
# Helpers
# ======================================================


def _build_vehicle_list(ownerships):
    """
    Builds a numbered list for vehicle selection.
    """
    lines = []
    for idx, ownership in enumerate(ownerships, start=1):
        car = ownership.car
        lines.append(f"{idx}. {car.brand} {car.model} {car.year}")
    return "\n".join(lines)


def _match_vehicle_from_message(message, ownerships):
    """
    Attempts to match user reply to a vehicle.
    Accepts:
    - numeric selection (1, 2, ...)
    - full or partial vehicle identity
    """
    msg = message.lower().strip()

    # Numeric selection
    if msg.isdigit():
        index = int(msg) - 1
        if 0 <= index < len(ownerships):
            return ownerships[index]

    # Keyword match
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

    if any(
        x in msg
        for x in [
            "book",
            "appointment",
            "check my car",
            "schedule",
            "reserve",
            "reserve my car",
        ]
    ):
        return "booking"

    if any(x in msg for x in ["problem", "issue", "noise", "fault"]):
        return "diagnostic"

    if any(x in msg for x in ["thanks", "okay", "thank you", "thank you very much"]):
        return "casual"

    return "general"


# ======================================================
# Chat Route (STRICT, VEHICLE-LOCKED)
# ======================================================


@chat_bp.route("/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    # --------------------------------
    # Always respond
    # --------------------------------
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

        # Intent detection
        intent = detect_intent(message)

        # --------------------------------
        # Fetch ALL active vehicles
        # --------------------------------
        ownerships = (
            CarOwnership.query.filter(
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
        # HARD SOURCE OF TRUTH (ORDERED)  VEHICLE RESOLUTION
        # --------------------------------
        ownership = None

        # 1️⃣ Dashboard-selected vehicle (ABSOLUTE PRIORITY)
        active_vehicle_id = session.get("active_vehicle_id")
        if active_vehicle_id:
            ownership = next(
                (o for o in ownerships if o.car and o.car.id == active_vehicle_id),
                None,
            )

        # 2️⃣ Chat-selected vehicle (fallback only)
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

        # 3️⃣ Single vehicle auto-lock
        if ownership is None and len(ownerships) == 1:
            ownership = ownerships[0]
            session["active_vehicle_id"] = ownership.car.id

        # 4️⃣ Enforced selection (ONLY if none chosen yet)
        if ownership is None:
            matched = _match_vehicle_from_message(message, ownerships)

            if not matched:
                return (
                    jsonify(
                        {
                            "reply": (
                                "You have multiple vehicles under your care.\n\n"
                                "Which vehicle would you like to discuss?\n\n"
                                f"{_build_vehicle_list(ownerships)}"
                            ),
                            "intent": intent,
                        }
                    ),
                    200,
                )

            ownership = matched
            session["selected_vehicle_id"] = ownership.car.id

        # --------------------------------
        # VEHICLE IS NOW HARD-LOCKED
        # --------------------------------
        car = ownership.car

        # --------------------------------
        # Calculate health (NO CROSS-LEAK)
        # --------------------------------
        try:
            health = calculate_vehicle_health(car, ownership)
        except Exception:
            current_app.logger.exception("Vehicle health calculation failed")
            health = {
                "health_status": "unknown",
                "label": "Unavailable",
                "risk_reasons": [],
                "health_score": None,
                "next_action": {
                    "message": "An advisor can assist with a manual review.",
                    "type": "info",
                },
            }

        # --------------------------------
        # Ask Rina (VEHICLE CONTEXT ENFORCED)
        # --------------------------------
        try:
            reply = RinaChatEngine.respond(
                message,
                {
                    **health,
                    "vehicle_id": car.id,
                    "vehicle_identity": f"{car.brand} {car.model} {car.year}",
                    "intent": intent,
                },
            )

            if not reply or not isinstance(reply, str):
                raise ValueError("Invalid Rina response")

        except Exception:
            current_app.logger.exception("RinaChatEngine.respond failed")
            reply = (
                "I’m having trouble responding right now. "
                "Please try again shortly or contact an advisor if this continues."
            )

        # Save AI response
        save_message("assistant", reply)

        # --------------------------------
        # Persist FINAL session truth
        # --------------------------------
        session["rina_context"] = {
            "vehicle_id": car.id,
            "vehicle": f"{car.brand} {car.model} {car.year}",
            "health_status": health.get("health_status"),
            "health_score": health.get("health_score"),
            "intent": health.get("intent"),
        }

        return jsonify({"reply": reply, "intent": intent}), 200

    except Exception:
        current_app.logger.exception("Chat route failed")
        return (
            jsonify(
                {
                    "reply": (
                        "Something went wrong while processing your request. "
                        "Please try again shortly."
                    )
                }
            ),
            200,
        )


# ============================================
# CHAT HISTORY
# =======================================


@chat_bp.route("/chat/history", methods=["GET"])
@login_required
def chat_history():
    messages = (
        ChatMessage.query.filter_by(user_id=current_user.id)
        .order_by(ChatMessage.timestamp.asc())
        .limit(20)
        .all()
    )

    return {"messages": [{"role": m.role, "message": m.message} for m in messages]}
