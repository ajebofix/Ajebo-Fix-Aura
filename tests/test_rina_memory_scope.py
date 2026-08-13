from __future__ import annotations

from datetime import datetime

import pytest

import rina.memory_model_extensions  # noqa: F401
from extensions import db
from models import (
    AdvisorNote,
    Car,
    CarDriver,
    CarOwnership,
    ChatMessage,
    ConversationRecord,
    User,
)
from services.conversation_logger import log_conversation_record
from services.rina_authority import RinaVehicleAuthorityDenied
from services.rina_memory_service import (
    RinaMemoryPolicyError,
    load_rina_advisor_memory,
    load_rina_chat_history,
    load_rina_memory_bundle,
    load_rina_summaries,
    save_rina_chat_turn,
)


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Memory User {suffix}",
        email=f"memory-user-{suffix}@example.com",
        phone_number=f"0800610{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 13, 8, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _car(*, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLS 450 4MATIC",
        year=2025,
        vin=f"W1NRINAMEM{suffix:008d}",
        current_mileage=18000 + suffix,
        vehicle_identity_source="manual",
    )
    db.session.add(car)
    db.session.flush()
    return car


def _own(*, owner: User, car: Car, suffix: int) -> CarOwnership:
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"RM-{suffix:03d}-LA",
        mileage_at_transfer=car.current_mileage,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.flush()
    return ownership


def _client_record(
    *,
    user: User,
    car: Car,
    client_summary: str,
    advisor_summary: str,
    visibility: str = "client",
) -> ConversationRecord:
    row = ConversationRecord(
        user_id=user.id,
        vehicle_id=car.id,
        concern="Raw concern text that is not client memory output.",
        advisor_summary=advisor_summary,
        client_summary=client_summary,
        visibility=visibility,
        source="tests",
        provenance="advisor",
        verification_state="advisor_verified",
        status="logged",
        created_at=datetime(2026, 8, 13, 9, 0, 0),
    )
    db.session.add(row)
    db.session.flush()
    return row


def test_chat_history_is_scoped_by_user_vehicle_and_conversation(app):
    with app.app_context():
        owner = _user(suffix=1)
        car_one = _car(suffix=1)
        car_two = _car(suffix=2)
        _own(owner=owner, car=car_one, suffix=1)
        _own(owner=owner, car=car_two, suffix=2)
        db.session.commit()

        save_rina_chat_turn(
            user_id=owner.id,
            car_id=car_one.id,
            conversation_id="car-one-a",
            role="user",
            content="Question for car one.",
        )
        save_rina_chat_turn(
            user_id=owner.id,
            car_id=car_one.id,
            conversation_id="car-one-b",
            role="assistant",
            content="Second conversation on car one.",
        )
        save_rina_chat_turn(
            user_id=owner.id,
            car_id=car_two.id,
            conversation_id="car-two-a",
            role="user",
            content="Question for car two.",
        )
        db.session.commit()

        car_one_history = load_rina_chat_history(
            user_id=owner.id,
            car_id=car_one.id,
        )
        assert [item.content for item in car_one_history] == [
            "Question for car one.",
            "Second conversation on car one.",
        ]
        assert all(item.content != "Question for car two." for item in car_one_history)

        conversation_only = load_rina_chat_history(
            user_id=owner.id,
            car_id=car_one.id,
            conversation_id="car-one-a",
        )
        assert [item.content for item in conversation_only] == ["Question for car one."]


def test_unscoped_legacy_chat_is_never_loaded_as_vehicle_memory(app):
    with app.app_context():
        owner = _user(suffix=3)
        car = _car(suffix=3)
        _own(owner=owner, car=car, suffix=3)
        legacy = ChatMessage(
            user_id=owner.id,
            car_id=None,
            conversation_id=None,
            role="user",
            message="Legacy user-only history must not be guessed onto a car.",
            channel="legacy",
            visibility="client",
            timestamp=datetime(2026, 8, 13, 7, 0, 0),
        )
        db.session.add(legacy)
        db.session.commit()

        assert load_rina_chat_history(user_id=owner.id, car_id=car.id) == ()


def test_client_summary_never_falls_back_to_advisor_summary_or_raw_concern(app):
    with app.app_context():
        owner = _user(suffix=4)
        administrator = _user(suffix=5, role="admin")
        car = _car(suffix=4)
        _own(owner=owner, car=car, suffix=4)
        client_row = _client_record(
            user=owner,
            car=car,
            client_summary="The concern is recorded for continued monitoring.",
            advisor_summary="INTERNAL: validate escalation assumptions with advisor.",
        )
        internal_row = _client_record(
            user=owner,
            car=car,
            client_summary="This text remains hidden because the row is internal.",
            advisor_summary="INTERNAL ONLY: professional deliberation.",
            visibility="internal",
        )
        db.session.commit()

        owner_memory = load_rina_summaries(user_id=owner.id, car_id=car.id)
        assert len(owner_memory) == 1
        assert owner_memory[0].record_id == client_row.id
        assert owner_memory[0].summary == (
            "The concern is recorded for continued monitoring."
        )
        assert owner_memory[0].concern is None
        owner_payload = repr([item.to_dict() for item in owner_memory])
        assert "validate escalation assumptions" not in owner_payload
        assert "Raw concern text" not in owner_payload
        assert str(internal_row.id) not in [str(item.record_id) for item in owner_memory]

        admin_memory = load_rina_summaries(
            user_id=administrator.id,
            car_id=car.id,
        )
        assert {item.record_id for item in admin_memory} == {
            client_row.id,
            internal_row.id,
        }
        assert any(
            item.summary == "INTERNAL ONLY: professional deliberation."
            for item in admin_memory
        )


def test_client_visible_row_without_client_summary_is_not_client_memory(app):
    with app.app_context():
        owner = _user(suffix=6)
        car = _car(suffix=6)
        _own(owner=owner, car=car, suffix=6)
        db.session.add(
            ConversationRecord(
                user_id=owner.id,
                vehicle_id=car.id,
                concern="Raw concern",
                advisor_summary="Advisor-only summary accidentally marked client.",
                client_summary=None,
                visibility="client",
                provenance="legacy",
                verification_state="unverified",
                status="logged",
            )
        )
        db.session.commit()

        assert load_rina_summaries(user_id=owner.id, car_id=car.id) == ()


def test_driver_cannot_inherit_owner_summary_or_advisor_notes(app):
    with app.app_context():
        owner = _user(suffix=7)
        driver = _user(suffix=8, role="driver")
        advisor = _user(suffix=9, role="admin")
        car = _car(suffix=7)
        _own(owner=owner, car=car, suffix=7)
        db.session.add(CarDriver(car_id=car.id, user_id=driver.id, is_active=True))
        _client_record(
            user=owner,
            car=car,
            client_summary="Owner continuity note.",
            advisor_summary="Owner internal summary.",
        )
        _client_record(
            user=driver,
            car=car,
            client_summary="Driver's own operating continuity note.",
            advisor_summary="Driver interaction internal summary.",
        )
        db.session.add(
            AdvisorNote(
                user_id=owner.id,
                car_id=car.id,
                advisor_id=advisor.id,
                note="Advisor-only operational note.",
            )
        )
        db.session.commit()

        driver_memory = load_rina_summaries(user_id=driver.id, car_id=car.id)
        assert [item.summary for item in driver_memory] == [
            "Driver's own operating continuity note."
        ]

        with pytest.raises(RinaVehicleAuthorityDenied):
            load_rina_advisor_memory(user_id=driver.id, car_id=car.id)

        bundle = load_rina_memory_bundle(user_id=driver.id, car_id=car.id)
        assert bundle.advisor_memory == ()
        assert "Owner continuity note" not in repr(bundle.to_safe_dict())
        assert "Advisor-only operational note" not in repr(bundle.to_safe_dict())


def test_owner_cannot_retrieve_advisor_note_but_administrator_can(app):
    with app.app_context():
        owner = _user(suffix=10)
        administrator = _user(suffix=11, role="admin")
        car = _car(suffix=10)
        _own(owner=owner, car=car, suffix=10)
        note = AdvisorNote(
            user_id=owner.id,
            car_id=car.id,
            advisor_id=administrator.id,
            note="Restricted advisor deliberation.",
        )
        db.session.add(note)
        db.session.commit()

        with pytest.raises(RinaVehicleAuthorityDenied):
            load_rina_advisor_memory(user_id=owner.id, car_id=car.id)

        admin_notes = load_rina_advisor_memory(
            user_id=administrator.id,
            car_id=car.id,
        )
        assert len(admin_notes) == 1
        assert admin_notes[0].note_id == note.id
        assert admin_notes[0].content == "Restricted advisor deliberation."


def test_revoked_driver_immediately_loses_chat_memory_access(app):
    with app.app_context():
        owner = _user(suffix=12)
        driver = _user(suffix=13, role="driver")
        car = _car(suffix=12)
        _own(owner=owner, car=car, suffix=12)
        assignment = CarDriver(car_id=car.id, user_id=driver.id, is_active=True)
        db.session.add(assignment)
        db.session.commit()

        save_rina_chat_turn(
            user_id=driver.id,
            car_id=car.id,
            conversation_id="driver-revocation",
            role="user",
            content="Pre-revocation driver message.",
            commit=True,
        )
        assert len(load_rina_chat_history(user_id=driver.id, car_id=car.id)) == 1

        assignment.is_active = False
        db.session.commit()

        with pytest.raises(RinaVehicleAuthorityDenied):
            load_rina_chat_history(user_id=driver.id, car_id=car.id)


def test_chat_turn_transaction_is_caller_owned_by_default(app):
    with app.app_context():
        owner = _user(suffix=14)
        car = _car(suffix=14)
        _own(owner=owner, car=car, suffix=14)
        db.session.commit()

        row = save_rina_chat_turn(
            user_id=owner.id,
            car_id=car.id,
            conversation_id="rollback-test",
            role="user",
            content="This turn should roll back.",
        )
        row_id = row.id
        assert row_id is not None
        db.session.rollback()

        assert db.session.get(ChatMessage, row_id) is None


def test_owner_cannot_write_hidden_chat_visibility(app):
    with app.app_context():
        owner = _user(suffix=15)
        car = _car(suffix=15)
        _own(owner=owner, car=car, suffix=15)
        db.session.commit()

        with pytest.raises(RinaMemoryPolicyError):
            save_rina_chat_turn(
                user_id=owner.id,
                car_id=car.id,
                conversation_id="hidden-owner-turn",
                role="user",
                content="Do not permit hidden owner memory.",
                visibility="internal",
            )


def test_conversation_logger_requires_explicit_client_safe_summary(app):
    with app.app_context():
        owner = _user(suffix=16)
        car = _car(suffix=16)
        _own(owner=owner, car=car, suffix=16)
        db.session.commit()

        with pytest.raises(ValueError):
            log_conversation_record(
                owner.id,
                car.id,
                "I noticed a dashboard light.",
                visibility="client",
                client_summary=None,
                commit=False,
            )

        record = log_conversation_record(
            owner.id,
            car.id,
            "I noticed a dashboard light.",
            conversation_id="summary-contract",
            visibility="client",
            client_summary="A dashboard-light observation was recorded for review.",
            commit=False,
        )
        assert record.id is not None
        assert record.conversation_id == "summary-contract"
        assert record.visibility == "client"
        assert record.client_summary == (
            "A dashboard-light observation was recorded for review."
        )
        db.session.rollback()
