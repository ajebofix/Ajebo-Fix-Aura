from flask import Blueprint, render_template, session, request, jsonify
from flask_login import login_required, current_user

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
# DASHBOARD — VEHICLE OVERVIEW (AUTHORITATIVE)
# ======================================================


@dashboard_bp.route("/", methods=["GET"])
@login_required
def aura_home():
    """
    Aura Home — Vehicle Overview

    SINGLE SOURCE OF TRUTH:
    - session["active_vehicle_id"]
    - Dashboard ALWAYS controls the active vehicle
    - Chat + Rina are READ-ONLY consumers
    """

    # ---------------------------------------------
    # Fetch all ACTIVE vehicles for the user
    # ---------------------------------------------
    ownerships = (
        CarOwnership.query.filter(
            CarOwnership.user_id == current_user.id,
            CarOwnership.is_active.is_(True),
        )
        .order_by(CarOwnership.start_date.asc())
        .all()
    )

    # ---------------------------------------------
    # Resolve ACTIVE VEHICLE (authoritative)
    # ---------------------------------------------
    active_vehicle_id = session.get("active_vehicle_id")
    active_ownership = None

    if active_vehicle_id:
        active_ownership = next(
            (o for o in ownerships if o.car and o.car.id == int(active_vehicle_id)),
            None,
        )

    # If session missing or stale → auto-select first
    if active_ownership is None and ownerships:
        active_ownership = ownerships[0]
        session["active_vehicle_id"] = active_ownership.car.id

    # ---------------------------------------------
    # Build UI-safe vehicle cards
    # ---------------------------------------------
    vehicles = []

    for ownership in ownerships:
        car = ownership.car
        if not car:
            continue

        raw_health = calculate_vehicle_health(car, ownership)
        advisor_guidance = get_next_action(raw_health)

        vehicles.append(
            {
                "vehicle_id": car.id,
                "vehicle_identity": f"{car.brand} {car.model} {car.year}",
                # HEALTH (SAFE FIELDS ONLY)
                "health_status": raw_health.get("health_status"),
                "health_label": raw_health.get("label"),
                # Timestamp (DEFENSIVE)
                "last_assessed_at": raw_health.get("generated_at"),
                # REASSURANCE
                "advisor_name": "Ajebo Fix",
                "reassurance": "Your vehicle is under professional monitoring.",
                # UI STATE
                "is_active": (
                    active_ownership is not None and car.id == active_ownership.car.id
                ),
            }
        )

    # ---------------------------------------------
    # Persist dashboard → chat alignment
    # ---------------------------------------------
    if active_ownership:
        session["rina_context"] = {
            "vehicle_id": active_ownership.car.id,
            "vehicle": (
                f"{active_ownership.car.brand} "
                f"{active_ownership.car.model} "
                f"{active_ownership.car.year}"
            ),
        }

    return render_template(
        "dashboard.html",
        user=current_user,
        vehicles=vehicles,
        active_vehicle_id=(active_ownership.car.id if active_ownership else None),
    )


# ======================================================
# SET ACTIVE VEHICLE (FROM DASHBOARD UI)
# ======================================================


@dashboard_bp.route("/select-vehicle", methods=["POST"])
@login_required
def select_vehicle():
    """
    Explicitly sets the active vehicle.

    THIS IS THE ONLY PLACE
    that mutates session["active_vehicle_id"].
    """

    data = request.get_json(silent=True) or {}
    vehicle_id = data.get("vehicle_id")

    if not vehicle_id:
        return jsonify({"status": "error", "message": "Missing vehicle_id"}), 400

    ownership = CarOwnership.query.filter(
        CarOwnership.user_id == current_user.id,
        CarOwnership.is_active.is_(True),
        CarOwnership.car_id == int(vehicle_id),
    ).first()

    if not ownership or not ownership.car:
        return jsonify({"status": "error", "message": "Invalid vehicle selection"}), 403

    # 🔑 AUTHORITATIVE STATE CHANGE
    session["active_vehicle_id"] = ownership.car.id

    # Keep Rina in sync
    session["rina_context"] = {
        "vehicle_id": ownership.car.id,
        "vehicle": (
            f"{ownership.car.brand} " f"{ownership.car.model} " f"{ownership.car.year}"
        ),
    }

    return jsonify({"status": "ok"})
