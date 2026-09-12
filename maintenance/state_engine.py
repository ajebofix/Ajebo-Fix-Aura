"""Deterministic Maintenance Intelligence state evaluation.

This module is intentionally read-only.  It combines production-active verified
maintenance knowledge with vehicle identity, Aura's authoritative odometer
provenance, and explicitly classified service-history facts.  It never guesses
service completion from free text and never writes care state.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from maintenance.knowledge_service import MaintenanceKnowledgeService
from maintenance.models import MaintenanceKnowledgeRule
from models import Car, VehicleEvent
from services.mileage_observations import MileageObservationService, MileageSnapshot


DUE_WINDOW_KM = 1_000
DUE_WINDOW_DAYS = 30

TRUSTED_ODOMETER_VERIFICATION_STATUSES = frozenset(
    {
        "advisor_verified",
        "document_reviewed",
        "system_verified",
    }
)

_TRUSTED_HISTORICAL_SERVICE_VERIFICATIONS = frozenset(
    {
        "advisor_confirmed",
        "document_reviewed",
    }
)

_STATE_RANK = {
    "upcoming": 1,
    "due": 2,
    "overdue": 3,
}

_APPLICABILITY_FIELDS = (
    "brand",
    "model",
    "trim",
    "engine_type",
    "fuel_type",
    "transmission",
    "drive_type",
)


@dataclass(frozen=True)
class MaintenanceStateResult:
    maintenance_item_key: str
    display_name: str
    state: str
    rule_id: int | None
    rule_version: str | None
    rule_source: dict[str, Any]
    verification_status: str | None
    vehicle_identity_used: dict[str, Any]
    current_odometer_km: int | None
    odometer_provenance: dict[str, Any]
    latest_matching_service_event_id: int | None
    latest_matching_service_mileage: int | None
    latest_matching_service_date: str | None
    next_due_mileage: int | None
    next_due_date: str | None
    evaluated_at: str
    unknown_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["unknown_reasons"] = list(self.unknown_reasons)
        return payload


@dataclass(frozen=True)
class MaintenanceVehicleEvaluation:
    car_id: int
    evaluated_at: str
    results: tuple[MaintenanceStateResult, ...]
    unknown_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "car_id": self.car_id,
            "evaluated_at": self.evaluated_at,
            "results": [result.to_dict() for result in self.results],
            "state_counts": {
                state: sum(1 for result in self.results if result.state == state)
                for state in ("upcoming", "due", "overdue", "unknown")
            },
            "unknown_reasons": list(self.unknown_reasons),
        }


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalise_datetime(value: datetime | None) -> datetime:
    value = value or _utcnow_naive()
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _normalise_text(value: Any) -> str | None:
    if value is None:
        return None
    clean = " ".join(str(value).strip().split())
    return clean.casefold() if clean else None


def _add_months(value: date, months: int) -> date:
    """Add whole calendar months without turning month length into an estimate."""

    month_index = (value.year * 12 + value.month - 1) + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _vehicle_identity(car: Car) -> dict[str, Any]:
    profile = car.vehicle_profile
    return {
        "car_id": car.id,
        "brand": car.brand,
        "model": car.model,
        "year": car.year,
        "trim": profile.trim if profile else None,
        "engine_type": car.engine_type or (profile.engine_model if profile else None),
        "fuel_type": profile.fuel_type if profile else None,
        "transmission": car.transmission_type,
        "drive_type": profile.drive_type if profile else None,
        "identity_source": car.vehicle_identity_source or "unknown",
        "profile_source": profile.source if profile else None,
        "vin_decoded": bool(profile and profile.vin_decoded),
    }


def _applicability(rule: MaintenanceKnowledgeRule, car: Car) -> tuple[str, tuple[str, ...]]:
    """Return applicable, indeterminate, or mismatch with bounded reasons."""

    if rule.car_id is not None:
        if rule.car_id != car.id:
            return "mismatch", ("vehicle_override_mismatch",)
        return "applicable", ()

    identity = _vehicle_identity(car)
    reasons: list[str] = []

    # A broad rule with a different brand/model/year is not relevant to this
    # vehicle. Missing required identity is different: the rule might apply, so
    # the evaluator must abstain rather than silently discard it.
    for field in _APPLICABILITY_FIELDS:
        expected = getattr(rule, field)
        if expected is None:
            continue
        actual = identity.get(field)
        if actual is None:
            reasons.append(f"vehicle_identity_missing:{field}")
            continue
        if _normalise_text(actual) != _normalise_text(expected):
            return "mismatch", (f"vehicle_identity_mismatch:{field}",)

    if rule.year_start is not None or rule.year_end is not None:
        year = identity.get("year")
        if year is None:
            reasons.append("vehicle_identity_missing:year")
        else:
            if rule.year_start is not None and year < rule.year_start:
                return "mismatch", ("vehicle_identity_mismatch:year",)
            if rule.year_end is not None and year > rule.year_end:
                return "mismatch", ("vehicle_identity_mismatch:year",)

    return ("indeterminate", tuple(reasons)) if reasons else ("applicable", ())


def _rule_specificity(rule: MaintenanceKnowledgeRule) -> int:
    if rule.car_id is not None:
        return 10_000

    score = 0
    for field in _APPLICABILITY_FIELDS:
        if getattr(rule, field) is not None:
            score += 10
    if rule.year_start is not None or rule.year_end is not None:
        score += 10
    return score


def _odometer_evidence(snapshot: MileageSnapshot) -> dict[str, Any]:
    return {
        "observed_at": snapshot.observed_at.isoformat() if snapshot.observed_at else None,
        "source": snapshot.source,
        "source_label": snapshot.source_label,
        "verification_status": snapshot.verification_status,
        "verification_label": snapshot.verification_label,
        "freshness_status": snapshot.freshness_status,
        "freshness_label": snapshot.freshness_label,
        "age_days": snapshot.age_days,
        "provenance_available": snapshot.provenance_available,
    }


def _rule_source(rule: MaintenanceKnowledgeRule | None) -> dict[str, Any]:
    if rule is None:
        return {}
    return {
        "source_type": rule.source_type,
        "source_name": rule.source_name,
        "source_reference": rule.source_reference,
        "source_version": rule.source_version,
    }


def _service_has_matching_item(event: VehicleEvent, item_key: str) -> bool:
    data = event.data if isinstance(event.data, dict) else {}
    return data.get("maintenance_item_key") == item_key


def _service_is_trustworthy(event: VehicleEvent) -> bool:
    data = event.data if isinstance(event.data, dict) else {}
    verification = data.get("verification_status")
    record_mode = data.get("record_mode")
    entered_by_role = data.get("entered_by_role")

    if verification in _TRUSTED_HISTORICAL_SERVICE_VERIFICATIONS:
        return True
    if record_mode == "current" and entered_by_role == "advisor":
        return True
    if event.source == "admin_current":
        return True
    return False


def _matching_service_events(car_id: int, item_key: str) -> tuple[list[VehicleEvent], list[VehicleEvent]]:
    events = (
        VehicleEvent.query.filter(
            VehicleEvent.car_id == car_id,
            VehicleEvent.event_type == "service",
            VehicleEvent.is_deleted.is_(False),
        )
        .order_by(VehicleEvent.id.desc())
        .all()
    )
    explicit = [event for event in events if _service_has_matching_item(event, item_key)]
    trusted = [event for event in explicit if _service_is_trustworthy(event)]
    return explicit, trusted


def _select_latest_service(
    events: list[VehicleEvent],
    *,
    require_mileage: bool,
    require_date: bool,
) -> VehicleEvent | None:
    eligible: list[VehicleEvent] = []
    for event in events:
        if require_mileage and event.mileage is None:
            continue
        if require_date and event.occurred_at is None:
            continue
        eligible.append(event)

    if not eligible:
        return None

    if require_date:
        return max(
            eligible,
            key=lambda event: (event.occurred_at, event.id or 0),
        )
    return max(
        eligible,
        key=lambda event: (event.mileage if event.mileage is not None else -1, event.id or 0),
    )


def _classify_mileage(
    *,
    snapshot: MileageSnapshot,
    next_due_mileage: int,
) -> tuple[str | None, str | None]:
    current = snapshot.odometer_km
    if current is None:
        return None, "current_odometer_unavailable"
    if not snapshot.provenance_available:
        return None, "current_odometer_provenance_unavailable"
    if snapshot.verification_status not in TRUSTED_ODOMETER_VERIFICATION_STATUSES:
        return None, "current_odometer_not_verified"

    if current > next_due_mileage:
        # A trustworthy stale cumulative reading can still prove that a mileage
        # threshold has already been crossed; the odometer cannot move backward.
        return "overdue", None

    if snapshot.freshness_status == "stale":
        return None, "current_odometer_stale"

    if current >= next_due_mileage - DUE_WINDOW_KM:
        return "due", None
    return "upcoming", None


def _classify_date(*, evaluated_on: date, next_due_date: date) -> str:
    if evaluated_on > next_due_date:
        return "overdue"
    if evaluated_on >= next_due_date - timedelta(days=DUE_WINDOW_DAYS):
        return "due"
    return "upcoming"


def _combine_dimension_states(
    mileage_state: str | None,
    date_state: str | None,
    *,
    mileage_required: bool,
    date_required: bool,
) -> str:
    states = [state for state in (mileage_state, date_state) if state is not None]
    if "overdue" in states:
        return "overdue"
    if (mileage_required and mileage_state is None) or (date_required and date_state is None):
        return "unknown"
    if not states:
        return "unknown"
    return max(states, key=_STATE_RANK.__getitem__)


def _base_result(
    *,
    car: Car,
    rule: MaintenanceKnowledgeRule | None,
    item_key: str,
    display_name: str,
    state: str,
    snapshot: MileageSnapshot,
    evaluated_at: datetime,
    unknown_reasons: tuple[str, ...],
    service: VehicleEvent | None = None,
    next_due_mileage: int | None = None,
    next_due_date: date | None = None,
) -> MaintenanceStateResult:
    return MaintenanceStateResult(
        maintenance_item_key=item_key,
        display_name=display_name,
        state=state,
        rule_id=rule.id if rule else None,
        rule_version=rule.source_version if rule else None,
        rule_source=_rule_source(rule),
        verification_status=rule.verification_status if rule else None,
        vehicle_identity_used=_vehicle_identity(car),
        current_odometer_km=snapshot.odometer_km,
        odometer_provenance=_odometer_evidence(snapshot),
        latest_matching_service_event_id=service.id if service else None,
        latest_matching_service_mileage=service.mileage if service else None,
        latest_matching_service_date=(
            service.occurred_at.date().isoformat()
            if service is not None and service.occurred_at is not None
            else None
        ),
        next_due_mileage=next_due_mileage,
        next_due_date=next_due_date.isoformat() if next_due_date else None,
        evaluated_at=evaluated_at.isoformat(),
        unknown_reasons=unknown_reasons,
    )


class MaintenanceStateEngine:
    """Read-only deterministic evaluator for Aura Maintenance Intelligence."""

    @staticmethod
    def evaluate_rule(
        *,
        car: Car,
        rule: MaintenanceKnowledgeRule,
        evaluated_at: datetime | None = None,
    ) -> MaintenanceStateResult:
        evaluated_at = _normalise_datetime(evaluated_at)
        snapshot = MileageObservationService.snapshot(car)

        if not rule.is_production_active or rule.source_type == "test":
            return _base_result(
                car=car,
                rule=rule,
                item_key=rule.maintenance_item_key,
                display_name=rule.display_name,
                state="unknown",
                snapshot=snapshot,
                evaluated_at=evaluated_at,
                unknown_reasons=("rule_not_production_active",),
            )

        applicability, applicability_reasons = _applicability(rule, car)
        if applicability == "mismatch":
            return _base_result(
                car=car,
                rule=rule,
                item_key=rule.maintenance_item_key,
                display_name=rule.display_name,
                state="unknown",
                snapshot=snapshot,
                evaluated_at=evaluated_at,
                unknown_reasons=("rule_not_applicable", *applicability_reasons),
            )
        if applicability == "indeterminate":
            return _base_result(
                car=car,
                rule=rule,
                item_key=rule.maintenance_item_key,
                display_name=rule.display_name,
                state="unknown",
                snapshot=snapshot,
                evaluated_at=evaluated_at,
                unknown_reasons=applicability_reasons,
            )

        mileage_required = rule.interval_km is not None
        date_required = rule.interval_months is not None

        explicit_events, trusted_events = _matching_service_events(
            car.id,
            rule.maintenance_item_key,
        )
        if not explicit_events:
            return _base_result(
                car=car,
                rule=rule,
                item_key=rule.maintenance_item_key,
                display_name=rule.display_name,
                state="unknown",
                snapshot=snapshot,
                evaluated_at=evaluated_at,
                unknown_reasons=("no_matching_service_baseline",),
            )
        if not trusted_events:
            return _base_result(
                car=car,
                rule=rule,
                item_key=rule.maintenance_item_key,
                display_name=rule.display_name,
                state="unknown",
                snapshot=snapshot,
                evaluated_at=evaluated_at,
                unknown_reasons=("matching_service_baseline_unverified",),
            )

        service = _select_latest_service(
            trusted_events,
            require_mileage=mileage_required,
            require_date=date_required,
        )
        if service is None:
            reasons: list[str] = []
            if mileage_required and not any(event.mileage is not None for event in trusted_events):
                reasons.append("matching_service_mileage_unavailable")
            if date_required and not any(event.occurred_at is not None for event in trusted_events):
                reasons.append("matching_service_date_unavailable")
            if not reasons:
                reasons.append("complete_matching_service_baseline_unavailable")
            return _base_result(
                car=car,
                rule=rule,
                item_key=rule.maintenance_item_key,
                display_name=rule.display_name,
                state="unknown",
                snapshot=snapshot,
                evaluated_at=evaluated_at,
                unknown_reasons=tuple(reasons),
            )

        next_due_mileage: int | None = None
        next_due_date: date | None = None
        mileage_state: str | None = None
        date_state: str | None = None
        unknown_reasons: list[str] = []

        if mileage_required:
            if service.mileage is None:
                unknown_reasons.append("matching_service_mileage_unavailable")
            else:
                if snapshot.odometer_km is not None and service.mileage > snapshot.odometer_km:
                    unknown_reasons.append("service_mileage_exceeds_current_odometer")
                next_due_mileage = service.mileage + int(rule.interval_km)
                if "service_mileage_exceeds_current_odometer" not in unknown_reasons:
                    mileage_state, mileage_reason = _classify_mileage(
                        snapshot=snapshot,
                        next_due_mileage=next_due_mileage,
                    )
                    if mileage_reason:
                        unknown_reasons.append(mileage_reason)

        if date_required:
            if service.occurred_at is None:
                unknown_reasons.append("matching_service_date_unavailable")
            else:
                next_due_date = _add_months(
                    service.occurred_at.date(),
                    int(rule.interval_months),
                )
                date_state = _classify_date(
                    evaluated_on=evaluated_at.date(),
                    next_due_date=next_due_date,
                )

        state = _combine_dimension_states(
            mileage_state,
            date_state,
            mileage_required=mileage_required,
            date_required=date_required,
        )

        # If one dimension already proves overdue, the first-threshold contract
        # makes that result conclusive even when another dimension cannot be
        # evaluated. Otherwise uncertainty keeps the overall result unknown.
        result_unknown_reasons = tuple(unknown_reasons) if state == "unknown" else ()

        return _base_result(
            car=car,
            rule=rule,
            item_key=rule.maintenance_item_key,
            display_name=rule.display_name,
            state=state,
            snapshot=snapshot,
            evaluated_at=evaluated_at,
            unknown_reasons=result_unknown_reasons,
            service=service,
            next_due_mileage=next_due_mileage,
            next_due_date=next_due_date,
        )

    @staticmethod
    def evaluate_vehicle(
        *,
        car: Car,
        evaluated_at: datetime | None = None,
    ) -> MaintenanceVehicleEvaluation:
        evaluated_at = _normalise_datetime(evaluated_at)
        snapshot = MileageObservationService.snapshot(car)
        rules = MaintenanceKnowledgeService.production_active_query().all()

        candidates: dict[str, list[MaintenanceKnowledgeRule]] = {}
        for rule in rules:
            applicability, _reasons = _applicability(rule, car)
            if applicability == "mismatch":
                continue
            candidates.setdefault(rule.maintenance_item_key, []).append(rule)

        if not candidates:
            return MaintenanceVehicleEvaluation(
                car_id=car.id,
                evaluated_at=evaluated_at.isoformat(),
                results=(),
                unknown_reasons=("no_applicable_verified_maintenance_knowledge",),
            )

        results: list[MaintenanceStateResult] = []
        for item_key in sorted(candidates):
            item_rules = candidates[item_key]
            max_specificity = max(_rule_specificity(rule) for rule in item_rules)
            selected = [
                rule for rule in item_rules if _rule_specificity(rule) == max_specificity
            ]

            if len(selected) > 1:
                display_name = sorted(rule.display_name for rule in selected)[0]
                results.append(
                    _base_result(
                        car=car,
                        rule=None,
                        item_key=item_key,
                        display_name=display_name,
                        state="unknown",
                        snapshot=snapshot,
                        evaluated_at=evaluated_at,
                        unknown_reasons=(
                            "conflicting_equally_specific_verified_rules",
                            "conflicting_rule_ids:"
                            + ",".join(str(rule.id) for rule in sorted(selected, key=lambda r: r.id)),
                        ),
                    )
                )
                continue

            results.append(
                MaintenanceStateEngine.evaluate_rule(
                    car=car,
                    rule=selected[0],
                    evaluated_at=evaluated_at,
                )
            )

        return MaintenanceVehicleEvaluation(
            car_id=car.id,
            evaluated_at=evaluated_at.isoformat(),
            results=tuple(results),
        )
