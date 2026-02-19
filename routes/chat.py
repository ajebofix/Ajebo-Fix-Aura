from flask import Blueprint, request, jsonify, session, current_app
from flask_login import login_required, current_user

from models import CarOwnership
from services.vehicle_intelligence import calculate_vehicle_health
from services.rina_chat_engine import RinaChatEngine

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
                    "reply": (
                        "I’m here with you. "
                        "You can ask about a vehicle’s score, condition, or next steps."
                    )
                }
            ),
            200,
        )

    try:
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
                        "reply": (
                            "I don’t see any vehicles under your care yet. "
                            "Once one is added, I can assist."
                        )
                    }
                ),
                200,
            )

        # --------------------------------
        # HARD SOURCE OF TRUTH (ORDERED)
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
                vehicle_list = _build_vehicle_list(ownerships)
                return (
                    jsonify(
                        {
                            "reply": (
                                "You have multiple vehicles under your care.\n\n"
                                "Which vehicle would you like to discuss?\n\n"
                                f"{vehicle_list}\n\n"
                                "Reply with a number or the vehicle name."
                            )
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

        # --------------------------------
        # Persist FINAL session truth
        # --------------------------------
        session["rina_context"] = {
            "vehicle_id": car.id,
            "vehicle": f"{car.brand} {car.model} {car.year}",
            "health_status": health.get("health_status"),
            "health_score": health.get("health_score"),
        }

        return jsonify({"reply": reply}), 200

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
