from flask import Blueprint, jsonify, render_template, request, session
from flask_login import current_user, login_required

from models import CarOwnership
from services.vehicle_intelligence import (
    calculate_vehicle_health,
    get_next_action,
)


dashboard_bp = Blueprint(
    "dashboard",
    __name__,
    url_prefix="/dashboard",
)


# ======================================================
# DASHBOARD — VEHICLE OVERVIEW
# ======================================================


@dashboard_bp.get("/")
@login_required
def aura_home():
    """Render the owner's dashboard vehicle overview.

    `active_vehicle_id` remains a dashboard presentation state. The dashboard may
    choose a default card when the page first loads, but that automatic UI choice
    is no longer copied into Rina authority/memory context. Rina has its own
    explicit `rina_active_car_id` binding and re-authorizes it per request.
    """

    ownerships = (
        CarOwnership.query.filter(
            CarOwnership.user_id == current_user.id,
            CarOwnership.is_active.is_(True),
        )
        .order_by(CarOwnership.start_date.asc())
        .all()
    )

    active_vehicle_id = session.get("active_vehicle_id")
    active_ownership = None

    if active_vehicle_id:
        try:
            active_vehicle_id = int(active_vehicle_id)
        except (TypeError, ValueError):
            active_vehicle_id = None
            session.pop("active_vehicle_id", None)

    if active_vehicle_id:
        active_ownership = next(
            (
                ownership
                for ownership in ownerships
                if ownership.car and ownership.car.id == active_vehicle_id
            ),
            None,
        )

    # This default is dashboard UI state only. It must never silently grant Rina
    # a vehicle context.
    if active_ownership is None and ownerships:
        active_ownership = ownerships[0]
        session["active_vehicle_id"] = active_ownership.car.id

    vehicles = []
    for ownership in ownerships:
        car = ownership.car
        if not car:
            continue

        raw_health = calculate_vehicle_health(car, ownership)
        get_next_action(raw_health)

        vehicles.append(
            {
                "vehicle_id": car.id,
                "vehicle_identity": f"{car.brand} {car.model} {car.year}",
                "health_status": raw_health.get("health_status"),
                "health_label": raw_health.get("label"),
                "last_assessed_at": raw_health.get("generated_at"),
                "advisor_name": "Ajebo Fix",
                "reassurance": "Your vehicle is under professional monitoring.",
                "is_active": (
                    active_ownership is not None
                    and car.id == active_ownership.car.id
                ),
            }
        )

    return render_template(
        "dashboard.html",
        user=current_user,
        vehicles=vehicles,
        active_vehicle_id=(active_ownership.car.id if active_ownership else None),
    )


# ======================================================
# SET ACTIVE VEHICLE (EXPLICIT DASHBOARD UI ACTION)
# ======================================================


@dashboard_bp.post("/select-vehicle")
@login_required
def select_vehicle():
    """Set dashboard state and explicitly bind the same vehicle to Rina.

    Unlike the dashboard's initial default card, this endpoint represents a
    deliberate user action. It is therefore safe to establish Rina's short-lived
    vehicle binding after ownership is proven.
    """

    data = request.get_json(silent=True) or {}
    try:
        vehicle_id = int(data.get("vehicle_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Missing vehicle_id"}), 400

    if vehicle_id <= 0:
        return jsonify({"status": "error", "message": "Invalid vehicle_id"}), 400

    ownership = CarOwnership.query.filter(
        CarOwnership.user_id == current_user.id,
        CarOwnership.is_active.is_(True),
        CarOwnership.car_id == vehicle_id,
    ).first()

    if not ownership or not ownership.car:
        return (
            jsonify({"status": "error", "message": "Invalid vehicle selection"}),
            403,
        )

    previous_rina_car_id = session.get("rina_active_car_id")

    session["active_vehicle_id"] = ownership.car.id
    session["rina_active_car_id"] = ownership.car.id

    try:
        previous_rina_car_id = int(previous_rina_car_id)
    except (TypeError, ValueError):
        previous_rina_car_id = None

    if previous_rina_car_id != ownership.car.id:
        session.pop("rina_conversation_id", None)

    # Remove the legacy broad session context if it exists. Durable facts and
    # summaries now come from vehicle-scoped persistence after authorization.
    session.pop("rina_context", None)
    session.pop("rina_context_full", None)
    session.pop("selected_vehicle_id", None)

    return jsonify({"status": "ok", "vehicle_id": ownership.car.id})
