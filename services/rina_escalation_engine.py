# services/rina_escalation_engine.py

from typing import Dict


class RinaEscalationEngine:
    """
    Determines the appropriate level of attention for a vehicle.

    This does NOT instruct.
    This does NOT diagnose.

    It classifies:
    - monitor
    - flag
    - escalate
    """

    @staticmethod
    def evaluate(health: Dict, guidance: Dict, care_context: Dict) -> Dict:
        """
        Returns escalation state + reasoning.
        """

        score = health.get("health_score", 0)
        status = health.get("health_status")
        advisory = guidance.get("usage_advisory")
        has_signals = care_context.get("has_active_signals")
        advisory_level = care_context.get("advisory_level")

        # ---------------------------------------
        # 🔴 ESCALATE (high confidence)
        # ---------------------------------------
        if (
            status == "critical"
            or score < 50
            or advisory == "restricted"
            or advisory_level == "elevated"
        ):
            return {
                "level": "escalate",
                "label": "Professional review recommended",
                "message": (
                    "Current signals suggest that a professional assessment "
                    "would provide clarity and reduce risk exposure."
                ),
                "confidence": "high",
            }

        # ---------------------------------------
        # 🟡 FLAG (moderate concern)
        # ---------------------------------------
        if (
            status == "attention"
            or score < 75
            or advisory == "caution"
            or advisory_level == "significant"
            or has_signals
        ):
            return {
                "level": "flag",
                "label": "Closer attention advised",
                "message": (
                    "Some indicators suggest this vehicle would benefit "
                    "from closer monitoring or a scheduled review."
                ),
                "confidence": "medium",
            }

        # ---------------------------------------
        # 🟢 MONITOR (stable)
        # ---------------------------------------
        return {
            "level": "monitor",
            "label": "Stable",
            "message": (
                "No elevated signals are currently present. "
                "Routine monitoring remains appropriate."
            ),
            "confidence": "high",
        }
