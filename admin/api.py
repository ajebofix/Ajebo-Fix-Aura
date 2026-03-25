from datetime import datetime

from models import User, Car, VehicleEvent, CarOwnership
from services.vehicle_intelligence import calculate_vehicle_health


# =====================================================
# AURA — ADMIN SYSTEM OVERVIEW (DATA ONLY)
# =====================================================


def get_admin_stats_data():
    """
    Returns high-level system overview for Aura advisors.
    Pure data. No presentation assumptions.
    """

    total_events = VehicleEvent.query.count()
    archived_events = VehicleEvent.query.filter_by(is_deleted=True).count()

    return {
        "clients": User.query.count(),
        "vehicles": Car.query.count(),
        "active_care_assignments": CarOwnership.query.filter_by(is_active=True).count(),
        "records": {
            "total": total_events,
            "active": total_events - archived_events,
            "archived": archived_events,
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


# =====================================================
# AURA — VEHICLE HEALTH OVERVIEW (NORMALIZED)
# =====================================================


def get_fleet_health_data():
    """
    Returns normalized vehicle health overview.
    One entry per actively assigned vehicle.

    Language intentionally avoids mechanical framing.
    """

    fleet = []

    cars = (
        Car.query.join(CarOwnership)
        .filter(CarOwnership.is_active.is_(True))
        .order_by(Car.id.asc())
        .all()
    )

    for car in cars:
        ownership = CarOwnership.query.filter_by(car_id=car.id, is_active=True).first()

        # Vehicles without active care are excluded
        if not ownership:
            continue

        health = calculate_vehicle_health(car, ownership)

        fleet.append(
            {
                # Identity
                "vehicle_id": car.id,
                "vehicle_label": f"{car.brand} {car.model} {car.year}",
                "assigned_client_id": ownership.user_id,
                # Context
                "current_mileage": car.current_mileage,
                # Interpreted Health (PRIMARY)
                "health_status": health["health_status"],  # green | amber | red
                "health_summary": health.get("label"),
                # Internal Metrics (NOT client-facing)
                "internal_health_score": health.get("health_score"),
                # Clinical Interpretation
                "active_risks": health.get("risk_reasons", []),
                # Guidance
                "recommended_next_step": health.get("next_action"),
            }
        )

    # Priority ordering: most attention required first
    fleet.sort(
        key=lambda x: (
            x["internal_health_score"]
            if x["internal_health_score"] is not None
            else 100
        )
    )

    return {
        "vehicles_under_care": fleet,
        "generated_at": datetime.utcnow().isoformat(),
    }
