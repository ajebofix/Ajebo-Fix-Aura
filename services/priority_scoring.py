"""Derived advisor-review scoring for Aura.

This module intentionally remains a read-time projection.  A score or band is
not a PriorityRequest, does not create canonical events, and must never be used
to accept/defer/resolve a durable priority workflow automatically.
"""

from models import CarFault, Consultation, TreatmentPlan
from services.vehicle_intelligence import calculate_vehicle_health


class PriorityScoringEngine:
    """Compute a bounded advisor-attention projection from durable facts.

    The class name is retained for compatibility with existing call sites, but
    callers should present the result as a *review projection*, not as the
    durable Priority Request queue introduced in Wave 2.4D.
    """

    CONSULTATION_WEIGHTS = {
        "requested": 10,
        "scheduled": 5,
        "in_progress": 20,
        "deferred": 15,
    }

    # Wave 2.3 canonical Treatment Plan states plus the explicitly supported
    # legacy ``approved`` compatibility value.  Terminal states intentionally
    # contribute no review pressure.
    TREATMENT_WEIGHTS = {
        "proposed": 5,
        "authorized": 10,
        "scheduled": 10,
        "in_progress": 15,
        "monitoring": 5,
        "deferred": 25,
        "approved": 10,
        "completed": 0,
        "cancelled": 0,
    }

    HEALTH_WEIGHTS = {
        "healthy": 0,
        "attention": 20,
        "critical": 40,
    }

    @staticmethod
    def calculate(car, ownership):
        score = 0
        reasons = []

        active_concerns = CarFault.query.filter(
            CarFault.car_id == car.id,
            CarFault.status != "resolved",
        ).count()
        if active_concerns:
            concern_points = min(active_concerns * 10, 30)
            score += concern_points
            reasons.append(
                {
                    "kind": "reported_concerns",
                    "count": active_concerns,
                    "points": concern_points,
                }
            )

        consultation = (
            Consultation.query.filter(
                Consultation.car_id == car.id,
                Consultation.status.in_(tuple(PriorityScoringEngine.CONSULTATION_WEIGHTS)),
            )
            .order_by(Consultation.created_at.desc(), Consultation.id.desc())
            .first()
        )
        if consultation is not None:
            consultation_points = PriorityScoringEngine.CONSULTATION_WEIGHTS.get(
                consultation.status,
                0,
            )
            score += consultation_points
            if consultation_points:
                reasons.append(
                    {
                        "kind": "consultation",
                        "state": consultation.status,
                        "points": consultation_points,
                    }
                )

        plan = (
            TreatmentPlan.query.filter_by(car_id=car.id)
            .order_by(TreatmentPlan.created_at.desc(), TreatmentPlan.id.desc())
            .first()
        )
        if plan is not None:
            treatment_points = PriorityScoringEngine.TREATMENT_WEIGHTS.get(
                plan.status,
                0,
            )
            score += treatment_points
            if treatment_points:
                reasons.append(
                    {
                        "kind": "treatment_plan",
                        "state": plan.status,
                        "points": treatment_points,
                    }
                )

        health = calculate_vehicle_health(car, ownership)
        health_status = health.get("health_status")
        health_points = PriorityScoringEngine.HEALTH_WEIGHTS.get(health_status, 0)
        score += health_points
        if health_points:
            reasons.append(
                {
                    "kind": "vehicle_health",
                    "state": health_status,
                    "points": health_points,
                }
            )

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
            "score": score,
            "band": band,
            "record_kind": "projection",
            "workflow": None,
            "reasons": reasons,
        }
