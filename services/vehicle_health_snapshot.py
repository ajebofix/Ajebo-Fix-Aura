from models import db, VehicleHealthSnapshot, Car, CarOwnership
from services.vehicle_intelligence import calculate_vehicle_health
from services.health_alert_service import HealthAlertService


# =====================================================
# VEHICLE HEALTH RECORD CREATION (AURA)
# =====================================================


def create_health_snapshot(
    car_id: int,
    ownership_id: int,
    recorded_via: str,
):
    """
    Creates a clinical-style vehicle health record.

    This is NOT a diagnostic.
    It is a documented assessment based on:
    - recorded usage
    - service history
    - logged concerns and records

    Language intent:
    - Snapshot → Health Record
    - Reasons → Observations
    - Triggered by → Recorded via
    """

    vehicle = Car.query.get(car_id)
    care_assignment = CarOwnership.query.get(ownership_id)

    if not vehicle or not care_assignment:
        return

    intelligence = calculate_vehicle_health(vehicle, care_assignment)

    # ---------------------------------
    # Persist health record
    # ---------------------------------

    record = VehicleHealthSnapshot(
        car_id=vehicle.id,
        ownership_id=care_assignment.id,
        # Internal fields (kept for V1 stability)
        health_score=intelligence["health_score"],
        health_status=intelligence["health_status"],
        reasons=intelligence["risk_reasons"],  # interpreted as observations
        triggered_by=recorded_via,  # interpreted as recorded_via
    )

    db.session.add(record)
    db.session.commit()

    # ---------------------------------
    # Re-evaluate alerts calmly
    # ---------------------------------

    HealthAlertService.evaluate(vehicle.id)
