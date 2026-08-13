from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import Car, CarDriver, CarOwnership, User, VehicleEvent
from services.event_emission import (
    EventAuthorityError,
    EventEmissionError,
    EventIdempotencyConflict,
    emit_vehicle_event,
)


def _create_user(
    *,
    name: str,
    email: str,
    phone: str,
    role: str = "user",
) -> User:
    user = User(
        name=name,
        email=email,
        phone_number=phone,
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _create_owned_car(
    owner: User,
    *,
    suffix: str,
) -> tuple[Car, CarOwnership]:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2022,
        vin=f"W1N1671591A00{suffix.zfill(4)}",
        current_mileage=42000,
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"EVT-{suffix.zfill(3)}-LA",
        mileage_at_transfer=42000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return car, ownership


def _reported_event_kwargs(
    *,
    car: Car,
    actor: User,
    subject_id: int = 101,
    key: str = "concern-101-reported",
) -> dict:
    return {
        "car_id": car.id,
        "event_type": "concern.reported",
        "subject_type": "reported_concern",
        "subject_id": subject_id,
        "actor_type": "user",
        "actor_user_id": actor.id,
        "visibility": "client",
        "source": "tests.reported_concern",
        "occurred_at": datetime(2026, 8, 13, 8, 0, 0),
        "title": "Reported concern recorded",
        "progression_direction": "insufficient_evidence",
        "idempotency_key": key,
        "new_state": "reported",
        "evidence_refs": [{"type": "reported_concern", "id": subject_id}],
        "data": {"category": "observation"},
    }


def test_emit_vehicle_event_creates_canonical_owner_event_without_committing(app):
    with app.app_context():
        owner = _create_user(
            name="Owner One",
            email="event-owner-1@example.com",
            phone="08001000001",
        )
        car, ownership = _create_owned_car(owner, suffix="1")

        event = emit_vehicle_event(**_reported_event_kwargs(car=car, actor=owner))

        assert event.id is not None
        assert event.schema_version == 1
        assert event.ownership_id == ownership.id
        assert event.actor_authority == "owner"
        assert event.actor_user_id == owner.id
        assert event.created_by == owner.id
        assert event.visibility == "client"
        assert event.subject_type == "reported_concern"
        assert event.new_state == "reported"
        assert event.progression_direction == "insufficient_evidence"
        assert event.mileage is None
        assert event.event_date.isoformat() == "2026-08-13"

        event_id = event.id
        db.session.rollback()

        assert db.session.get(VehicleEvent, event_id) is None


def test_emit_vehicle_event_returns_existing_event_on_idempotent_replay(app):
    with app.app_context():
        owner = _create_user(
            name="Replay Owner",
            email="event-owner-2@example.com",
            phone="08001000002",
        )
        car, _ownership = _create_owned_car(owner, suffix="2")
        kwargs = _reported_event_kwargs(car=car, actor=owner, subject_id=102)

        first = emit_vehicle_event(**kwargs)
        second = emit_vehicle_event(**kwargs)

        assert second.id == first.id
        assert VehicleEvent.query.filter_by(fingerprint=first.fingerprint).count() == 1


def test_emit_vehicle_event_rejects_idempotency_key_with_changed_semantics(app):
    with app.app_context():
        owner = _create_user(
            name="Conflict Owner",
            email="event-owner-3@example.com",
            phone="08001000003",
        )
        car, _ownership = _create_owned_car(owner, suffix="3")
        kwargs = _reported_event_kwargs(car=car, actor=owner, subject_id=103)

        emit_vehicle_event(**kwargs)

        conflicting = dict(kwargs)
        conflicting["title"] = "Different semantic meaning"

        with pytest.raises(EventIdempotencyConflict):
            emit_vehicle_event(**conflicting)


def test_emit_vehicle_event_derives_driver_authority_from_assignment(app):
    with app.app_context():
        owner = _create_user(
            name="Driver Vehicle Owner",
            email="event-owner-4@example.com",
            phone="08001000004",
        )
        driver = _create_user(
            name="Assigned Driver",
            email="event-driver@example.com",
            phone="08001000005",
            role="driver",
        )
        car, ownership = _create_owned_car(owner, suffix="4")
        db.session.add(
            CarDriver(
                car_id=car.id,
                user_id=driver.id,
                is_active=True,
            )
        )
        db.session.commit()

        event = emit_vehicle_event(
            **_reported_event_kwargs(
                car=car,
                actor=driver,
                subject_id=104,
                key="concern-104-reported-by-driver",
            )
        )

        assert event.actor_authority == "driver"
        assert event.ownership_id == ownership.id


def test_emit_vehicle_event_derives_advisor_authority_from_current_admin_role(app):
    with app.app_context():
        owner = _create_user(
            name="Advisor Vehicle Owner",
            email="event-owner-5@example.com",
            phone="08001000006",
        )
        advisor = _create_user(
            name="Aura Advisor",
            email="event-advisor@example.com",
            phone="08001000007",
            role="admin",
        )
        car, _ownership = _create_owned_car(owner, suffix="5")

        event = emit_vehicle_event(
            **_reported_event_kwargs(
                car=car,
                actor=advisor,
                subject_id=105,
                key="concern-105-reported-by-advisor",
            )
        )

        assert event.actor_authority == "advisor"


def test_emit_vehicle_event_rejects_actor_without_vehicle_authority(app):
    with app.app_context():
        owner = _create_user(
            name="Real Owner",
            email="event-owner-6@example.com",
            phone="08001000008",
        )
        outsider = _create_user(
            name="Unrelated User",
            email="event-outsider@example.com",
            phone="08001000009",
        )
        car, _ownership = _create_owned_car(owner, suffix="6")

        with pytest.raises(EventAuthorityError):
            emit_vehicle_event(
                **_reported_event_kwargs(
                    car=car,
                    actor=outsider,
                    subject_id=106,
                    key="concern-106-outsider",
                )
            )


def test_emit_vehicle_event_requires_explicit_valid_visibility(app):
    with app.app_context():
        owner = _create_user(
            name="Visibility Owner",
            email="event-owner-7@example.com",
            phone="08001000010",
        )
        car, _ownership = _create_owned_car(owner, suffix="7")
        kwargs = _reported_event_kwargs(car=car, actor=owner, subject_id=107)
        kwargs["visibility"] = "public"

        with pytest.raises(EventEmissionError, match="visibility"):
            emit_vehicle_event(**kwargs)


def test_emit_vehicle_event_rejects_sensitive_payload_keys(app):
    with app.app_context():
        owner = _create_user(
            name="Privacy Owner",
            email="event-owner-8@example.com",
            phone="08001000011",
        )
        car, _ownership = _create_owned_car(owner, suffix="8")
        kwargs = _reported_event_kwargs(car=car, actor=owner, subject_id=108)
        kwargs["data"] = {"provider": {"access_token": "must-not-be-recorded"}}

        with pytest.raises(EventEmissionError, match="prohibited sensitive key"):
            emit_vehicle_event(**kwargs)


def test_emit_vehicle_event_enforces_transition_evidence_contract(app):
    with app.app_context():
        owner = _create_user(
            name="Transition Owner",
            email="event-owner-9@example.com",
            phone="08001000012",
        )
        car, _ownership = _create_owned_car(owner, suffix="9")

        with pytest.raises(EventEmissionError, match="previous_state and new_state"):
            emit_vehicle_event(
                car_id=car.id,
                event_type="concern.resolved",
                subject_type="reported_concern",
                subject_id=109,
                actor_type="user",
                actor_user_id=owner.id,
                visibility="client",
                source="tests.reported_concern",
                occurred_at=datetime(2026, 8, 13, 9, 0, 0),
                title="Concern resolved",
                progression_direction="resolved",
                idempotency_key="concern-109-resolved",
            )


def test_emit_vehicle_event_correction_is_additive_and_vehicle_scoped(app):
    with app.app_context():
        owner = _create_user(
            name="Correction Owner",
            email="event-owner-10@example.com",
            phone="08001000013",
        )
        car, _ownership = _create_owned_car(owner, suffix="10")
        original = emit_vehicle_event(
            **_reported_event_kwargs(car=car, actor=owner, subject_id=110)
        )

        correction = emit_vehicle_event(
            car_id=car.id,
            event_type="concern.corrected",
            subject_type="reported_concern",
            subject_id=110,
            actor_type="user",
            actor_user_id=owner.id,
            visibility="advisor",
            source="tests.reported_concern",
            occurred_at=datetime(2026, 8, 13, 10, 0, 0),
            title="Concern event corrected",
            progression_direction="not_applicable",
            idempotency_key="concern-110-correction-1",
            correction_of_event_id=original.id,
            evidence_refs=[{"type": "vehicle_event", "id": original.id}],
        )

        assert correction.id != original.id
        assert correction.correction_of_event_id == original.id
        assert original.is_deleted is False


def test_emit_vehicle_event_rejects_reserved_system_actor_until_schema_followup(app):
    with app.app_context():
        owner = _create_user(
            name="System Vehicle Owner",
            email="event-owner-11@example.com",
            phone="08001000014",
        )
        car, _ownership = _create_owned_car(owner, suffix="11")
        kwargs = _reported_event_kwargs(car=car, actor=owner, subject_id=111)
        kwargs["actor_type"] = "system"
        kwargs["actor_user_id"] = None

        with pytest.raises(EventEmissionError, match="remain reserved"):
            emit_vehicle_event(**kwargs)
