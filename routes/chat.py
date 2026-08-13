"""Authority-first A.J. Rina chat routes for Wave 1.3.

The chat surface no longer guesses a vehicle from free text, selects the first
vehicle, reads user-wide chat history, invokes the legacy Rina engine, or stores
broad context blobs in Flask session. Session state contains only short-lived
vehicle/conversation identifiers and is re-authorized on every request.
"""

from __future__ import annotations

import re
import uuid

from flask import Blueprint, current_app, jsonify, request, session
from flask_login import current_user, login_required

from extensions import db
from models import Car, CarDriver, CarOwnership
from services.rina_authority import (
    RinaAuthorityError,
    resolve_rina_authority,
)
from services.rina_contracts import (
    RINA_STATE_AUTHORITY_DENIED,
    RINA_STATE_ESCALATION_REQUIRED,
    RINA_STATE_VEHICLE_REQUIRED,
)
from services.rina_material_summary import (
    MATERIAL_ADVISOR_REVIEW,
    MATERIAL_BOOKING_REQUEST,
    record_rina_material_summary,
)
from services.rina_memory_service import (
    load_rina_chat_history,
    save_rina_chat_turn,
)
from services.rina_orchestrator import orchestrate_rina


chat_bp = Blueprint("chat", __name__)

_SESSION_CAR_KEY = "rina_active_car_id"
_SESSION_CONVERSATION_KEY = "rina_conversation_id"
_BOOKING_PATTERN = re.compile(
    r"\b(book|consult|consultation|appointment|schedule|reserve|assessment)\b",
    re.IGNORECASE,
)


def detect_intent(message: str) -> str:
    """Return the small UI intent contract still used for the booking CTA."""

    return "booking" if _BOOKING_PATTERN.search(message or "") else "general"


def _coerce_car_id(value) -> int | None:
    try:
        car_id = int(value)
    except (TypeError, ValueError):
        return None
    return car_id if car_id > 0 else None


def _new_conversation_id() -> str:
    return uuid.uuid4().hex


def _clear_rina_binding() -> None:
    session.pop(_SESSION_CAR_KEY, None)
    session.pop(_SESSION_CONVERSATION_KEY, None)


def _validated_session_car_id() -> int | None:
    car_id = _coerce_car_id(session.get(_SESSION_CAR_KEY))
    if car_id is None:
        _clear_rina_binding()
        return None

    try:
        resolve_rina_authority(user_id=current_user.id, car_id=car_id)
    except RinaAuthorityError:
        _clear_rina_binding()
        return None

    return car_id


def _conversation_id_for(car_id: int) -> str:
    active_car_id = _validated_session_car_id()
    conversation_id = str(session.get(_SESSION_CONVERSATION_KEY) or "").strip()

    if active_car_id == car_id and conversation_id:
        return conversation_id[:64]

    return _new_conversation_id()


def _bind_rina_vehicle(*, car_id: int, conversation_id: str | None = None) -> str:
    previous_car_id = _coerce_car_id(session.get(_SESSION_CAR_KEY))
    if previous_car_id != car_id:
        conversation_id = None

    resolved_conversation_id = (
        (conversation_id or "").strip()[:64] or _new_conversation_id()
    )
    session[_SESSION_CAR_KEY] = car_id
    session[_SESSION_CONVERSATION_KEY] = resolved_conversation_id
    return resolved_conversation_id


def _vehicle_choice(car: Car) -> dict[str, object]:
    authority = resolve_rina_authority(user_id=current_user.id, car_id=car.id)
    return {
        "car_id": car.id,
        "label": f"{car.decoded_display_name} {car.year}",
        "authority": authority.authority,
    }


def _authorized_vehicle_choices(
    *,
    explicit_car_id: int | None = None,
) -> list[dict[str, object]]:
    """Return selectable Rina vehicles without exposing broad admin fleet data."""

    car_ids: set[int] = set()

    for ownership in CarOwnership.query.filter_by(
        user_id=current_user.id,
        is_active=True,
    ).all():
        if ownership.car_id:
            car_ids.add(int(ownership.car_id))

    for assignment in CarDriver.query.filter_by(
        user_id=current_user.id,
        is_active=True,
    ).all():
        if assignment.car_id:
            car_ids.add(int(assignment.car_id))

    # Advisor/administrator pages may explicitly name a vehicle without making
    # the Rina selector a broad fleet browser. The authority service still has
    # to prove access before that vehicle is returned.
    if explicit_car_id is not None:
        try:
            resolve_rina_authority(
                user_id=current_user.id,
                car_id=explicit_car_id,
            )
        except RinaAuthorityError:
            pass
        else:
            car_ids.add(explicit_car_id)

    cars = Car.query.filter(Car.id.in_(car_ids)).all() if car_ids else []
    choices = [_vehicle_choice(car) for car in cars]
    choices.sort(key=lambda item: (str(item["label"]).lower(), int(item["car_id"])))
    return choices


@chat_bp.get("/chat/context")
@login_required
def chat_context():
    """Return only the vehicle choices this user can explicitly bind to Rina."""

    page_car_id = _coerce_car_id(request.args.get("car_id"))
    active_car_id = _validated_session_car_id()
    choices = _authorized_vehicle_choices(explicit_car_id=page_car_id)
    choice_ids = {int(item["car_id"]) for item in choices}

    return (
        jsonify(
            {
                "vehicles": choices,
                "active_car_id": (
                    active_car_id if active_car_id in choice_ids else None
                ),
                "page_car_id": page_car_id if page_car_id in choice_ids else None,
                "conversation_id": (
                    session.get(_SESSION_CONVERSATION_KEY)
                    if active_car_id in choice_ids
                    else None
                ),
            }
        ),
        200,
    )


@chat_bp.post("/chat/select-vehicle")
@login_required
def select_chat_vehicle():
    """Bind Rina to one explicitly selected, re-authorized vehicle."""

    data = request.get_json(silent=True) or {}
    car_id = _coerce_car_id(data.get("car_id"))
    if car_id is None:
        return jsonify({"error": "A valid vehicle is required."}), 400

    try:
        authority = resolve_rina_authority(user_id=current_user.id, car_id=car_id)
    except RinaAuthorityError:
        return jsonify({"error": "That vehicle is not available to Rina."}), 403

    car = db.session.get(Car, car_id)
    if car is None:
        return jsonify({"error": "Vehicle not found."}), 404

    conversation_id = _bind_rina_vehicle(car_id=car_id)
    return (
        jsonify(
            {
                "car_id": car_id,
                "conversation_id": conversation_id,
                "authority": authority.authority,
                "label": f"{car.decoded_display_name} {car.year}",
            }
        ),
        200,
    )


@chat_bp.post("/chat")
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()
    explicit_car_id = _coerce_car_id(data.get("car_id"))
    car_id = explicit_car_id or _validated_session_car_id()
    intent = detect_intent(message)

    conversation_id = (
        _conversation_id_for(car_id) if car_id is not None else _new_conversation_id()
    )

    try:
        response = orchestrate_rina(
            user_id=current_user.id,
            car_id=car_id,
            message=message,
            channel="in_app",
            conversation_id=conversation_id,
            audit_commit=False,
        )

        if response.state not in {
            RINA_STATE_AUTHORITY_DENIED,
            RINA_STATE_VEHICLE_REQUIRED,
        }:
            conversation_id = _bind_rina_vehicle(
                car_id=response.car_id,
                conversation_id=conversation_id,
            )

            if message:
                save_rina_chat_turn(
                    user_id=current_user.id,
                    car_id=response.car_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=message,
                    channel="in_app",
                    commit=False,
                )

                save_rina_chat_turn(
                    user_id=current_user.id,
                    car_id=response.car_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=response.message,
                    channel="in_app",
                    commit=False,
                )

                if intent == "booking":
                    record_rina_material_summary(
                        user_id=current_user.id,
                        car_id=response.car_id,
                        conversation_id=conversation_id,
                        material_type=MATERIAL_BOOKING_REQUEST,
                        commit=False,
                    )

                if response.state == RINA_STATE_ESCALATION_REQUIRED:
                    record_rina_material_summary(
                        user_id=current_user.id,
                        car_id=response.car_id,
                        conversation_id=conversation_id,
                        material_type=MATERIAL_ADVISOR_REVIEW,
                        commit=False,
                    )

        db.session.commit()

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Rina chat transaction failed for user_id=%s car_id=%s",
            current_user.id,
            car_id,
        )
        return (
            jsonify(
                {
                    "reply": (
                        "Rina couldn't complete that request safely. Please try "
                        "again shortly."
                    ),
                    "intent": intent,
                    "car_id": car_id,
                    "authority": None,
                    "state": "provider_unavailable",
                    "conversation_id": None,
                    "uncertainty": "the chat transaction did not complete",
                    "escalation": None,
                    "evidence_refs": [],
                }
            ),
            503,
        )

    status_code = 403 if response.state == RINA_STATE_AUTHORITY_DENIED else 200
    return (
        jsonify(
            {
                "reply": response.message,
                "intent": intent,
                "car_id": response.car_id if response.car_id > 0 else None,
                "authority": response.authority or None,
                "state": response.state,
                "conversation_id": (
                    conversation_id
                    if response.state
                    not in {RINA_STATE_AUTHORITY_DENIED, RINA_STATE_VEHICLE_REQUIRED}
                    else None
                ),
                "uncertainty": response.uncertainty,
                "escalation": response.escalation,
                "evidence_refs": list(response.evidence_refs),
            }
        ),
        status_code,
    )


@chat_bp.get("/chat/history")
@login_required
def chat_history():
    explicit_car_id = _coerce_car_id(request.args.get("car_id"))
    car_id = explicit_car_id or _validated_session_car_id()

    if car_id is None:
        return jsonify({"messages": [], "state": RINA_STATE_VEHICLE_REQUIRED}), 200

    try:
        resolve_rina_authority(user_id=current_user.id, car_id=car_id)
        history = load_rina_chat_history(
            user_id=current_user.id,
            car_id=car_id,
            limit=20,
        )
    except RinaAuthorityError:
        if _coerce_car_id(session.get(_SESSION_CAR_KEY)) == car_id:
            _clear_rina_binding()
        return jsonify({"messages": [], "state": RINA_STATE_AUTHORITY_DENIED}), 403
    except Exception:
        current_app.logger.exception(
            "Rina chat history failed for user_id=%s car_id=%s",
            current_user.id,
            car_id,
        )
        return jsonify({"messages": [], "state": "unavailable"}), 503

    return (
        jsonify(
            {
                "messages": [
                    {
                        "role": item.role,
                        "message": item.content,
                        "timestamp": (
                            item.timestamp.isoformat() if item.timestamp else None
                        ),
                    }
                    for item in history
                ],
                "state": "answered",
                "car_id": car_id,
            }
        ),
        200,
    )
