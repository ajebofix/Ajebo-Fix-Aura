from datetime import datetime
from typing import Dict, List

from models import (
    Car,
    CarOwnership,
    VehicleEvent,
    CarFault,
)
from services.vehicle_intelligence import calculate_vehicle_health
from services.health_interpreter import interpret_health


def build_vehicle_report(car: Car, ownership: CarOwnership) -> Dict:
    """
    Builds the authoritative Vehicle Medical File.
    Pure structured data. No formatting.
    """

    # -------------------------------
    # VEHICLE IDENTITY
    # -------------------------------
    vehicle_identity = {
        "name": f"{car.brand} {car.model} {car.year}",
        "vin": car.vin,
        "plate": ownership.plate_number,
        "assigned_advisor": "Ajebo Fix",
        "ownership_start": ownership.start_date,
        "generated_at": datetime.utcnow(),
    }

    # -------------------------------
    # HEALTH (INTERPRETED)
    # -------------------------------
    raw_health = calculate_vehicle_health(car, ownership)
    interpreted_health = interpret_health(raw_health)

    # -------------------------------
    # REPORTED CONCERNS (DEFENSIVE)
    # -------------------------------
    concerns = (
        CarFault.query.filter_by(car_id=car.id)
        .order_by(CarFault.created_at.desc())
        .all()
    )

    concerns_payload = []
    for c in concerns:
        concerns_payload.append(
            {
                "category": c.category,
                "severity": getattr(c, "severity", None),  # 🔒 SAFE
                "description": c.description,
                "status": c.status,
                "reported_at": c.reported_at,
            }
        )

    # -------------------------------
    # TREATMENT & SERVICE RECORDS
    # -------------------------------
    events = (
        VehicleEvent.query.filter_by(
            car_id=car.id,
            ownership_id=ownership.id,
            is_deleted=False,
        )
        .order_by(VehicleEvent.created_at.asc())
        .all()
    )

    treatments = []
    for e in events:
        treatments.append(
            {
                "type": e.event_type,
                "title": e.title,
                "description": e.description,
                "mileage": e.mileage,
                "date": e.created_at,
            }
        )

    # -------------------------------
    # TIMELINE (MERGED)
    # -------------------------------
    timeline = [
        {
            "date": ownership.start_date,
            "label": "Vehicle enrolled under Ajebo Fix care",
            "type": "system",
        }
    ]

    for c in concerns:
        timeline.append(
            {
                "date": c.reported_at,
                "label": f"Concern reported: {c.category.replace('_', ' ').title()}",
                "type": "concern",
            }
        )

    for e in events:
        timeline.append(
            {
                "date": e.created_at,
                "label": e.title,
                "type": "service",
            }
        )

    timeline.sort(key=lambda x: x["date"])

    return {
        "vehicle": vehicle_identity,
        "health": interpreted_health,
        "concerns": concerns_payload,
        "treatments": treatments,
        "timeline": timeline,
    }
