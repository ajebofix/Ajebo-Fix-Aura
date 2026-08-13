"""PostgreSQL verification for Wave 1.3 Rina memory scope.

Run only against a disposable CI database after ``flask db upgrade``.
"""

from __future__ import annotations

from datetime import datetime

from app import create_app
from extensions import db
from models import (
    AdvisorNote,
    Car,
    CarDriver,
    CarOwnership,
    ConversationRecord,
    User,
)
from services.rina_authority import RinaVehicleAuthorityDenied
from services.rina_memory_service import (
    load_rina_advisor_memory,
    load_rina_chat_history,
    load_rina_summaries,
    save_rina_chat_turn,
)


PASSWORD = "Password123"


def make_user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Postgres Memory {suffix}",
        email=f"postgres-memory-{suffix}@example.com",
        phone_number=f"+2348119{suffix:05d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 13, 9, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def make_car(*, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2025,
        vin=f"W1NPGMEM{suffix:009d}",
        current_mileage=12000 + suffix,
        vehicle_identity_source="manual",
    )
    db.session.add(car)
    db.session.flush()
    return car


def own(*, owner: User, car: Car, suffix: int) -> None:
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"PM-{suffix:03d}-LA",
            mileage_at_transfer=car.current_mileage,
            is_active=True,
        )
    )
    db.session.flush()


def main() -> None:
    app = create_app()
    app.config.update(TESTING=True)

    with app.app_context():
        owner = make_user(suffix=1)
        outsider = make_user(suffix=2)
        driver = make_user(suffix=3, role="driver")
        administrator = make_user(suffix=4, role="admin")
        car_one = make_car(suffix=1)
        car_two = make_car(suffix=2)
        own(owner=owner, car=car_one, suffix=1)
        own(owner=owner, car=car_two, suffix=2)
        assignment = CarDriver(
            car_id=car_one.id,
            user_id=driver.id,
            is_active=True,
        )
        db.session.add(assignment)
        db.session.commit()

        save_rina_chat_turn(
            user_id=owner.id,
            car_id=car_one.id,
            conversation_id="postgres-car-one",
            role="user",
            content="Car one PostgreSQL memory.",
        )
        save_rina_chat_turn(
            user_id=owner.id,
            car_id=car_two.id,
            conversation_id="postgres-car-two",
            role="user",
            content="Car two PostgreSQL memory.",
        )

        client_record = ConversationRecord(
            user_id=owner.id,
            vehicle_id=car_one.id,
            concern="Raw concern must remain out of client memory output.",
            advisor_summary="POSTGRES INTERNAL ADVISOR SUMMARY",
            client_summary="Client-safe PostgreSQL continuity summary.",
            visibility="client",
            source="postgres-verifier",
            provenance="advisor",
            verification_state="advisor_verified",
            status="logged",
        )
        internal_record = ConversationRecord(
            user_id=owner.id,
            vehicle_id=car_one.id,
            concern="Internal raw concern.",
            advisor_summary="POSTGRES INTERNAL ONLY",
            client_summary=None,
            visibility="internal",
            source="postgres-verifier",
            provenance="advisor",
            verification_state="advisor_verified",
            status="logged",
        )
        driver_record = ConversationRecord(
            user_id=driver.id,
            vehicle_id=car_one.id,
            concern="Driver raw concern.",
            advisor_summary="Driver internal summary.",
            client_summary="Driver client-safe continuity summary.",
            visibility="client",
            source="postgres-verifier",
            provenance="rules",
            verification_state="unverified",
            status="logged",
        )
        db.session.add_all([client_record, internal_record, driver_record])
        db.session.flush()
        note = AdvisorNote(
            user_id=owner.id,
            car_id=car_one.id,
            advisor_id=administrator.id,
            note="POSTGRES ADVISOR ONLY NOTE",
        )
        db.session.add(note)
        db.session.commit()

        history = load_rina_chat_history(user_id=owner.id, car_id=car_one.id)
        assert [turn.content for turn in history] == ["Car one PostgreSQL memory."]
        assert all(turn.content != "Car two PostgreSQL memory." for turn in history)

        try:
            load_rina_chat_history(user_id=outsider.id, car_id=car_one.id)
        except RinaVehicleAuthorityDenied:
            pass
        else:
            raise AssertionError("unrelated user gained Rina memory access")

        owner_summaries = load_rina_summaries(user_id=owner.id, car_id=car_one.id)
        owner_payload = repr([item.to_dict() for item in owner_summaries])
        assert "Client-safe PostgreSQL continuity summary." in owner_payload
        assert "POSTGRES INTERNAL ADVISOR SUMMARY" not in owner_payload
        assert "POSTGRES INTERNAL ONLY" not in owner_payload
        assert "Raw concern" not in owner_payload

        driver_summaries = load_rina_summaries(user_id=driver.id, car_id=car_one.id)
        assert [item.summary for item in driver_summaries] == [
            "Driver client-safe continuity summary."
        ]

        try:
            load_rina_advisor_memory(user_id=owner.id, car_id=car_one.id)
        except RinaVehicleAuthorityDenied:
            pass
        else:
            raise AssertionError("owner gained advisor-only memory access")

        admin_summaries = load_rina_summaries(
            user_id=administrator.id,
            car_id=car_one.id,
        )
        assert {item.record_id for item in admin_summaries} == {
            client_record.id,
            internal_record.id,
            driver_record.id,
        }
        admin_notes = load_rina_advisor_memory(
            user_id=administrator.id,
            car_id=car_one.id,
        )
        assert [item.content for item in admin_notes] == ["POSTGRES ADVISOR ONLY NOTE"]

        assignment.is_active = False
        db.session.commit()
        try:
            load_rina_summaries(user_id=driver.id, car_id=car_one.id)
        except RinaVehicleAuthorityDenied:
            pass
        else:
            raise AssertionError("revoked driver retained Rina memory access")

        print("Wave 1.3 PostgreSQL Rina memory isolation verified.")


if __name__ == "__main__":
    main()
