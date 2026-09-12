"""Seed and inspect one provenance-aware maintenance rule for Aura's demo GLE.

Branch-only smoke utility. It is deliberately narrow:
- only the known demo 2021 GLE 450 (car id 1 / known VIN);
- one car-specific rule from an official Mercedes-Benz USA source;
- no service classification, mileage change, service record, or alert mutation;
- idempotent through MaintenanceKnowledgeService fingerprinting.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from extensions import db
from maintenance.knowledge_service import MaintenanceKnowledgeService
from maintenance.service_history import ServiceHistoryNormalizationService
from maintenance.state_engine import MaintenanceStateEngine
from models import Car, User


EXPECTED_CAR_ID = 1
EXPECTED_VIN = "4JGFB5KB5MA477535"
SOURCE_URL = "https://www.mbusa.com/en/owners/service-maintenance/prepaid"


def _require_explicit_opt_in() -> None:
    if os.getenv("AURA_ALLOW_PRODUCTION_MAINTENANCE_SMOKE") != "1":
        raise RuntimeError(
            "Refusing to seed maintenance smoke knowledge without "
            "AURA_ALLOW_PRODUCTION_MAINTENANCE_SMOKE=1"
        )


def _active_advisor() -> User:
    for user in User.query.filter_by(is_active=True).order_by(User.id.asc()).all():
        if user.is_admin:
            return user
    raise RuntimeError("No active advisor/admin user is available for verification")


def main() -> None:
    _require_explicit_opt_in()

    with app.app_context():
        car = db.session.get(Car, EXPECTED_CAR_ID)
        if car is None:
            raise RuntimeError("Expected demo GLE car id 1 does not exist")
        if (car.vin or "").strip().upper() != EXPECTED_VIN:
            raise RuntimeError(
                "Refusing to seed: car id 1 is not the expected demo GLE VIN"
            )
        if car.year != 2021 or "GLE" not in (car.model or "").upper():
            raise RuntimeError(
                "Refusing to seed: car id 1 identity no longer matches the smoke fixture"
            )

        advisor = _active_advisor()

        rule = MaintenanceKnowledgeService.create_candidate(
            maintenance_item_key="scheduled_maintenance",
            display_name="Mercedes-Benz scheduled maintenance",
            interval_km=16093,
            interval_months=12,
            source_type="oem",
            source_name="Mercedes-Benz USA Service & Maintenance",
            source_reference=SOURCE_URL,
            source_version="10,000 mi/1 yr = 16,093 km; accessed 2026-09-12",
            car_id=car.id,
        )
        MaintenanceKnowledgeService.verify(
            rule_id=rule.id,
            actor_user_id=advisor.id,
            verification_status="advisor_verified",
        )
        db.session.commit()

        options = ServiceHistoryNormalizationService.classification_options(car)
        evaluation = MaintenanceStateEngine.evaluate_vehicle(car=car)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "car_id": car.id,
                    "vin": car.vin,
                    "rule": {
                        "rule_id": rule.id,
                        "maintenance_item_key": rule.maintenance_item_key,
                        "display_name": rule.display_name,
                        "interval_km": rule.interval_km,
                        "interval_months": rule.interval_months,
                        "source_type": rule.source_type,
                        "source_name": rule.source_name,
                        "source_reference": rule.source_reference,
                        "verification_status": rule.verification_status,
                        "verified_by": rule.verified_by,
                    },
                    "classification_options": [
                        {
                            "maintenance_item_key": option.maintenance_item_key,
                            "display_name": option.display_name,
                            "knowledge_rule_id": option.knowledge_rule_id,
                        }
                        for option in options
                    ],
                    "evaluation_before_service_link": evaluation.to_dict(),
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()

# Auditable branch-only smoke runner; intentionally not merged into production app code.
