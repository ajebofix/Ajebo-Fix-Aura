# services/priority_scoring.py

from models import (
    CarFault,
    Consultation,
    TreatmentPlan,
)

from services.vehicle_intelligence import calculate_vehicle_health


class PriorityScoringEngine:

    @staticmethod
    def calculate(car, ownership):

        score = 0

        # -----------------------------------
        # ACTIVE CONCERNS
        # -----------------------------------

        active_concerns = CarFault.query.filter(
            CarFault.car_id == car.id,
            CarFault.status != "resolved",
        ).count()

        score += active_concerns * 10

        # ------------------------------------
        # ACTIVE CONSULTATION
        # ------------------------------------

        active_consultation = Consultation.query.filter(
            Consultation.car_id == car.id,
            Consultation.status == "in_progress",
        ).first()

        if active_consultation:
            score += 20

        # -------------------------------------
        # TREATMENT STATUS
        # -------------------------------------

        plan = (
            TreatmentPlan.query.filter_by(car_id=car.id)
            .order_by(TreatmentPlan.created_at.desc())
            .first()
        )

        if plan:

            if plan.status == "approved":
                score += 10

            elif plan.status == "in_progress":
                score += 15

            elif plan.status == "deferred":
                score += 25

        # -------------------------------
        # HEALTH STATUS
        # ---------------------------------

        health = calculate_vehicle_health(car, ownership)

        status = health.get("health_status")

        if status == "healthy":
            score += 0

        elif status == "attention":
            score += 20

        elif status == "critical":
            score += 40

        # -----------------------------------------
        # PRIORITY BAND
        # -----------------------------------

        score = min(score, 100)

        if score >= 80:
            band = "critical"

        elif score >= 60:
            band = "high"

        elif score >= 30:
            band = "moderate"

        else:
            band = "low"

        return {
            "score": min(score, 100),
            "band": band,
        }
