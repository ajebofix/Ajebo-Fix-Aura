"""Authority-first A.J. Rina chat routes for Wave 1.3.

The chat surface no longer guesses a vehicle from free text, selects the first
vehicle, reads user-wide chat history, invokes the legacy Rina engine, or stores
broad context blobs in Flask session. Session state contains only short-lived
vehicle/conversation identifiers and is re-authorized on every request.
"""

from __future__ import annotations

import re
import uuid

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    session,
)
from flask_login import current_user, login_required
from sqlalchemy import or_

from extensions import db
from models import (
    AdvisorNote,
    Car,
    CarDriver,
    CarOwnership,
    Consultation,
    TreatmentPlan,
    User,
    VehicleAssessment,
)
from services.rina_audit import record_rina_audit
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
from services.rina_speaker import account_help, describe_speaker, speaker_identity

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

    resolved_conversation_id = (conversation_id or "").strip()[
        :64
    ] or _new_conversation_id()
    session[_SESSION_CAR_KEY] = car_id
    session[_SESSION_CONVERSATION_KEY] = resolved_conversation_id
    return resolved_conversation_id


def _vehicle_choice(car: Car) -> dict[str, object]:
    """Return an authority-checked, disambiguated vehicle choice."""

    authority = resolve_rina_authority(user_id=current_user.id, car_id=car.id)
    ownership = (
        CarOwnership.query.filter_by(car_id=car.id, is_active=True)
        .order_by(CarOwnership.id.desc())
        .first()
    )
    owner = (
        db.session.get(User, ownership.user_id)
        if ownership is not None and ownership.user_id
        else None
    )
    client_name = (owner.name or "").strip() if owner is not None else ""
    plate_number = (
        (ownership.plate_number or "").strip() if ownership is not None else ""
    )
    vin = (car.vin or "").strip().upper()
    vin_tail = vin[-6:] if vin else ""

    detail_parts = [
        part
        for part in (
            client_name,
            plate_number,
            f"VIN …{vin_tail}" if vin_tail else "",
        )
        if part
    ]
    context_label = " · ".join([car.rina_display_name, *detail_parts])

    return {
        "car_id": car.id,
        "label": car.rina_display_name,
        "authority": authority.authority,
        "client_name": client_name or None,
        "plate_number": plate_number or None,
        "vin_tail": vin_tail or None,
        "context_label": context_label,
    }


def _professional_vehicle_search(
    query: str,
    *,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Search only the professional vehicle scope that Rina can re-authorize."""

    if current_user.role not in {"admin", "advisor"}:
        return []

    clean_query = " ".join(str(query or "").strip().split())[:120]
    if len(clean_query) < 2:
        return []

    pattern = (
        "%"
        + clean_query.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        + "%"
    )

    search = Car.query.outerjoin(
        CarOwnership, CarOwnership.car_id == Car.id
    ).outerjoin(User, User.id == CarOwnership.user_id)

    search = search.filter(
        or_(
            Car.brand.ilike(pattern, escape="\\"),
            Car.model.ilike(pattern, escape="\\"),
            Car.vin.ilike(pattern, escape="\\"),
            db.and_(
                CarOwnership.is_active.is_(True),
                or_(
                    CarOwnership.plate_number.ilike(pattern, escape="\\"),
                    User.name.ilike(pattern, escape="\\"),
                ),
            ),
        )
    )

    if current_user.role == "advisor":
        search = search.filter(
            or_(
                *[
                    Car.id.in_(
                        db.session.query(model.car_id).filter(
                            model.advisor_id == current_user.id
                        )
                    )
                    for model in (
                        Consultation,
                        VehicleAssessment,
                        TreatmentPlan,
                        AdvisorNote,
                    )
                ]
            )
        )

    choices: list[dict[str, object]] = []
    for candidate in search.distinct().order_by(Car.id).limit(limit).all():
        try:
            choices.append(_vehicle_choice(candidate))
        except RinaAuthorityError:
            continue

    choices.sort(
        key=lambda item: (
            str(item.get("client_name") or "").lower(),
            str(item["label"]).lower(),
            int(item["car_id"]),
        )
    )
    return choices


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
    choices = _authorized_vehicle_choices(explicit_car_id=page_car_id or active_car_id)
    choice_ids = {int(item["car_id"]) for item in choices}

    return (
        jsonify(
            {
                "vehicles": choices,
                "speaker": speaker_identity(current_user.id),
                "account_welcome": describe_speaker(speaker_identity(current_user.id)),
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


@chat_bp.get("/chat/vehicle-search")
@login_required
def chat_vehicle_search():
    """Bounded professional search for an explicit Rina vehicle context."""

    if current_user.role not in {"admin", "advisor"}:
        return jsonify({"error": "Professional vehicle search is not available."}), 403

    query = str(request.args.get("q") or "").strip()[:120]
    return (
        jsonify(
            {
                "query": query,
                "vehicles": _professional_vehicle_search(query, limit=20),
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

    if car_id is None and data.get("clear") is True:
        _clear_rina_binding()
        return (
            jsonify(
                {
                    "car_id": None,
                    "conversation_id": None,
                    "authority": None,
                    "label": "Advisor overview",
                }
            ),
            200,
        )

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
                "label": car.rina_display_name,
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


@chat_bp.get("/chat/workspace")
@login_required
def rina_workspace():
    """An explicit, authenticated entry point for account help and vehicle review."""
    if not current_user.is_active:
        abort(403)
    car_id = _coerce_car_id(request.args.get("car_id"))
    car = None
    if car_id is not None:
        try:
            resolve_rina_authority(user_id=current_user.id, car_id=car_id)
        except RinaAuthorityError:
            abort(403)
        car = db.session.get(Car, car_id)

    query = str(request.args.get("q") or "").strip()[:120]
    matches = []
    professional = current_user.role in {"admin", "advisor"}
    if professional and len(query) >= 2:
        # Search is explicit and bounded; dedicated advisors see only linked vehicles.
        pattern = (
            "%"
            + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
        )
        search = Car.query.outerjoin(
            CarOwnership, CarOwnership.car_id == Car.id
        ).outerjoin(User, User.id == CarOwnership.user_id)
        search = search.filter(
            or_(
                Car.brand.ilike(pattern, escape="\\"),
                Car.model.ilike(pattern, escape="\\"),
                Car.vin.ilike(pattern, escape="\\"),
                db.and_(
                    CarOwnership.is_active.is_(True),
                    or_(
                        CarOwnership.plate_number.ilike(pattern, escape="\\"),
                        User.name.ilike(pattern, escape="\\"),
                    ),
                ),
            )
        )
        if current_user.role == "advisor":
            search = search.filter(
                or_(
                    *[
                        Car.id.in_(
                            db.session.query(model.car_id).filter(
                                model.advisor_id == current_user.id
                            )
                        )
                        for model in (
                            Consultation,
                            VehicleAssessment,
                            TreatmentPlan,
                            AdvisorNote,
                        )
                    ]
                )
            )
        for candidate in search.distinct().order_by(Car.id).limit(20).all():
            try:
                resolve_rina_authority(user_id=current_user.id, car_id=candidate.id)
            except RinaAuthorityError:
                continue
            matches.append(candidate)
    return render_template(
        "chat/workspace.html",
        car=car,
        query=query,
        matches=matches,
        professional=professional,
    )


@chat_bp.post("/chat/account")
@login_required
def chat_account():
    """Account-only help never restores a vehicle binding or reads vehicle memory."""
    try:
        identity = speaker_identity(current_user.id)
    except RinaAuthorityError:
        abort(403)
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()[:2000]
    reply = account_help(identity, message)
    record_rina_audit(
        request_id=_new_conversation_id(),
        user_id=current_user.id,
        car_id=None,
        authority=None,
        state="answered",
        outcome="answered",
        action_family="account_help",
        provider_status="not_called",
        metadata={"channel": "in_app", "provider_attempted": False},
    )
    return jsonify(
        reply=reply,
        state="answered",
        car_id=None,
        authority=None,
        intent="general",
        conversation_id=None,
    )
