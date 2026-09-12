# services/health_alert_service.py

# =====================================================
# AURA — VEHICLE CARE SIGNAL ENGINE
# =====================================================

from datetime import datetime

from maintenance.care_signal import MaintenanceCareSignalService
from models import db, Car, CarOwnership
from services.care_signal_lifecycle import CareSignalLifecycleService
from services.vehicle_intelligence import calculate_vehicle_health
from services.health_trend_service import (
    VehicleCareTrajectoryService as HealthTrendService,
)


_ALLOWED_TRIGGERS = frozenset(
    {
        "system",
        "event_created",
        "event_updated",
        "event_deleted",
        "ownership_transferred",
        "mileage_observed",
        "maintenance_knowledge_verified",
        "maintenance_knowledge_rejected",
        "maintenance_knowledge_superseded",
        "service_classification_changed",
        "manual",
    }
)


class CareSignalService:
    """
    Aura Care Signal Engine.

    Deterministic rule evaluation may request care-signal lifecycle mutations,
    but the durable state/event contract is owned by CareSignalLifecycleService.
    This engine remains monitoring guidance only: it does not diagnose or
    prescribe repairs.
    """

    @staticmethod
    def evaluate(car_id: int, trigger: str = "system"):
        car = db.session.get(Car, car_id)
        if car is None:
            return

        ownership = CarOwnership.query.filter_by(
            car_id=car.id,
            is_active=True,
        ).first()
        if ownership is None:
            return

        health = calculate_vehicle_health(car, ownership)
        health_score = health["health_score"]
        trajectory = HealthTrendService.analyze_car_trajectory(car.id)

        trigger_key = trigger if trigger in _ALLOWED_TRIGGERS else "other"
        source_classification = f"deterministic_rule:{trigger_key}"
        occurred_at = datetime.utcnow()

        try:
            if health_score <= 40:
                CareSignalLifecycleService.raise_signal(
                    car_id=car.id,
                    alert_type="low_health_status",
                    severity="high",
                    message=(
                        "Vehicle health status indicates elevated risk. "
                        "An advisor review is recommended."
                    ),
                    source_classification=source_classification,
                    actor_type="system",
                    actor_user_id=None,
                    occurred_at=occurred_at,
                )
            else:
                CareSignalLifecycleService.resolve_active_system_signal(
                    car_id=car.id,
                    alert_type="low_health_status",
                    source_classification=source_classification,
                    occurred_at=occurred_at,
                )

            if trajectory.get("rapid_decline"):
                CareSignalLifecycleService.raise_signal(
                    car_id=car.id,
                    alert_type="declining_health_trajectory",
                    severity="moderate",
                    message=(
                        "A downward trend in vehicle health has been observed. "
                        "Continued monitoring or assessment is advised."
                    ),
                    source_classification=source_classification,
                    actor_type="system",
                    actor_user_id=None,
                    occurred_at=occurred_at,
                )
            else:
                CareSignalLifecycleService.resolve_active_system_signal(
                    car_id=car.id,
                    alert_type="declining_health_trajectory",
                    source_classification=source_classification,
                    occurred_at=occurred_at,
                )

            # Wave 2.4C intentionally does not canonicalize the legacy
            # elevated_risk_indicator rule that searched risk-reason prose for
            # the word "predicted". Existing rows remain historical data and
            # require advisor handling; no new automatic mutation is performed.

            # M5 cutover: maintenance monitoring is driven only by the typed M3
            # Maintenance Intelligence result.  Free-text risk-reason prose is
            # no longer an authority source.  Due/upcoming/unknown cannot raise
            # an overdue maintenance signal.
            MaintenanceCareSignalService.sync(
                car_id=car.id,
                trigger=trigger_key,
                occurred_at=occurred_at,
            )

            db.session.commit()
        except Exception:
            db.session.rollback()
            raise


# =====================================================
# Backward compatibility alias (V1 Frozen Contract)
# =====================================================

HealthAlertService = CareSignalService
