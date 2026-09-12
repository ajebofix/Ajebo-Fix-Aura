"""Seed an explicitly synthetic Wave 2.5 longitudinal validation cohort.

This script is intended for an isolated validation database only. It creates
synthetic owners, vehicles, ownerships, reported concerns and canonical concern
events with deterministic historical timestamps.

Nothing produced by this script is real customer evidence. Every domain row and
canonical event is marked with the Wave 2.5 synthetic provenance marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402
from extensions import db  # noqa: E402
from models import Car, CarFault, CarOwnership, User  # noqa: E402
from services.event_emission import emit_vehicle_event  # noqa: E402
from services.reported_concern_session_events import _INTEGRATION_GUARD  # noqa: E402


SYNTHETIC_SOURCE = "synthetic_wave_2_5"
SYNTHETIC_DATASET = "wave_2_5_validation"
SYNTHETIC_PASSWORD = "Synthetic-Wave25-Validation-Only!"


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    owner_number: int
    brand: str
    model: str
    year: int
    vin: str
    plate: str
    category: str
    reported_at: datetime
    resolved_at: datetime
    reopened_at: datetime | None
    final_status: str


SCENARIOS = (
    Scenario(
        scenario_id="recurrence_positive_01",
        owner_number=1,
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2021,
        vin="W25SYNTHA00000001",
        plate="SYN-25-A1",
        category="suspension & steering",
        reported_at=datetime(2026, 4, 1, 9, 0, 0),
        resolved_at=datetime(2026, 4, 15, 14, 0, 0),
        reopened_at=datetime(2026, 5, 20, 10, 30, 0),
        final_status="reported",
    ),
    Scenario(
        scenario_id="recurrence_positive_02",
        owner_number=2,
        brand="Mercedes-Benz",
        model="E 350",
        year=2019,
        vin="W25SYNTHB00000002",
        plate="SYN-25-B2",
        category="cooling & HVAC",
        reported_at=datetime(2026, 4, 2, 11, 0, 0),
        resolved_at=datetime(2026, 4, 20, 16, 0, 0),
        reopened_at=datetime(2026, 7, 9, 8, 45, 0),
        final_status="reported",
    ),
    Scenario(
        scenario_id="non_recurrence_01",
        owner_number=3,
        brand="BMW",
        model="X5",
        year=2020,
        vin="W25SYNTHC00000003",
        plate="SYN-25-C3",
        category="electrical & electronics",
        reported_at=datetime(2026, 4, 10, 13, 0, 0),
        resolved_at=datetime(2026, 5, 1, 15, 30, 0),
        reopened_at=None,
        final_status="resolved",
    ),
    Scenario(
        scenario_id="censored_followup_01",
        owner_number=4,
        brand="Lexus",
        model="RX 350",
        year=2022,
        vin="W25SYNTHD00000004",
        plate="SYN-25-D4",
        category="brakes",
        reported_at=datetime(2026, 8, 20, 12, 0, 0),
        resolved_at=datetime(2026, 8, 25, 17, 0, 0),
        reopened_at=None,
        final_status="resolved",
    ),
)


def _get_or_create_user(*, name: str, email: str, phone: str, role: str) -> User:
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            name=name,
            email=email,
            phone_number=phone,
            role=role,
            is_active=True,
        )
        user.set_password(SYNTHETIC_PASSWORD)
        db.session.add(user)
        db.session.flush()
    return user


def _get_or_create_car(scenario: Scenario) -> Car:
    car = Car.query.filter_by(vin=scenario.vin).first()
    if car is None:
        car = Car(
            brand=scenario.brand,
            model=scenario.model,
            year=scenario.year,
            vin=scenario.vin,
            current_mileage=50000 + (scenario.owner_number * 2500),
            color="Synthetic validation",
            vehicle_identity_source="manual",
        )
        db.session.add(car)
        db.session.flush()
    return car


def _get_or_create_ownership(*, owner: User, car: Car, scenario: Scenario) -> CarOwnership:
    ownership = CarOwnership.query.filter_by(
        user_id=owner.id,
        car_id=car.id,
        is_active=True,
    ).first()
    if ownership is None:
        ownership = CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=scenario.plate,
            start_date=datetime(2026, 1, 1, 0, 0, 0),
            is_active=True,
            care_plan="active_monitoring",
        )
        db.session.add(ownership)
        db.session.flush()
    return ownership


def _get_or_create_concern(
    *,
    owner: User,
    advisor: User,
    car: Car,
    scenario: Scenario,
) -> CarFault:
    title = f"[SYNTHETIC] {scenario.scenario_id}"
    concern = CarFault.query.filter_by(
        car_id=car.id,
        title=title,
        source=SYNTHETIC_SOURCE,
    ).first()

    if concern is None:
        concern = CarFault(
            car_id=car.id,
            reported_by=owner.id,
            resolved_by=advisor.id,
            title=title,
            category=scenario.category,
            description=(
                "Synthetic Wave 2.5 validation concern. This is not a real customer "
                "report and must never be represented as production evidence."
            ),
            status=scenario.final_status,
            source=SYNTHETIC_SOURCE,
            observed_at=scenario.reported_at,
            reported_at=scenario.reported_at,
            created_at=scenario.reported_at,
            resolved_at=scenario.resolved_at,
        )
        db.session.add(concern)
        db.session.flush()
    else:
        concern.reported_by = owner.id
        concern.resolved_by = advisor.id
        concern.category = scenario.category
        concern.status = scenario.final_status
        concern.observed_at = scenario.reported_at
        concern.reported_at = scenario.reported_at
        concern.resolved_at = scenario.resolved_at
        db.session.flush()

    return concern


def _event_data(scenario: Scenario) -> dict[str, str]:
    return {
        "data_origin": "synthetic",
        "dataset": SYNTHETIC_DATASET,
        "scenario_id": scenario.scenario_id,
    }


def _emit_scenario_events(
    *,
    scenario: Scenario,
    owner: User,
    advisor: User,
    car: Car,
    concern: CarFault,
) -> None:
    shared = {
        "car_id": car.id,
        "subject_type": "reported_concern",
        "subject_id": concern.id,
        "visibility": "advisor",
        "source": SYNTHETIC_SOURCE,
        "evidence_refs": [{"type": "reported_concern", "id": concern.id}],
        "data": _event_data(scenario),
        "mileage": car.current_mileage,
    }

    emit_vehicle_event(
        **shared,
        event_type="concern.reported",
        actor_type="user",
        actor_user_id=owner.id,
        occurred_at=scenario.reported_at,
        title="Synthetic reported concern recorded",
        progression_direction="insufficient_evidence",
        idempotency_key=f"{SYNTHETIC_DATASET}:{scenario.scenario_id}:reported",
        previous_state=None,
        new_state="reported",
    )

    emit_vehicle_event(
        **shared,
        event_type="concern.resolved",
        actor_type="user",
        actor_user_id=advisor.id,
        occurred_at=scenario.resolved_at,
        title="Synthetic concern resolved",
        progression_direction="resolved",
        idempotency_key=f"{SYNTHETIC_DATASET}:{scenario.scenario_id}:resolved",
        previous_state="reported",
        new_state="resolved",
    )

    if scenario.reopened_at is not None:
        emit_vehicle_event(
            **shared,
            event_type="concern.reopened",
            actor_type="user",
            actor_user_id=owner.id,
            occurred_at=scenario.reopened_at,
            title="Synthetic concern reopened",
            progression_direction="recurring",
            idempotency_key=f"{SYNTHETIC_DATASET}:{scenario.scenario_id}:reopened",
            previous_state="resolved",
            new_state="reported",
        )


def seed_synthetic_cohort() -> dict[str, int]:
    """Create or idempotently reuse the deterministic synthetic cohort."""

    advisor = _get_or_create_user(
        name="Synthetic Wave 2.5 Advisor",
        email="wave25.advisor@synthetic.ajebofix.invalid",
        phone="+2347000250000",
        role="admin",
    )

    db.session.info[_INTEGRATION_GUARD] = True
    try:
        concern_ids: set[int] = set()
        car_ids: set[int] = set()
        owner_ids: set[int] = set()

        for scenario in SCENARIOS:
            owner = _get_or_create_user(
                name=f"Synthetic Wave 2.5 Owner {scenario.owner_number}",
                email=(
                    f"wave25.owner{scenario.owner_number}@synthetic.ajebofix.invalid"
                ),
                phone=f"+23470002500{scenario.owner_number:02d}",
                role="user",
            )
            car = _get_or_create_car(scenario)
            _get_or_create_ownership(owner=owner, car=car, scenario=scenario)
            concern = _get_or_create_concern(
                owner=owner,
                advisor=advisor,
                car=car,
                scenario=scenario,
            )
            _emit_scenario_events(
                scenario=scenario,
                owner=owner,
                advisor=advisor,
                car=car,
                concern=concern,
            )

            owner_ids.add(owner.id)
            car_ids.add(car.id)
            concern_ids.add(concern.id)

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    finally:
        db.session.info.pop(_INTEGRATION_GUARD, None)

    return {
        "synthetic_owners": len(owner_ids),
        "synthetic_vehicles": len(car_ids),
        "synthetic_concerns": len(concern_ids),
        "synthetic_scenarios": len(SCENARIOS),
    }


def main() -> int:
    with app.app_context():
        result = seed_synthetic_cohort()
        print("Wave 2.5 synthetic cohort seeded idempotently")
        for key, value in result.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
