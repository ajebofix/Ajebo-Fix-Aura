from datetime import datetime

from models import (
    VehicleHealthSnapshot,
    VehicleHealthAlert,
    VehicleEvent,
    CarOwnership,
)


class RinaCareGuidanceEngine:
    """
    Provides calm, non-directive care guidance.
    Rina does NOT instruct.
    Rina does NOT diagnose.
    Rina frames observations for advisor-led decisions.
    """

    @staticmethod
    def generate_guidance(car_id: int, user_id: int) -> dict:
        """
        Returns a clinical-style usage advisory and care guidance.
        """

        ownership = CarOwnership.query.filter_by(
            car_id=car_id,
            user_id=user_id,
            is_active=True,
        ).first()

        if not ownership:
            return {
                "usage_advisory": "unknown",
                "summary": "No active ownership record is currently associated with this vehicle.",
                "guidance": [],
                "assessment_confidence": "low",
                "generated_at": datetime.utcnow().isoformat(),
            }

        # -----------------------------------------
        # Latest Health Assessment
        # -----------------------------------------

        snapshot = (
            VehicleHealthSnapshot.query.filter_by(
                car_id=car_id,
                ownership_id=ownership.id,
            )
            .order_by(VehicleHealthSnapshot.created_at.desc())
            .first()
        )

        # -----------------------------------------
        # Active Health Alerts
        # -----------------------------------------

        alerts = VehicleHealthAlert.query.filter_by(
            car_id=car_id,
            ownership_id=ownership.id,
            is_active=True,
        ).all()

        critical_alerts = [a for a in alerts if a.severity == "critical"]
        high_alerts = [a for a in alerts if a.severity == "high"]

        # -----------------------------------------
        # Recent High-Risk Records (Non-Diagnostic)
        # -----------------------------------------

        recent_high_risk_record = (
            VehicleEvent.query.filter_by(
                car_id=car_id,
                ownership_id=ownership.id,
                severity="critical",
                is_deleted=False,
            )
            .order_by(VehicleEvent.created_at.desc())
            .first()
        )

        # -----------------------------------------
        # GUIDANCE ENGINE (NON-COMMANDING)
        # -----------------------------------------

        # 🔴 HIGH RISK CONTEXT
        if critical_alerts or recent_high_risk_record:
            return {
                "usage_advisory": "restricted",
                "summary": (
                    "This vehicle is currently presenting active risks that require "
                    "professional review before extended use."
                ),
                "guidance": [
                    "Limit non-essential usage",
                    "Arrange a private vehicle assessment",
                    "Resolve active risks before resuming normal operation",
                ],
                "assessment_confidence": "very_high",
                "context": "active_critical_risk",
                "generated_at": datetime.utcnow().isoformat(),
            }

        # 🟠 ELEVATED RISK
        if (snapshot and snapshot.health_score < 60) or high_alerts:
            return {
                "usage_advisory": "caution",
                "summary": (
                    "The vehicle shows indicators that warrant closer attention. "
                    "Continued use should be conservative."
                ),
                "guidance": [
                    "Avoid prolonged or demanding usage",
                    "Monitor vehicle behavior closely",
                    "Schedule a vehicle assessment in the near term",
                ],
                "assessment_confidence": "high",
                "context": "elevated_risk",
                "generated_at": datetime.utcnow().isoformat(),
            }

        # 🟢 STABLE CONDITION
        if snapshot and snapshot.health_score >= 75:
            return {
                "usage_advisory": "normal",
                "summary": "The vehicle is currently assessed as stable.",
                "guidance": [
                    "Continue normal usage patterns",
                    "Maintain routine monitoring",
                    "Follow preventive care recommendations",
                ],
                "assessment_confidence": "high",
                "context": "stable_condition",
                "generated_at": datetime.utcnow().isoformat(),
            }

        # 🟡 LIMITED DATA / MONITORING
        return {
            "usage_advisory": "monitoring",
            "summary": (
                "Current information is limited. Ongoing observation is recommended "
                "to establish a clearer assessment."
            ),
            "guidance": [
                "Observe vehicle performance",
                "Log any new concerns",
                "Consider assessment if uncertainty persists",
            ],
            "assessment_confidence": "medium",
            "context": "insufficient_data",
            "generated_at": datetime.utcnow().isoformat(),
        }
