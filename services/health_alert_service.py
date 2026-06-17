# services/health_alert_service.py

# =====================================================
# AURA — VEHICLE CARE SIGNAL ENGINE
# =====================================================

from datetime import datetime

from models import (
    db,
    Car,
    CarOwnership,
    VehicleHealthAlert,
)

from services.vehicle_intelligence import calculate_vehicle_health
from services.health_trend_service import (
    VehicleCareTrajectoryService as HealthTrendService,
)


class CareSignalService:
    """
    Aura Care Signal Engine

    Observes vehicle health patterns and raises or resolves
    advisory care signals.

    This system provides monitoring guidance only.
    It does not diagnose or prescribe repairs.
    """

    # =================================================
    # PUBLIC ENTRY POINT
    # =================================================

    @staticmethod
    def evaluate(car_id: int, trigger: str = "system"):
        """
        Evaluate vehicle state and update care signals.

        Typical triggers:
        - event_created
        - event_updated
        - event_deleted
        - ownership_transferred
        - manual
        """

        car = Car.query.get(car_id)
        if not car:
            return

        ownership = CarOwnership.query.filter_by(
            car_id=car.id,
            is_active=True,
        ).first()

        if not ownership:
            return

        # ---------------------------------
        # CURRENT VEHICLE HEALTH SNAPSHOT
        # ---------------------------------

        health = calculate_vehicle_health(car, ownership)

        health_score = health["health_score"]
        risk_reasons = health.get("risk_reasons", [])

        # ---------------------------------
        # HEALTH TRAJECTORY ANALYSIS
        # ---------------------------------

        trajectory = HealthTrendService.analyze_car_trajectory(car.id)

        # =================================================
        # CARE SIGNAL RULES (CALM & ADVISORY)
        # =================================================

        # 1️⃣ LOW HEALTH STATUS — ADVISOR REVIEW
        if health_score <= 40:
            CareSignalService._raise_signal(
                car,
                ownership,
                signal_type="low_health_status",
                severity="high",
                message=(
                    "Vehicle health status indicates elevated risk. "
                    "An advisor review is recommended."
                ),
            )
        else:
            CareSignalService._resolve_signal(car, ownership, "low_health_status")

        # 2️⃣ DECLINING TRAJECTORY
        if trajectory.get("rapid_decline"):
            CareSignalService._raise_signal(
                car,
                ownership,
                signal_type="declining_health_trajectory",
                severity="moderate",
                message=(
                    "A downward trend in vehicle health has been observed. "
                    "Continued monitoring or assessment is advised."
                ),
            )
        else:
            CareSignalService._resolve_signal(
                car, ownership, "declining_health_trajectory"
            )

        # 3️⃣ ELEVATED RISK INDICATORS
        elevated_risks = [r for r in risk_reasons if "predicted" in r.lower()]

        if elevated_risks:
            CareSignalService._raise_signal(
                car,
                ownership,
                signal_type="elevated_risk_indicator",
                severity="moderate",
                message=(
                    "One or more monitored components show elevated risk indicators. "
                    "An assessment may be appropriate."
                ),
            )
        else:
            CareSignalService._resolve_signal(car, ownership, "elevated_risk_indicator")

        # 4️⃣ MAINTENANCE MONITORING
        monitoring_items = [r for r in risk_reasons if "overdue" in r.lower()]

        if monitoring_items:
            CareSignalService._raise_signal(
                car,
                ownership,
                signal_type="maintenance_monitoring",
                severity="low",
                message=(
                    "Routine maintenance monitoring is recommended "
                    "based on current vehicle data."
                ),
            )
        else:
            CareSignalService._resolve_signal(car, ownership, "maintenance_monitoring")




        db.session.commit()

    # =================================================
    # INTERNAL HELPERS
    # =================================================

    @staticmethod
    def _raise_signal(car, ownership, signal_type, severity, message):
        """
        Create a care signal if one is not already active.
        """

        existing = VehicleHealthAlert.query.filter_by(
            car_id=car.id,
            ownership_id=ownership.id,
            alert_type=signal_type,
            is_active=True,
        ).first()

        if existing:
            return

        signal = VehicleHealthAlert(
            car_id=car.id,
            ownership_id=ownership.id,
            alert_type=signal_type,
            severity=severity,
            status="new",
            message=message,
            is_active=True,
            created_at=datetime.utcnow(),
        )

        db.session.add(signal)

    @staticmethod
    def _resolve_signal(car, ownership, signal_type):
        """
        Resolve an active care signal when conditions normalize.
        """

        signal = VehicleHealthAlert.query.filter_by(
            car_id=car.id,
            ownership_id=ownership.id,
            alert_type=signal_type,
            is_active=True,
        ).first()

        if not signal:
            return

        signal.is_active = False
        signal.resolved_at = datetime.utcnow()


# =====================================================
# Backward compatibility alias (V1 Frozen Contract)
# =====================================================

HealthAlertService = CareSignalService
