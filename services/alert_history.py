"""Read-only presentation of saved care-signal occurrences and their events."""

from datetime import timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import joinedload

from extensions import db
from models import Car, VehicleEvent, VehicleHealthAlert
from services.care_signal_event_emission import CARE_SIGNAL_EVENT_TYPES


def alert_time(value):
    """Render Aura's UTC timestamps explicitly in the team's Lagos timezone."""
    if value is None:
        return "Not recorded"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo("Africa/Lagos")).strftime(
        "%d %b %Y, %I:%M:%S %p WAT"
    )


def _event_summary(event):
    data = event.data if isinstance(event.data, dict) else {}
    source = data.get("source_classification") or ""
    if event.actor_type == "system":
        actor = "System"
    elif event.actor_type == "user":
        actor = f"Advisor · {event.actor.name}" if event.actor else "Advisor"
    else:
        actor = "Not recorded"

    reason = "No further explanation was recorded."
    if event.event_type == "care_signal.resolved":
        if event.actor_type == "system" and source.startswith("maintenance_state:"):
            reason = "Maintenance reevaluation found no overdue items."
        elif event.actor_type == "system":
            reason = "The system cleared the monitoring condition."
        elif source == "advisor_resolution":
            reason = "An advisor resolved this alert."
    elif event.event_type == "care_signal.raised":
        reason = "A monitoring condition opened this alert."
    elif event.event_type == "care_signal.acknowledged":
        reason = "An advisor acknowledged this alert for review."

    return {
        "id": event.id,
        "event_type": event.event_type,
        "label": {
            "care_signal.raised": "Alert raised",
            "care_signal.acknowledged": "Alert acknowledged",
            "care_signal.resolved": "Alert resolved",
        }[event.event_type],
        "actor": actor,
        "actor_type": event.actor_type,
        "occurred_at": event.occurred_at,
        "recorded_at": event.recorded_at,
        "previous_state": event.previous_state,
        "new_state": event.new_state,
        "source_classification": source,
        "reason": reason,
    }


class AlertHistoryService:
    @staticmethod
    def resolved_page(*, page=1):
        pagination = db.paginate(
            db.select(VehicleHealthAlert)
            .where(
                VehicleHealthAlert.status == "resolved",
                VehicleHealthAlert.is_active.is_(False),
            )
            .order_by(
                VehicleHealthAlert.resolved_at.desc().nullslast(),
                VehicleHealthAlert.id.desc(),
            ),
            page=page,
            per_page=25,
            error_out=False,
        )
        cars = {
            car.id: car
            for car in Car.query.filter(
                Car.id.in_({signal.car_id for signal in pagination.items})
            ).all()
        }
        alerts = [
            {
                "id": signal.id,
                "type": "vehicle_alert",
                "signal_type": signal.alert_type,
                "record_kind": "care_signal",
                "actionable": False,
                "severity": signal.severity,
                "status": signal.status,
                "title": signal.message,
                "vehicle": cars[signal.car_id],
                "created_at": signal.created_at,
                "resolved_at": signal.resolved_at,
                # Current calculations must not masquerade as historical evidence.
                "maintenance_evidence": None,
            }
            for signal in pagination.items
        ]
        return alerts, pagination

    @staticmethod
    def detail(signal):
        events = (
            VehicleEvent.query.filter(
                VehicleEvent.subject_type == "vehicle_health_alert",
                VehicleEvent.subject_id == signal.id,
                VehicleEvent.car_id == signal.car_id,
                VehicleEvent.ownership_id == signal.ownership_id,
                VehicleEvent.event_type.in_(CARE_SIGNAL_EVENT_TYPES),
                VehicleEvent.is_deleted.is_(False),
            )
            .options(joinedload(VehicleEvent.actor))
            .order_by(VehicleEvent.occurred_at.asc(), VehicleEvent.id.asc())
            .all()
        )
        entries = [_event_summary(event) for event in events]
        resolution = next(
            (entry for entry in reversed(entries)
             if entry["event_type"] == "care_signal.resolved"),
            None,
        )
        return {
            "signal": signal,
            "vehicle": db.session.get(Car, signal.car_id),
            "entries": entries,
            "resolution": resolution,
        }
