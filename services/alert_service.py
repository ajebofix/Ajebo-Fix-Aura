"""Advisor Alert Center aggregation.

The Alert Center deliberately combines one durable workflow family
(``VehicleHealthAlert`` care signals) with read-time operational projections.
Projection rows are never canonical events merely because they appear here.
Durable PriorityRequest workflow is intentionally kept in its own queue.
"""

from datetime import datetime, timedelta

from maintenance.presentation import MaintenancePresentationService
from models import Car, CarFault, Consultation, TreatmentPlan, VehicleHealthAlert


class AlertService:
    @staticmethod
    def build_alert_center():
        alerts = []

        # Durable care-signal occurrences.
        active_alerts = VehicleHealthAlert.query.filter_by(is_active=True).all()
        for alert in active_alerts:
            vehicle = Car.query.filter_by(id=alert.car_id).one()
            maintenance_evidence = None
            if alert.alert_type == "maintenance_monitoring":
                advisor_view = MaintenancePresentationService.advisor_view(vehicle)
                maintenance_evidence = {
                    "state_counts": advisor_view["state_counts"],
                    "overdue_items": [
                        item for item in advisor_view["items"] if item["state"] == "overdue"
                    ],
                    "evaluated_at": advisor_view["evaluated_at"],
                    "evaluation_unknown_reasons": advisor_view[
                        "evaluation_unknown_reasons"
                    ],
                }

            alerts.append(
                {
                    "id": alert.id,
                    "type": "vehicle_alert",
                    "signal_type": alert.alert_type,
                    "record_kind": "care_signal",
                    "projection_source": None,
                    "actionable": True,
                    "severity": alert.severity,
                    "status": alert.status,
                    "title": alert.message,
                    "vehicle": vehicle,
                    "created_at": alert.created_at,
                    "maintenance_evidence": maintenance_evidence,
                }
            )

        # Read-only recurring-concern projection.
        recent_window = datetime.utcnow() - timedelta(days=14)
        recent_faults = (
            CarFault.query.filter(
                CarFault.status != "resolved",
                CarFault.created_at >= recent_window,
            )
            .order_by(CarFault.created_at.desc())
            .all()
        )

        grouped_faults = {}
        for fault in recent_faults:
            key = (fault.car_id, fault.category)
            if key not in grouped_faults:
                grouped_faults[key] = {
                    "count": 0,
                    "latest_fault": fault,
                }
            grouped_faults[key]["count"] += 1

        for (_, category), data in grouped_faults.items():
            if data["count"] >= 3:
                latest_fault = data["latest_fault"]
                alerts.append(
                    {
                        "id": None,
                        "status": "new",
                        "type": "recurring_concern",
                        "signal_type": None,
                        "record_kind": "projection",
                        "projection_source": "reported_concern",
                        "actionable": False,
                        "severity": "high",
                        "title": (
                            f"{category.title()} concern repeated "
                            f"{data['count']} times in 14 days"
                        ),
                        "vehicle": latest_fault.car,
                        "created_at": latest_fault.created_at,
                        "maintenance_evidence": None,
                    }
                )

        # Read-only unresolved-consultation projection.  Wave 2.4E removes the
        # obsolete ``approved`` assumption and follows the current Consultation
        # lifecycle: requested, scheduled, in_progress, deferred.
        overdue_consultations = Consultation.query.filter(
            Consultation.status.in_(
                ("requested", "scheduled", "in_progress", "deferred")
            ),
            Consultation.created_at <= (datetime.utcnow() - timedelta(days=5)),
        ).all()

        for consultation in overdue_consultations:
            alerts.append(
                {
                    "id": None,
                    "status": "new",
                    "type": "consultation_delay",
                    "signal_type": None,
                    "record_kind": "projection",
                    "projection_source": "consultation",
                    "actionable": False,
                    "severity": "moderate",
                    "title": "Consultation remains unresolved",
                    "vehicle": consultation.car,
                    "created_at": consultation.created_at,
                    "maintenance_evidence": None,
                }
            )

        # Read-only treatment-monitoring staleness projection.
        stalled_treatments = TreatmentPlan.query.filter(
            TreatmentPlan.status == "monitoring",
            TreatmentPlan.updated_at <= (datetime.utcnow() - timedelta(days=14)),
        ).all()

        for treatment in stalled_treatments:
            alerts.append(
                {
                    "id": None,
                    "status": "new",
                    "type": "monitoring_stall",
                    "signal_type": None,
                    "record_kind": "projection",
                    "projection_source": "treatment_plan",
                    "actionable": False,
                    "severity": "moderate",
                    "title": "Monitoring state has not been reviewed recently",
                    "vehicle": treatment.car,
                    "created_at": treatment.updated_at,
                    "maintenance_evidence": None,
                }
            )

        severity_order = {
            "critical": 4,
            "high": 3,
            "moderate": 2,
            "low": 1,
        }
        alerts.sort(
            key=lambda item: (
                severity_order.get(item["severity"], 0),
                item["created_at"],
            ),
            reverse=True,
        )
        return alerts
