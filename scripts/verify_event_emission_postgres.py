"""PostgreSQL verification for Aura's canonical event-emission contract.

This script is intended for the PostgreSQL CI service. It verifies two
production-critical behaviours that SQLite cannot authoritatively prove:

1. an emitted event remains in the caller's transaction and disappears on
   rollback; and
2. concurrent replay of the same idempotency key converges on one durable
   VehicleEvent row.
"""

from __future__ import annotations

from datetime import datetime
from threading import Barrier, Lock, Thread

from app import create_app
from extensions import db
from models import Car, CarOwnership, User, VehicleEvent
from services.event_emission import emit_vehicle_event


app = create_app()


def _create_user(*, name: str, email: str, phone: str) -> User:
    user = User(
        name=name,
        email=email,
        phone_number=phone,
        role="user",
        is_active=True,
    )
    user.set_password("CI-only-password")
    db.session.add(user)
    db.session.flush()
    return user


def _create_owned_car(owner: User, *, vin: str, plate: str) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="E 450 4MATIC",
        year=2024,
        vin=vin,
        current_mileage=15000,
    )
    db.session.add(car)
    db.session.flush()

    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=plate,
            mileage_at_transfer=15000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _event_kwargs(*, car_id: int, actor_user_id: int, subject_id: int, key: str) -> dict:
    return {
        "car_id": car_id,
        "event_type": "concern.reported",
        "subject_type": "reported_concern",
        "subject_id": subject_id,
        "actor_type": "user",
        "actor_user_id": actor_user_id,
        "visibility": "client",
        "source": "postgres_ci.event_emission",
        "occurred_at": datetime(2026, 8, 13, 9, 0, 0),
        "title": "Reported concern recorded",
        "progression_direction": "insufficient_evidence",
        "idempotency_key": key,
        "new_state": "reported",
        "evidence_refs": [{"type": "reported_concern", "id": subject_id}],
        "data": {"verified_by": "postgres_ci"},
    }


def verify_caller_owned_rollback() -> None:
    with app.app_context():
        owner = _create_user(
            name="Postgres Rollback Owner",
            email="event-postgres-rollback@example.com",
            phone="+2348000000101",
        )
        car = _create_owned_car(
            owner,
            vin="W1KCI000000000101",
            plate="EVT-101-LA",
        )

        event = emit_vehicle_event(
            **_event_kwargs(
                car_id=car.id,
                actor_user_id=owner.id,
                subject_id=900101,
                key="postgres-rollback-900101",
            )
        )
        event_id = event.id

        if event_id is None:
            raise SystemExit("PostgreSQL event flush did not allocate an event id")

        db.session.rollback()

        if VehicleEvent.query.filter_by(id=event_id).count() != 0:
            raise SystemExit(
                "Canonical event escaped the caller transaction on PostgreSQL"
            )

        print("PostgreSQL caller-owned rollback verified.")


def verify_concurrent_idempotency() -> None:
    with app.app_context():
        owner = _create_user(
            name="Postgres Concurrent Owner",
            email="event-postgres-concurrent@example.com",
            phone="+2348000000102",
        )
        car = _create_owned_car(
            owner,
            vin="W1KCI000000000102",
            plate="EVT-102-LA",
        )
        car_id = car.id
        owner_id = owner.id

    barrier = Barrier(2)
    lock = Lock()
    event_ids: list[int] = []
    errors: list[str] = []

    def worker() -> None:
        with app.app_context():
            try:
                barrier.wait(timeout=10)
                event = emit_vehicle_event(
                    **_event_kwargs(
                        car_id=car_id,
                        actor_user_id=owner_id,
                        subject_id=900102,
                        key="postgres-concurrent-900102",
                    )
                )
                event_id = event.id
                db.session.commit()
                if event_id is None:
                    raise RuntimeError("event id was not allocated")
                with lock:
                    event_ids.append(event_id)
            except Exception as exc:  # surfaced below with full type/message
                db.session.rollback()
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                db.session.remove()

    threads = [Thread(target=worker, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    if any(thread.is_alive() for thread in threads):
        raise SystemExit("Concurrent event-emission verification timed out")

    if errors:
        raise SystemExit(f"Concurrent event emission failed: {errors}")

    if len(event_ids) != 2 or len(set(event_ids)) != 1:
        raise SystemExit(
            f"Concurrent idempotency did not converge on one event id: {event_ids}"
        )

    with app.app_context():
        rows = VehicleEvent.query.filter_by(
            subject_type="reported_concern",
            subject_id=900102,
            event_type="concern.reported",
        ).all()
        if len(rows) != 1:
            raise SystemExit(
                f"Concurrent idempotency produced {len(rows)} durable event rows"
            )

    print("PostgreSQL concurrent idempotency verified.")


if __name__ == "__main__":
    verify_caller_owned_rollback()
    verify_concurrent_idempotency()
