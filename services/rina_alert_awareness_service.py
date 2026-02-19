from typing import Dict

from models import VehicleHealthAlert, CarOwnership


class RinaCareContextService:
    """
    Provides Rina with awareness of active care signals.
    Read-only.
    Non-directive.
    No behavioral instructions.
    """

    @staticmethod
    def get_active_care_context(car_id: int, user_id: int) -> Dict:
        """
        Returns calm, clinical awareness data for Rina.
        """

        ownership = CarOwnership.query.filter_by(
            car_id=car_id,
            user_id=user_id,
            is_active=True,
        ).first()

        if not ownership:
            return {
                "has_active_signals": False,
                "advisory_level": "none",
                "active_signals": [],
            }

        signals = VehicleHealthAlert.query.filter_by(
            car_id=car_id,
            ownership_id=ownership.id,
            is_active=True,
        ).all()

        if not signals:
            return {
                "has_active_signals": False,
                "advisory_level": "none",
                "active_signals": [],
            }

        # ----------------------------------
        # Determine advisory level (calm)
        # ----------------------------------

        advisory_level = "monitoring"

        for s in signals:
            if s.severity == "critical":
                advisory_level = "elevated"
                break
            if s.severity == "high":
                advisory_level = "significant"

        return {
            "has_active_signals": True,
            "advisory_level": advisory_level,
            "active_signals": [
                {
                    "signal_type": s.alert_type,
                    "severity": s.severity,
                    "summary": s.message,
                }
                for s in signals
            ],
        }
