"""Advisor-governed normalization of service history for Maintenance Intelligence.

Free-text service descriptions are never interpreted here. A service can only be
mapped to a stable maintenance item when that item already exists as applicable,
production-active verified Maintenance Knowledge for the same vehicle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from extensions import db
from maintenance.knowledge_service import MaintenanceKnowledgeService
from maintenance.models import (
    MAINTENANCE_SERVICE_CLASSIFICATION_SOURCES,
    MaintenanceKnowledgeRule,
    MaintenanceServiceClassification,
)
from maintenance.state_engine import _applicability, _rule_specificity
from models import Car, User, VehicleEvent


class MaintenanceServiceClassificationError(ValueError):
    """Base error for service-history maintenance normalization."""


class MaintenanceServiceClassificationAuthorityError(MaintenanceServiceClassificationError):
    """Raised when a non-advisor attempts professional classification."""


class MaintenanceServiceClassificationConflict(MaintenanceServiceClassificationError):
    """Raised when a service or maintenance key violates vehicle isolation."""


@dataclass(frozen=True)
class MaintenanceClassificationOption:
    maintenance_item_key: str
    display_name: str
    knowledge_rule_id: int
    source_name: str
    source_version: str | None


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _require_advisor(actor_user_id: int) -> User:
    actor = db.session.get(User, actor_user_id) if isinstance(actor_user_id, int) else None
    if actor is None or not actor.is_admin or not actor.is_active:
        raise MaintenanceServiceClassificationAuthorityError(
            "service maintenance classification requires advisor authority"
        )
    return actor


def _active_for_event(service_event_id: int) -> MaintenanceServiceClassification | None:
    return (
        MaintenanceServiceClassification.query.filter_by(
            service_event_id=service_event_id,
            status="active",
        )
        .order_by(MaintenanceServiceClassification.id.desc())
        .first()
    )


def _mirror_classification(
    *,
    event: VehicleEvent,
    classification: MaintenanceServiceClassification | None,
) -> None:
    data = dict(event.data) if isinstance(event.data, dict) else {}
    mirror_fields = {
        "maintenance_item_key",
        "maintenance_item_classification_id",
        "maintenance_item_classification_status",
        "maintenance_item_classification_source",
        "maintenance_item_classified_by",
        "maintenance_item_classified_at",
    }
    for field in mirror_fields:
        data.pop(field, None)

    if classification is not None:
        data.update(
            {
                "maintenance_item_key": classification.maintenance_item_key,
                "maintenance_item_classification_id": classification.id,
                "maintenance_item_classification_status": "advisor_verified",
                "maintenance_item_classification_source": classification.classification_source,
                "maintenance_item_classified_by": classification.classified_by,
                "maintenance_item_classified_at": classification.classified_at.isoformat(),
            }
        )
    event.data = data


class ServiceHistoryNormalizationService:
    """Single authority boundary for stable maintenance-item classification."""

    @staticmethod
    def classification_options(car: Car) -> tuple[MaintenanceClassificationOption, ...]:
        """Return the controlled vocabulary that is safe for this vehicle.

        Vocabulary comes only from applicable production-active verified
        Maintenance Knowledge. Missing/ambiguous vehicle identity is not widened
        into a guessed match.
        """

        grouped: dict[str, list[MaintenanceKnowledgeRule]] = {}
        for rule in MaintenanceKnowledgeService.production_active_query().all():
            applicability, _reasons = _applicability(rule, car)
            if applicability != "applicable":
                continue
            grouped.setdefault(rule.maintenance_item_key, []).append(rule)

        options: list[MaintenanceClassificationOption] = []
        for item_key in sorted(grouped):
            rules = grouped[item_key]
            max_specificity = max(_rule_specificity(rule) for rule in rules)
            most_specific = [
                rule for rule in rules if _rule_specificity(rule) == max_specificity
            ]
            reference = sorted(most_specific, key=lambda rule: rule.id)[0]
            display_name = sorted({rule.display_name for rule in most_specific})[0]
            options.append(
                MaintenanceClassificationOption(
                    maintenance_item_key=item_key,
                    display_name=display_name,
                    knowledge_rule_id=reference.id,
                    source_name=reference.source_name,
                    source_version=reference.source_version,
                )
            )
        return tuple(options)

    @staticmethod
    def resolve_option(*, car: Car, maintenance_item_key: str) -> MaintenanceClassificationOption:
        key = (maintenance_item_key or "").strip().lower()
        for option in ServiceHistoryNormalizationService.classification_options(car):
            if option.maintenance_item_key == key:
                return option
        raise MaintenanceServiceClassificationError(
            "maintenance item is not in this vehicle's advisor-verified controlled vocabulary"
        )

    @staticmethod
    def active_for_event(service_event_id: int) -> MaintenanceServiceClassification | None:
        return _active_for_event(service_event_id)

    @staticmethod
    def history_for_event(service_event_id: int) -> tuple[MaintenanceServiceClassification, ...]:
        return tuple(
            MaintenanceServiceClassification.query.filter_by(
                service_event_id=service_event_id,
            )
            .order_by(
                MaintenanceServiceClassification.classified_at.asc(),
                MaintenanceServiceClassification.id.asc(),
            )
            .all()
        )

    @staticmethod
    def classify(
        *,
        service_event_id: int,
        actor_user_id: int,
        maintenance_item_key: str,
        classification_source: str,
        classified_at: datetime | None = None,
    ) -> MaintenanceServiceClassification:
        _require_advisor(actor_user_id)
        if classification_source not in MAINTENANCE_SERVICE_CLASSIFICATION_SOURCES:
            raise MaintenanceServiceClassificationError("invalid classification source")

        event = db.session.get(VehicleEvent, service_event_id)
        if event is None or event.event_type != "service" or event.is_deleted:
            raise MaintenanceServiceClassificationError("service record does not exist")
        car = db.session.get(Car, event.car_id)
        if car is None:
            raise MaintenanceServiceClassificationConflict("service vehicle does not exist")

        option = ServiceHistoryNormalizationService.resolve_option(
            car=car,
            maintenance_item_key=maintenance_item_key,
        )
        current = _active_for_event(event.id)
        if current is not None and current.maintenance_item_key == option.maintenance_item_key:
            _mirror_classification(event=event, classification=current)
            db.session.flush()
            return current

        classified_at = classified_at or _utcnow_naive()
        if classified_at.tzinfo is not None:
            classified_at = classified_at.astimezone(timezone.utc).replace(tzinfo=None)

        if current is not None:
            current.status = "superseded"
            db.session.flush()

        classification = MaintenanceServiceClassification(
            service_event_id=event.id,
            car_id=event.car_id,
            maintenance_item_key=option.maintenance_item_key,
            knowledge_rule_id=option.knowledge_rule_id,
            status="active",
            classification_source=classification_source,
            classified_by=actor_user_id,
            classified_at=classified_at,
        )
        db.session.add(classification)
        db.session.flush()

        if current is not None:
            current.superseded_by_id = classification.id

        _mirror_classification(event=event, classification=classification)
        db.session.flush()
        return classification

    @staticmethod
    def clear(
        *,
        service_event_id: int,
        actor_user_id: int,
    ) -> MaintenanceServiceClassification | None:
        _require_advisor(actor_user_id)
        event = db.session.get(VehicleEvent, service_event_id)
        if event is None or event.event_type != "service" or event.is_deleted:
            raise MaintenanceServiceClassificationError("service record does not exist")

        current = _active_for_event(event.id)
        if current is None:
            _mirror_classification(event=event, classification=None)
            db.session.flush()
            return None

        current.status = "removed"
        _mirror_classification(event=event, classification=None)
        db.session.flush()
        return current
