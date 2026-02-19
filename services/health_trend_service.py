from typing import Dict
from statistics import mean
from datetime import datetime

from models import VehicleHealthSnapshot


class VehicleCareTrajectoryService:
    """
    Aura Vehicle Care Trajectory Engine

    Interprets historical health snapshots to understand
    how a vehicle’s condition is evolving over time.

    This system observes patterns only.
    It does not diagnose faults or predict failures.
    """

    @staticmethod
    def analyze_car_trajectory(car_id: int) -> Dict:
        snapshots = (
            VehicleHealthSnapshot.query.filter_by(car_id=car_id)
            .order_by(VehicleHealthSnapshot.created_at.asc())
            .all()
        )

        # ---------------------------------
        # INSUFFICIENT DATA — MONITORING
        # ---------------------------------

        if len(snapshots) < 2:
            return {
                "car_id": car_id,
                "trajectory": "monitoring_phase",
                "assessment_confidence": "low",
                "message": (
                    "There is currently insufficient historical data "
                    "to assess vehicle health trajectory."
                ),
                "snapshot_count": len(snapshots),
                "generated_at": datetime.utcnow().isoformat(),
            }

        scores = [s.health_score for s in snapshots]

        starting_score = scores[0]
        current_score = scores[-1]
        delta = current_score - starting_score
        average_score = mean(scores)

        # ---------------------------------
        # TRAJECTORY INTERPRETATION
        # ---------------------------------

        if delta >= 5:
            trajectory = "improving_stability"
        elif delta <= -5:
            trajectory = "declining_stability"
        else:
            trajectory = "stable_condition"

        # ---------------------------------
        # ACCELERATED DETERIORATION CHECK
        # ---------------------------------

        accelerated_deterioration = False
        if len(scores) >= 3:
            recent_scores = scores[-3:]
            if recent_scores[0] - recent_scores[-1] >= 15:
                accelerated_deterioration = True

        # ---------------------------------
        # ASSESSMENT CONFIDENCE
        # ---------------------------------

        if len(scores) >= 6:
            confidence = "high"
        elif len(scores) >= 4:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "car_id": car_id,
            "trajectory": trajectory,
            "assessment_confidence": confidence,
            "average_health_score": round(average_score, 1),
            "starting_health_score": starting_score,
            "current_health_score": current_score,
            "net_change": delta,
            "accelerated_deterioration": accelerated_deterioration,
            "snapshot_count": len(scores),
            "generated_at": datetime.utcnow().isoformat(),
        }
