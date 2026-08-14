from flask import Blueprint, jsonify, render_template, request, session, url_for
from flask_login import current_user, login_required

from models import CarDriver, CarOwnership
from services.rina_authority import (
    AUTHORITY_DRIVER,
    AUTHORITY_OWNER,
    RinaAuthorityError,
    resolve_rina_authority,
)
from services.vehicle_intelligence import (
    calculate_vehicle_health,
    get_next_action,
)


dashboard_bp = Blueprint(
    "dashboard",
    __name__,
    url_prefix="/dashboard",
)


def _dashboard_mode() -> str:
    if current_user.role == "driver":
        return "driver"
    if current_user.role == "admin":
        return "administrator"
    if current_user.role == "advisor":
        return "advisor"
    return "owner"


def _presentation_relationships(mode: str):
    if mode == "driver":
        return (
            CarDriver.query.filter(
                CarDriver.user_id == current_user.id,
                CarDriver.is_active.is_(True),
            )
            .order_by(CarDriver.assigned_at.asc())
            .all()
        )

    if mode == "owner":
        return (
            CarOwnership.query.filter(
                CarOwnership.user_id == current_user.id,
                CarOwnership.is_active.is_(True),
            )
            .order_by(CarOwnership.start_date.asc())
            .all()
        )

    # Advisor and administrator dashboards deliberately do not become broad
    # fleet browsers. Professional vehicle scope must be established from an
    # advisor/admin workflow where object-level access is explicit.
    return []


def _relationship_car(relationship):
    return getattr(relationship, "car", None)


def _owner_record_for_car(car_id: int):
    return CarOwnership.query.filter(
        CarOwnership.car_id == car_id,
        CarOwnership.is_active.is_(True),
    ).first()


def _authority_label(mode: str) -> str:
    return {
        "driver": "Assigned Driver",
        "advisor": "Advisor",
        "administrator": "Administrator",
        "owner": "Owner",
    }[mode]


# ======================================================
# DASHBOARD — AUTHORITY-AWARE VEHICLE OVERVIEW
# ======================================================


@dashboard_bp.get("/")
@login_required
def aura_home():
    """Render dashboard content for the account's actual Aura relationship.

    Dashboard presentation state remains separate from Rina authority. A default
    card may be selected for display, but Rina still requires its own explicit,
    re-authorized vehicle binding.
    """

    mode = _dashboard_mode()
    relationships = _presentation_relationships(mode)

    active_vehicle_id = session.get("active_vehicle_id")
    if active_vehicle_id:
        try:
            active_vehicle_id = int(active_vehicle_id)
        except (TypeError, ValueError):
            active_vehicle_id = None
            session.pop("active_vehicle_id", None)

    available_car_ids = {
        relationship.car.id
        for relationship in relationships
        if _relationship_car(relationship) is not None
    }

    if active_vehicle_id not in available_car_ids:
        active_vehicle_id = None

    if active_vehicle_id is None and relationships:
        first_car = _relationship_car(relationships[0])
        if first_car is not None:
            active_vehicle_id = first_car.id
            session["active_vehicle_id"] = first_car.id

    vehicles = []
    for relationship in relationships:
        car = _relationship_car(relationship)
        if not car:
            continue

        ownership = (
            relationship
            if isinstance(relationship, CarOwnership)
            else _owner_record_for_car(car.id)
        )

        if ownership is not None:
            raw_health = calculate_vehicle_health(car, ownership)
            get_next_action(raw_health)
            health_status = raw_health.get("health_status")
            health_label = raw_health.get("label")
            last_assessed_at = raw_health.get("generated_at")
        else:
            health_status = "attention"
            health_label = "Awaiting owner record"
            last_assessed_at = None

        if mode == "driver":
            view_url = url_for("driver.driver_car_view", car_id=car.id)
            reassurance = "You are assigned to this vehicle for operational reporting."
        else:
            view_url = url_for("cars.car_detail", car_id=car.id)
            reassurance = "Your vehicle is under professional monitoring."

        vehicles.append(
            {
                "vehicle_id": car.id,
                "vehicle_identity": f"{car.brand} {car.model} {car.year}",
                "health_status": health_status,
                "health_label": health_label,
                "last_assessed_at": last_assessed_at,
                "advisor_name": "Ajebo Fix",
                "reassurance": reassurance,
                "is_active": car.id == active_vehicle_id,
                "view_url": view_url,
                "authority": mode,
            }
        )

    if mode == "driver":
        empty_title = "No active vehicle assignment"
        empty_message = (
            "No vehicle is currently assigned to this driver account. "
            "An owner or Ajebo Fix administrator must assign a vehicle first."
        )
    elif mode in {"advisor", "administrator"}:
        empty_title = "Professional vehicle scope"
        empty_message = (
            "Open the Advisor Console and choose an authorised client vehicle. "
            "Aura will not expose a broad fleet here or guess professional scope."
        )
    else:
        empty_title = "Welcome to Aura"
        empty_message = (
            "No vehicles have been added yet. Begin your private automotive "
            "health journey now."
        )

    return render_template(
        "dashboard.html",
        user=current_user,
        vehicles=vehicles,
        active_vehicle_id=active_vehicle_id,
        dashboard_mode=mode,
        authority_label=_authority_label(mode),
        can_add_vehicle=mode == "owner",
        empty_title=empty_title,
        empty_message=empty_message,
    )


# ======================================================
# SET ACTIVE VEHICLE (EXPLICIT DASHBOARD UI ACTION)
# ======================================================


@dashboard_bp.post("/select-vehicle")
@login_required
def select_vehicle():
    """Set dashboard state and explicitly bind an owner/driver vehicle to Rina."""

    data = request.get_json(silent=True) or {}
    try:
        vehicle_id = int(data.get("vehicle_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Missing vehicle_id"}), 400

    if vehicle_id <= 0:
        return jsonify({"status": "error", "message": "Invalid vehicle_id"}), 400

    try:
        authority = resolve_rina_authority(
            user_id=current_user.id,
            car_id=vehicle_id,
        )
    except RinaAuthorityError:
        return (
            jsonify({"status": "error", "message": "Invalid vehicle selection"}),
            403,
        )

    if authority.authority not in {AUTHORITY_OWNER, AUTHORITY_DRIVER}:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Select professional vehicle scope from the advisor workflow.",
                }
            ),
            403,
        )

    previous_rina_car_id = session.get("rina_active_car_id")

    session["active_vehicle_id"] = vehicle_id
    session["rina_active_car_id"] = vehicle_id

    try:
        previous_rina_car_id = int(previous_rina_car_id)
    except (TypeError, ValueError):
        previous_rina_car_id = None

    if previous_rina_car_id != vehicle_id:
        session.pop("rina_conversation_id", None)

    session.pop("rina_context", None)
    session.pop("rina_context_full", None)
    session.pop("selected_vehicle_id", None)

    return jsonify(
        {
            "status": "ok",
            "vehicle_id": vehicle_id,
            "authority": authority.authority,
        }
    )
