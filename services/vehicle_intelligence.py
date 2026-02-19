"""
AURA — VEHICLE INTELLIGENCE ENGINE

Single source of truth for:
- Vehicle health interpretation
- Observed risk signals
- Maintenance adherence
- Reported concerns (non-diagnostic)
- Calm professional guidance

PRINCIPLES:
- No diagnosis
- No repair instruction
- No panic language
- Conservative influence from user input

PURE LOGIC ONLY:
- No Flask
- No permissions
- No database writes
"""

from datetime import datetime
from typing import Dict, List, Optional

from models import VehicleEvent, Car, CarOwnership, CarFault


# =====================================================
# INTERNAL CONFIGURATION (NON-USER FACING)
# =====================================================

SERVICE_INTERVAL_KM = 12_000

FAULT_SEVERITY_WEIGHTS = {
    "critical": 20,
    "high": 12,
    "medium": 6,
    "low": 3,
}

MAX_PENALTIES = {
    "maintenance": 40,
    "reported_concerns": 30,
    "driving_behavior": 15,
    "predictive_signals": 15,
}

SYSTEM_SIGNAL_TYPES = {
    "engine",
    "suspension",
    "transmission",
    "brake",
    "electrical",
    "cooling",
    "body_repair",
}


# =====================================================
# REPORTED CONCERNS (OBSERVATIONAL INPUT)
# =====================================================


def apply_reported_concern_penalty(car: Car, score: int) -> int:
    """
    Applies controlled influence from reported concerns.

    These are observations only.
    Severity is optional and handled defensively.
    """

    concerns = CarFault.query.filter_by(car_id=car.id).all()
    penalty = 0

    for concern in concerns:
        if getattr(concern, "status", None) == "resolved":
            continue

        severity = getattr(concern, "severity", None)
        penalty += FAULT_SEVERITY_WEIGHTS.get(severity, 3)

    penalty = min(penalty, MAX_PENALTIES["reported_concerns"])
    return max(score - penalty, 0)


# =====================================================
# CORE VEHICLE HEALTH INTERPRETATION
# =====================================================


def calculate_vehicle_health(car: Car, ownership: CarOwnership) -> Dict:
    """
    Produces interpreted vehicle health.
    Output is calm, conservative, and non-diagnostic.
    """

    score = 100
    observations: List[str] = []
    next_step: Optional[str] = None
    critical_flag = False

    # ---------------------------------
    # Mileage integrity
    # ---------------------------------

    current_mileage = resolve_current_mileage(car, ownership)

    if not current_mileage:
        score -= 10
        observations.append("Mileage data unavailable")
        next_step = "Update vehicle mileage to improve monitoring accuracy."

    # ---------------------------------
    # Maintenance adherence
    # ---------------------------------

    services = VehicleEvent.query.filter_by(
        car_id=car.id,
        ownership_id=ownership.id,
        event_type="service",
        is_deleted=False,
    ).all()

    if not services:
        score -= 20
        observations.append("No maintenance history on record")
        next_step = next_step or "Initial maintenance review recommended."

    overdue_intervals = sum(
        1
        for s in services
        if s.mileage and current_mileage - s.mileage > SERVICE_INTERVAL_KM
    )

    if overdue_intervals:
        penalty = min(overdue_intervals * 10, MAX_PENALTIES["maintenance"])
        score -= penalty
        observations.append(f"{overdue_intervals} maintenance interval(s) overdue")
        next_step = next_step or "Vehicle maintenance review recommended."

    # ---------------------------------
    # System-detected signals
    # ---------------------------------

    system_events = VehicleEvent.query.filter(
        VehicleEvent.car_id == car.id,
        VehicleEvent.ownership_id == ownership.id,
        VehicleEvent.event_type.in_(SYSTEM_SIGNAL_TYPES),
        VehicleEvent.is_deleted.is_(False),
    ).all()

    if system_events:
        penalty = sum(
            FAULT_SEVERITY_WEIGHTS.get(getattr(e, "severity", None), 3)
            for e in system_events
        )
        penalty = min(penalty, MAX_PENALTIES["predictive_signals"])
        score -= penalty
        observations.append(f"{len(system_events)} system signal(s) detected")

    if any(getattr(e, "severity", None) == "critical" for e in system_events):
        critical_flag = True
        next_step = "Professional assessment advised."

    # ---------------------------------
    # Reported concerns (soft influence)
    # ---------------------------------

    before = score
    score = apply_reported_concern_penalty(car, score)

    if score < before:
        observations.append("Reported concerns influencing monitoring status")

    # ---------------------------------
    # Driving behavior (minor)
    # ---------------------------------

    behavior_events = VehicleEvent.query.filter_by(
        car_id=car.id,
        ownership_id=ownership.id,
        event_type="driver_behavior",
        is_deleted=False,
    ).all()

    if behavior_events:
        penalty = min(len(behavior_events) * 3, MAX_PENALTIES["driving_behavior"])
        score -= penalty
        observations.append("Driving behavior may increase wear risk")

    # ---------------------------------
    # Predictive indicators
    # ---------------------------------

    predictive_events = VehicleEvent.query.filter_by(
        car_id=car.id,
        ownership_id=ownership.id,
        event_type="prediction",
        is_deleted=False,
    ).all()

    if predictive_events:
        penalty = min(len(predictive_events) * 4, MAX_PENALTIES["predictive_signals"])
        score -= penalty
        observations.append("Predictive indicators detected")

    # ---------------------------------
    # Normalize & classify
    # ---------------------------------

    score = max(score, 0)

    if critical_flag or score < 50:
        status = "critical"
        label = "Critical Review Required"
        next_step = next_step or "Immediate professional review recommended."

    elif score < 80:
        status = "attention"
        label = "Under Observation"
        next_step = next_step or "Scheduled review recommended."

    else:
        status = "healthy"
        label = "Stable"
        next_step = next_step or "Continue routine monitoring."

    return {
        "health_score": score,
        "health_status": status,
        "label": label,
        "risk_reasons": observations,
        "next_action": next_step,
        "generated_at": datetime.utcnow().isoformat(),
    }


# =====================================================
# NEXT STEP TRANSLATION
# =====================================================


def get_next_action(health: Dict) -> Dict:
    return {
        "type": health.get("health_status"),
        "message": health.get("next_action"),
    }


# =====================================================
# UTILITIES
# =====================================================


def resolve_current_mileage(car: Car, ownership: CarOwnership) -> int:
    latest_event = (
        VehicleEvent.query.filter_by(
            car_id=car.id,
            ownership_id=ownership.id,
            is_deleted=False,
        )
        .order_by(VehicleEvent.mileage.desc())
        .first()
    )

    if latest_event and latest_event.mileage:
        return latest_event.mileage

    return car.current_mileage or 0


# =====================================================
# CLIENT-FACING SUMMARY (A.J. RINA)
# =====================================================


def generate_rina_insight(car: Car, ownership: CarOwnership) -> str:
    health = calculate_vehicle_health(car, ownership)

    if health["health_status"] == "healthy":
        return "Your vehicle is currently stable. No urgent issues are detected."

    if health["health_status"] == "attention":
        return (
            "Your vehicle is under observation. "
            "A few areas are being monitored to prevent future issues."
        )

    return (
        "Your vehicle requires professional attention. "
        "This does not indicate failure, but timely review is advised."
    )


# =====================================================
# BACKWARD COMPATIBILITY (V1 CONTRACT)
# =====================================================

VehicleIntelligenceService = apply_reported_concern_penalty
