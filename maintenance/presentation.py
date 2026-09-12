"""Bounded presentation projections for Aura Maintenance Intelligence M5."""

from __future__ import annotations

from maintenance.state_engine import MaintenanceStateEngine, MaintenanceStateResult


_CLIENT_COPY = {
    "upcoming": (
        "Upcoming",
        "This item remains ahead of its verified maintenance timing. Aura will continue monitoring it.",
    ),
    "due": (
        "Due",
        "This item has reached or is approaching its verified maintenance timing. It can be planned with your advisor.",
    ),
    "overdue": (
        "Overdue",
        "A verified maintenance threshold has passed. An advisor review is recommended to plan the next service step.",
    ),
    "unknown": (
        "Not yet established",
        "Aura does not yet have enough verified evidence to determine this maintenance item safely.",
    ),
}


def _client_item(result: MaintenanceStateResult) -> dict:
    label, guidance = _CLIENT_COPY[result.state]
    return {
        "display_name": result.display_name,
        "state": result.state,
        "state_label": label,
        "guidance": guidance,
        "next_due_mileage": result.next_due_mileage,
        "next_due_date": result.next_due_date,
    }


def _advisor_item(result: MaintenanceStateResult) -> dict:
    return {
        "maintenance_item_key": result.maintenance_item_key,
        "display_name": result.display_name,
        "state": result.state,
        "rule_id": result.rule_id,
        "rule_version": result.rule_version,
        "rule_source": dict(result.rule_source),
        "verification_status": result.verification_status,
        "vehicle_identity_used": dict(result.vehicle_identity_used),
        "current_odometer_km": result.current_odometer_km,
        "odometer_provenance": dict(result.odometer_provenance),
        "latest_matching_service_event_id": result.latest_matching_service_event_id,
        "latest_matching_service_mileage": result.latest_matching_service_mileage,
        "latest_matching_service_date": result.latest_matching_service_date,
        "next_due_mileage": result.next_due_mileage,
        "next_due_date": result.next_due_date,
        "evaluated_at": result.evaluated_at,
        "unknown_reasons": list(result.unknown_reasons),
    }


class MaintenancePresentationService:
    """Create role-bounded read projections from the single M3 evaluator."""

    @staticmethod
    def owner_view(car) -> dict:
        evaluation = MaintenanceStateEngine.evaluate_vehicle(car=car)
        items = [_client_item(result) for result in evaluation.results]
        return {
            "car_id": car.id,
            "items": items,
            "state_counts": evaluation.to_dict()["state_counts"],
            "has_verified_knowledge": bool(evaluation.results),
            "summary": (
                "Maintenance timing is based only on verified vehicle, odometer, schedule, and service-history evidence."
                if evaluation.results
                else "Aura does not yet have enough verified maintenance knowledge for this vehicle."
            ),
            "disclaimer": (
                "Maintenance monitoring is informational and non-diagnostic. It does not identify a fault or prescribe a repair."
            ),
        }

    @staticmethod
    def advisor_view(car) -> dict:
        evaluation = MaintenanceStateEngine.evaluate_vehicle(car=car)
        return {
            "car_id": car.id,
            "items": [_advisor_item(result) for result in evaluation.results],
            "state_counts": evaluation.to_dict()["state_counts"],
            "evaluation_unknown_reasons": list(evaluation.unknown_reasons),
            "evaluated_at": evaluation.evaluated_at,
        }

    @staticmethod
    def overdue_evidence(car) -> tuple[dict, ...]:
        view = MaintenancePresentationService.advisor_view(car)
        return tuple(item for item in view["items"] if item["state"] == "overdue")
