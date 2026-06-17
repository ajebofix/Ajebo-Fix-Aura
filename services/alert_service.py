# services/alert_service.py

from datetime import datetime, timedelta

from models import (
    CarFault,
    Consultation,
    TreatmentPlan,
    VehicleHealthAlert,
)


class AlertService:

    @staticmethod
    def build_alert_center():

        alerts = []

        # =====================================================
        # ACTIVE VEHICLE ALERTS
        # =====================================================

        active_alerts = VehicleHealthAlert.query.filter_by(is_active=True).all()

        for alert in active_alerts:

            alerts.append(
                {
                    "id": alert.id,
                    "type": "vehicle_alert",
                    "severity": alert.severity,
                    "status": alert.status,
                    "title": alert.message,
                    "vehicle": alert.car,
                    "created_at": alert.created_at,
                }
            )

        # =====================================================
        # RECURRING CONCERNS
        # =====================================================

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
                        "severity": "high",
                        "title": (
                            f"{category.title()} concern repeated "
                            f"{data['count']} times in 14 days"
                        ),
                        "vehicle": latest_fault.car,
                        "created_at": latest_fault.created_at,
                    }
                )

        # =====================================================
        # OVERDUE CONSULTATIONS
        # =====================================================

        overdue_consultations = Consultation.query.filter(
            Consultation.status.in_(
                [
                    "requested",
                    "approved",
                    "in_progress",
                ]
            ),
            Consultation.created_at <= (datetime.utcnow() - timedelta(days=5)),
        ).all()

        for consultation in overdue_consultations:

            alerts.append(
                {
                    "id": None,
                    "status": "new",
                    "type": "consultation_delay",
                    "severity": "moderate",
                    "title": "Consultation remains unresolved",
                    "vehicle": consultation.car,
                    "created_at": consultation.created_at,
                }
            )

        # =====================================================
        # MONITORING STALLS
        # =====================================================

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
                    "severity": "moderate",
                    "title": "Monitoring state has not been reviewed recently",
                    "vehicle": treatment.car,
                    "created_at": treatment.updated_at,
                }
            )

        # =====================================================
        # SORT BY SEVERITY + TIME
        # =====================================================

        severity_order = {
            "critical": 4,
            "high": 3,
            "moderate": 2,
            "low": 1,
        }

        alerts.sort(
            key=lambda x: (severity_order.get(x["severity"], 0), x["created_at"]),
            reverse=True,
        )

        return alerts
