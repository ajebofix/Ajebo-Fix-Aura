"""Read-only reevaluation hooks for Maintenance Intelligence.

Reevaluation deliberately creates no durable maintenance-state snapshot. It
recomputes the M3 projection from the currently accepted facts so M5 can later
consume the same deterministic result without creating a second source of truth.
"""

from __future__ import annotations

import logging

from flask import current_app, has_app_context

from extensions import db
from maintenance.models import MaintenanceKnowledgeRule
from maintenance.state_engine import MaintenanceStateEngine, _applicability
from models import Car


_logger = logging.getLogger(__name__)


def _log_info(message: str, *args) -> None:
    logger = current_app.logger if has_app_context() else _logger
    logger.info(message, *args)


def _log_exception(message: str, *args) -> None:
    logger = current_app.logger if has_app_context() else _logger
    logger.exception(message, *args)


class MaintenanceReevaluationService:
    """Recompute deterministic state after an accepted evidence change."""

    @staticmethod
    def evaluate_car(*, car_id: int, trigger: str):
        car = db.session.get(Car, car_id)
        if car is None:
            return None
        result = MaintenanceStateEngine.evaluate_vehicle(car=car)
        counts = result.to_dict().get("state_counts", {})
        _log_info(
            "Maintenance reevaluated car_id=%s trigger=%s states=%s",
            car_id,
            trigger,
            counts,
        )
        return result

    @staticmethod
    def safe_evaluate_car(*, car_id: int, trigger: str):
        """Best-effort reevaluation that never undoes an accepted source fact."""

        try:
            return MaintenanceReevaluationService.evaluate_car(
                car_id=car_id,
                trigger=trigger,
            )
        except Exception:
            _log_exception(
                "Maintenance reevaluation failed car_id=%s trigger=%s",
                car_id,
                trigger,
            )
            return None

    @staticmethod
    def safe_evaluate_rule_change(*, rule_id: int, trigger: str):
        """Reevaluate vehicles whose identity could be affected by a rule change."""

        rule = db.session.get(MaintenanceKnowledgeRule, rule_id)
        if rule is None:
            return ()

        results = []
        for car in Car.query.order_by(Car.id.asc()).all():
            applicability, _reasons = _applicability(rule, car)
            if applicability == "mismatch":
                continue
            result = MaintenanceReevaluationService.safe_evaluate_car(
                car_id=car.id,
                trigger=trigger,
            )
            if result is not None:
                results.append(result)
        return tuple(results)
