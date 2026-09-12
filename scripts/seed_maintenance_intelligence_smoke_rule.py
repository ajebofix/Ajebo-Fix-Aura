"""Seed one real, provenance-aware maintenance rule for Aura's synthetic GLE smoke test.

This script is intentionally narrow:
- it only permits the known synthetic/demo 2021 GLE 450 (car id 1 / known VIN);
- it creates one car-specific rule from an official Mercedes-Benz USA maintenance source;
- it does not classify service history, change mileage, record service work, or create alerts;
- it is idempotent through MaintenanceKnowledgeService fingerprinting.

The rule represents the aggregate routine scheduled-maintenance cadence documented by
Mercedes-Benz USA as every 10,000 miles or 1 year. Aura stores distance in km, so
10,000 miles is rounded to 16,093 km for the deterministic test rule.
"""

from __future__ import annotations

import json
import os

from app import app
from extensions import db
from maintenance.knowledge_service import MaintenanceKnowledgeService
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
            raise RuntimeError("Expected synthetic GLE car id 1 does not exist")
        if (car.vin or "").strip().upper() != EXPECTED_VIN:
            raise RuntimeError(
                "Refusing to seed: car id 1 is not the expected synthetic GLE VIN"
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

        print(
            json.dumps(
                {
                    "status": "ok",
                    "car_id": car.id,
                    "vin": car.vin,
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
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()

# Maintains an auditable branch-only smoke runner; not merged into production app code.
