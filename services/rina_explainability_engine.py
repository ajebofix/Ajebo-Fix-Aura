# services/rina_explainability_engine.py

# =====================================================
# RINA EXPLAINABILITY ENGINE — AURA CLINICAL MODE
# Explains observations + professional recommendations
# =====================================================

from typing import Dict, List

from models import Car
from services.vehicle_intelligence import VehicleIntelligenceService


class RinaExplainabilityEngine:
    """
    Aura Explainability Engine

    Purpose:
    - Translate vehicle intelligence into calm, human-readable context
    - Explain observations without diagnosing
    - Suggest professional next steps without urgency

    Read-only. No mutations.
    """

    # =====================================================
    # PUBLIC ENTRY POINT
    # =====================================================

    @staticmethod
    def explain_car(car: Car) -> Dict:
        """
        Returns a structured clinical-style explanation:
        - summary
        - observed factors
        - professional recommendations
        """

        intelligence = VehicleIntelligenceService.analyze_car(car)

        # ---------------------------------------------
        # NO ACTIVE OWNERSHIP
        # ---------------------------------------------
        if intelligence.get("health_status") == "unowned":
            return {
                "summary": "This vehicle is not currently under active care.",
                "observations": ["No ownership data is available"],
                "recommendations": [
                    "Assign an owner to enable ongoing vehicle health monitoring"
                ],
            }

        observations: List[str] = []
        recommendations: List[str] = []

        # ---------------------------------------------
        # INTERPRET INTELLIGENCE SIGNALS
        # ---------------------------------------------
        for issue in intelligence.get("issues", []):

            lowered = issue.lower()

            if "overdue service" in lowered:
                observations.append(issue)
                recommendations.append("A comprehensive vehicle assessment is advised")

            elif "fault" in lowered:
                observations.append(issue)
                recommendations.append(
                    "Further inspection is recommended to clarify this observation"
                )

            elif "risky driving" in lowered:
                observations.append(issue)
                recommendations.append(
                    "Driving patterns may benefit from professional review"
                )

            elif "predicted failure" in lowered:
                observations.append(issue)
                recommendations.append(
                    "Early inspection may help prevent avoidable disruption"
                )

            else:
                observations.append(issue)

        # ---------------------------------------------
        # FALLBACKS (CALM DEFAULTS)
        # ---------------------------------------------
        if not observations:
            observations.append("No elevated risk indicators are currently present")

        if not recommendations:
            recommendations.append(
                "Continue routine monitoring and scheduled maintenance"
            )

        return {
            "health_score": intelligence.get("health_score"),
            "health_status": intelligence.get("health_status"),
            "summary": "Vehicle health has been evaluated based on recorded data.",
            "observations": observations,
            "recommendations": recommendations,
        }
