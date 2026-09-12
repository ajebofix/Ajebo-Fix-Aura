"""Typed maintenance care-signal synchronization for Aura M5.

This module translates the read-only Maintenance Intelligence projection into the
existing governed ``VehicleHealthAlert`` lifecycle.  It deliberately owns no new
alert table and never commits; callers keep their existing transaction boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone

from extensions import db
from maintenance.state_engine import MaintenanceStateEngine, MaintenanceVehicleEvaluation
from models import Car
from services.care_signal_lifecycle import CareSignalLifecycleService


MAINTENANCE_SIGNAL_MESSAGE = (
    "Routine maintenance monitoring is recommended based on current vehicle data."
)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _source_classification(trigger: str) -> str:
    clean = "_".join((trigger or "system").strip().lower().split())
    token = f"maintenance_state:{clean or 'system'}"
    return token[:50]


class MaintenanceCareSignalService:
    """Synchronize one durable maintenance-monitoring signal from typed state."""

    @staticmethod
    def sync(
        *,
        car_id: int,
        trigger: str,
        evaluation: MaintenanceVehicleEvaluation | None = None,
        occurred_at: datetime | None = None,
    ):
        car = db.session.get(Car, car_id)
        if car is None:
            return None

        evaluation = evaluation or MaintenanceStateEngine.evaluate_vehicle(car=car)
        overdue = tuple(result for result in evaluation.results if result.state == "overdue")
        occurred_at = occurred_at or _utcnow_naive()
        source_classification = _source_classification(trigger)

        if overdue:
            signal = CareSignalLifecycleService.raise_signal(
                car_id=car.id,
                alert_type="maintenance_monitoring",
                severity="low",
                message=MAINTENANCE_SIGNAL_MESSAGE,
                source_classification=source_classification,
                actor_type="system",
                actor_user_id=None,
                occurred_at=occurred_at,
            )
        else:
            signal = CareSignalLifecycleService.resolve_active_system_signal(
                car_id=car.id,
                alert_type="maintenance_monitoring",
                source_classification=source_classification,
                occurred_at=occurred_at,
            )

        return {
            "evaluation": evaluation,
            "overdue_results": overdue,
            "signal": signal,
        }
