# services/rina_context_service.py

from datetime import datetime
from sqlalchemy.orm import joinedload

from models import (
    CarOwnership,
    CarDriver,
    ChatMessage,
    VehicleHealthAlert,
    VehicleHealthSnapshot,
    VehicleEvent,
    CarFault,
)

# OPTIONAL models (if exist in your project)
try:
    from models import Consultation, VehicleAssessment
except Exception:
    Consultation = None
    VehicleAssessment = None


class RinaContextService:
    """
    Central brain loader for Rina.
    Builds FULL awareness context.
    """

    # =========================
    # ROLE DETECTION
    # =========================
    @staticmethod
    def resolve_viewer_role(user, car_id=None):
        if getattr(user, "role", None) == "admin":
            return "admin"

        if car_id:
            is_driver = CarDriver.query.filter_by(
                user_id=user.id, car_id=car_id, is_active=True
            ).first()

            if is_driver:
                return "driver"

        return "owner"

    # =========================
    # MAIN BUILDER
    # =========================
    @staticmethod
    def build(user, message, active_car_id=None):

        # -------------------------
        # LOAD OWNERSHIPS
        # -------------------------
        ownerships = (
            CarOwnership.query.options(joinedload(CarOwnership.car))
            .filter(
                CarOwnership.user_id == user.id,
                CarOwnership.is_active.is_(True),
            )
            .all()
        )

        vehicles = []
        active_vehicle = None

        for o in ownerships:
            car = o.car
            identity = f"{car.brand} {car.model} {car.year}"

            vehicles.append(identity)

            if active_car_id and car.id == active_car_id:
                active_vehicle = car

        # fallback
        if not active_vehicle and ownerships:
            active_vehicle = ownerships[0].car

        # -------------------------
        # ROLE
        # -------------------------
        role = RinaContextService.resolve_viewer_role(
            user, active_vehicle.id if active_vehicle else None
        )

        if len(vehicles) == 1:
            context_vehicle = vehicles[0]
        else:
            context_vehicle = (
                f"{active_vehicle.brand} {active_vehicle.model} {active_vehicle.year}"
                if active_vehicle else None
            )

        # -------------------------
        # DRIVERS
        # -------------------------
        drivers = []
        if active_vehicle:
            assigned = CarDriver.query.filter_by(
                car_id=active_vehicle.id, is_active=True
            ).all()

            drivers = [
                {
                    "user_id": d.user_id,
                    "assigned_at": (
                        str(d.created_at) if hasattr(d, "created_at") else None
                    ),
                }
                for d in assigned
            ]

        # -------------------------
        # HEALTH + ALERTS
        # -------------------------
        alerts = []
        snapshot = None

        if active_vehicle:
            alerts_query = VehicleHealthAlert.query.filter_by(
                car_id=active_vehicle.id, is_active=True
            ).all()

            alerts = [
                {
                    "type": a.alert_type,
                    "severity": a.severity,
                    "message": a.message,
                }
                for a in alerts_query
            ]

            snapshot = (
                VehicleHealthSnapshot.query.filter_by(car_id=active_vehicle.id)
                .order_by(VehicleHealthSnapshot.created_at.desc())
                .first()
            )

        # -------------------------
        # EVENTS / HISTORY
        # -------------------------
        events = []
        if active_vehicle:
            event_q = (
                VehicleEvent.query.filter_by(car_id=active_vehicle.id)
                .order_by(VehicleEvent.created_at.desc())
                .limit(5)
                .all()
            )

            events = [
                {
                    "type": e.event_type,
                    "title": e.title,
                    "severity": e.severity,
                }
                for e in event_q
            ]

        # -------------------------
        # CONSULTATIONS
        # -------------------------
        consultations = {}

        if Consultation and active_vehicle:
            today = datetime.utcnow().date()

            consultations = {
                "today": Consultation.query.filter_by(car_id=active_vehicle.id).count(),
                "active": Consultation.query.filter_by(
                    car_id=active_vehicle.id, status="in_progress"
                ).count(),
                "completed": Consultation.query.filter_by(
                    car_id=active_vehicle.id, status="completed"
                ).count(),
            }

        # -------------------------
        # FAULTS
        # -------------------------
        faults = []

        if active_vehicle:
            fault_q = (
                CarFault.query.filter_by(car_id=active_vehicle.id)
                .order_by(CarFault.created_at.desc())
                .limit(5)
                .all()
            )

            faults = [
                {
                    "title": f.title,
                    "status": f.status,
                    "category": f.category,
                }
                for f in fault_q
            ]

        # -------------------------
        # ASSESSMENTS
        # -------------------------
        assessments = {}

        if VehicleAssessment and active_vehicle:
            assessments = {
                "draft": VehicleAssessment.query.filter_by(
                    car_id=active_vehicle.id, status="draft"
                ).count()
            }

        # -------------------------
        # ADMIN DASHBOARD
        # -------------------------
        admin_summary = {}

        if role == "admin" and Consultation:
            today = datetime.utcnow().date()

            admin_summary = {
                "consultations_today": Consultation.query.filter(
                    Consultation.created_at >= today
                ).count(),
                "active_consultations": Consultation.query.filter_by(
                    status="in_progress"
                ).count(),
                "completed_consultations": Consultation.query.filter_by(
                    status="completed"
                ).count(),
                "vehicles_requiring_attention": VehicleHealthAlert.query.filter_by(
                    severity="critical", is_active=True
                ).count(),
            }

        # -------------------------
        # CHAT HISTORY
        # -------------------------
        messages = (
            ChatMessage.query.filter_by(user_id=user.id)
            .order_by(ChatMessage.timestamp.desc())
            .limit(8)
            .all()
        )

        history = [{"role": m.role, "content": m.message} for m in reversed(messages)]


        # =========================
        # AWARENESS LAYER
        # =========================
        def _build_vehicle_story(faults, events, alerts):
            story = []

            for f in faults:
                if f["status"] == "reported":
                    story.append(
                        f"A concern was reported regarding {f['category']} ({f['title']})."
                    )
                elif f["status"] == "under_review":
                    story.append(
                        f"{f['category'].capitalize()} is currently under review."
                    )

            for e in events:
                story.append(f"Recent activity: {e['title']}.")

            for a in alerts:
                story.append(f"Active alert: {a['message']}.")

            return " ".join(story)

        context_story = _build_vehicle_story(faults, events, alerts)

        # =========================
        # FINAL CONTEXT
        # =========================
        return {
            "message": message,
            "user_name": getattr(user, "first_name", "there"),
            "role": role,
            "vehicles": vehicles,
            "vehicle_identity": context_vehicle,
            "vehicle_id": active_vehicle.id if active_vehicle else None,
            "vehicle_story": context_story,
            "drivers": drivers,
            "alerts": alerts,
            "events": events,
            "health_score": snapshot.health_score if snapshot else None,
            "consultations": consultations,
            "faults": faults,
            "assessments": assessments,
            "admin_summary": admin_summary,
            "history": history,
        }
