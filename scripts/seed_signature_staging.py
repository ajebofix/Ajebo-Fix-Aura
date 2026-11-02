"""Idempotent synthetic login fixtures for Aura's *isolated* Signature staging project.

This module is exclusive to the QA branch. It must never be imported by app
startup or run in any environment other than the hard-pinned staging project.
Credentials are read from Railway environment variables, never from source.
"""
from __future__ import annotations

import os
from datetime import datetime


STAGING_PROJECT_ID = "4acf602f-11e1-4da5-99e5-d0483ae56374"
STAGING_ENVIRONMENT_ID = "2a158e6f-e984-4092-8e4f-046a403c5752"

ACCOUNTS = (
    ("admin", "admin", "Aura Test Administrator", "staging.admin@example.com", "08000001111"),
    ("owner", "user", "Aura Test Vehicle Owner", "staging.owner@example.com", "08000001112"),
    ("driver", "driver", "Aura Test Driver", "staging.driver@example.com", "08000001113"),
    ("advisor", "advisor", "Aura Test Advisor", "staging.advisor@example.com", "08000001114"),
)


def _require_isolated_staging() -> None:
    guards = {
        "APP_ENV": "staging",
        "RAILWAY_PROJECT_ID": STAGING_PROJECT_ID,
        "RAILWAY_ENVIRONMENT_ID": STAGING_ENVIRONMENT_ID,
        "STAGING_SYNTHETIC_FIXTURES_ENABLED": "true",
    }
    mismatches = [
        key for key, expected in guards.items()
        if os.environ.get(key) != expected
    ]
    if mismatches:
        raise RuntimeError(
            "Synthetic fixture creation refused: wrong staging scope: "
            + ", ".join(mismatches)
        )

    for role, _, _, _, _ in ACCOUNTS:
        value = os.environ.get(f"STAGING_{role.upper()}_PASSWORD", "")
        if len(value) < 20:
            raise RuntimeError(
                f"Missing or too-short STAGING_{role.upper()}_PASSWORD"
            )


def main() -> None:
    _require_isolated_staging()

    from app import create_app
    from extensions import db
    from models import Car, CarDriver, CarOwnership, User

    app = create_app()
    with app.app_context():
        if db.engine.url.get_backend_name() != "postgresql":
            raise RuntimeError("Synthetic QA requires dedicated PostgreSQL")

        users = {}
        for role, database_role, name, email, phone in ACCOUNTS:
            account = User.query.filter_by(email=email).one_or_none()
            if account is None:
                account = User(
                    name=name, email=email, phone_number=phone,
                    role=database_role, is_active=True,
                )
                db.session.add(account)
            account.name = name
            account.phone_number = phone
            account.role = database_role
            account.is_active = True
            account.email_verified_at = datetime.utcnow()
            account.set_password(
                os.environ[f"STAGING_{role.upper()}_PASSWORD"]
            )
            db.session.flush()
            users[role] = account

        # Clearly fictional example vehicles: only synthetic facts and owner
        # authority are stored; no claims about diagnostics, repairs or billing.
        sample_vehicles = (
            ("AURASTAGE00000001", "S 450", 2021, 46000, "QA-001-LA"),
            ("AURASTAGE00000002", "GLE 450 4MATIC", 2022, 28000, "QA-002-LA"),
        )
        vehicles = []
        for vin, model, year, mileage, plate in sample_vehicles:
            car = Car.query.filter_by(vin=vin).one_or_none()
            if car is None:
                car = Car(
                    brand="Mercedes-Benz", model=model, year=year,
                    vin=vin, current_mileage=mileage,
                    vehicle_identity_source="manual",
                )
                db.session.add(car)
                db.session.flush()

            relationship = CarOwnership.query.filter_by(
                user_id=users["owner"].id, car_id=car.id,
            ).one_or_none()
            if relationship is None:
                db.session.add(CarOwnership(
                    user_id=users["owner"].id,
                    car_id=car.id,
                    plate_number=plate,
                    mileage_at_transfer=mileage,
                    is_active=True,
                ))
            vehicles.append(car)

        driver_link = CarDriver.query.filter_by(
            user_id=users["driver"].id, car_id=vehicles[0].id,
        ).one_or_none()
        if driver_link is None:
            db.session.add(CarDriver(
                user_id=users["driver"].id,
                car_id=vehicles[0].id,
                is_active=True,
            ))

        db.session.commit()
        print(
            "Synthetic Aura staging fixtures verified: "
            "4 role-specific accounts, 2 fictitious vehicles; "
            "no production account/data access."
        )


if __name__ == "__main__":
    main()
