from __future__ import annotations

from datetime import datetime

import pytest

from extensions import db
from models import Car, CarDriver, CarOwnership, Consultation, User
from services.rina_authority import (
    ACTION_ADMIN_GOVERNANCE,
    ACTION_APPROVE_ASSESSMENT,
    ACTION_APPROVE_TREATMENT,
    ACTION_READ_ADVISOR_MEMORY,
    ACTION_READ_OWNER_FINANCIAL_CONTEXT,
    AUTHORITY_ADMINISTRATOR,
    AUTHORITY_ADVISOR,
    AUTHORITY_DRIVER,
    AUTHORITY_OWNER,
    RinaVehicleAuthorityDenied,
    require_rina_action,
    resolve_rina_authority,
)
from services.rina_context_resolver import (
    RinaContextResolutionError,
    resolve_rina_vehicle_context,
)


PASSWORD = "Password123"


def _user(*, suffix: str, role: str = "user") -> User:
    user = User(
        name=f"Rina Authority {suffix}",
        email=f"rina-authority-{suffix}@example.com",
        phone_number=f"0800510{int(suffix):04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 13, 7, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _car(*, suffix: str) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2024,
        vin=f"W1NRINAUTH0000{int(suffix):04d}",
        current_mileage=24000,
        vehicle_identity_source="manual",
    )
    db.session.add(car)
    db.session.flush()
    return car


def _own(*, owner: User, car: Car, suffix: str) -> CarOwnership:
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"RA-{int(suffix):03d}-LA",
        mileage_at_transfer=24000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.flush()
    return ownership


def test_owner_authority_is_vehicle_specific_and_human_approvals_stay_denied(app):
    with app.app_context():
        owner = _user(suffix="1")
        own_car = _car(suffix="1")
        other_car = _car(suffix="2")
        _own(owner=owner, car=own_car, suffix="1")
        db.session.commit()

        authority = resolve_rina_authority(user_id=owner.id, car_id=own_car.id)

        assert authority.authority == AUTHORITY_OWNER
        assert authority.global_role == "user"
        assert AUTHORITY_OWNER in authority.relationships
        assert authority.allows(ACTION_READ_OWNER_FINANCIAL_CONTEXT)
        assert not authority.allows(ACTION_APPROVE_ASSESSMENT)
        assert not authority.allows(ACTION_APPROVE_TREATMENT)

        with pytest.raises(RinaVehicleAuthorityDenied):
            require_rina_action(
                authority_context=authority,
                action=ACTION_APPROVE_TREATMENT,
            )

        with pytest.raises(RinaVehicleAuthorityDenied):
            resolve_rina_authority(user_id=owner.id, car_id=other_car.id)


def test_driver_cannot_inherit_owner_financial_or_approval_authority(app):
    with app.app_context():
        owner = _user(suffix="3")
        driver = _user(suffix="4", role="driver")
        car = _car(suffix="3")
        _own(owner=owner, car=car, suffix="3")
        db.session.add(CarDriver(car_id=car.id, user_id=driver.id, is_active=True))
        db.session.commit()

        authority = resolve_rina_authority(user_id=driver.id, car_id=car.id)

        assert authority.authority == AUTHORITY_DRIVER
        assert not authority.allows(ACTION_READ_OWNER_FINANCIAL_CONTEXT)
        assert not authority.allows(ACTION_READ_ADVISOR_MEMORY)
        assert not authority.allows(ACTION_APPROVE_ASSESSMENT)
        assert not authority.allows(ACTION_APPROVE_TREATMENT)


def test_advisor_role_requires_persisted_vehicle_scope(app):
    with app.app_context():
        owner = _user(suffix="5")
        advisor = _user(suffix="6", role="advisor")
        car = _car(suffix="5")
        ownership = _own(owner=owner, car=car, suffix="5")
        db.session.commit()

        with pytest.raises(RinaVehicleAuthorityDenied):
            resolve_rina_authority(user_id=advisor.id, car_id=car.id)

        consultation = Consultation(
            car_id=car.id,
            ownership_id=ownership.id,
            advisor_id=advisor.id,
            client_id=owner.id,
            status="scheduled",
            scheduled_for=datetime(2026, 8, 14, 9, 0, 0),
        )
        db.session.add(consultation)
        db.session.commit()

        authority = resolve_rina_authority(user_id=advisor.id, car_id=car.id)
        assert authority.authority == AUTHORITY_ADVISOR
        assert authority.allows(ACTION_READ_ADVISOR_MEMORY)
        assert not authority.allows(ACTION_ADMIN_GOVERNANCE)


def test_administrator_is_not_misrepresented_as_owner_or_advisor(app):
    with app.app_context():
        administrator = _user(suffix="7", role="admin")
        car = _car(suffix="7")
        db.session.commit()

        authority = resolve_rina_authority(
            user_id=administrator.id,
            car_id=car.id,
        )

        assert authority.authority == AUTHORITY_ADMINISTRATOR
        assert authority.global_role == "admin"
        assert AUTHORITY_ADMINISTRATOR in authority.relationships
        assert AUTHORITY_OWNER not in authority.relationships
        assert authority.allows(ACTION_ADMIN_GOVERNANCE)


def test_context_requires_explicit_vehicle_and_rechecks_authority(app):
    with app.app_context():
        owner = _user(suffix="8")
        car = _car(suffix="8")
        other_car = _car(suffix="9")
        _own(owner=owner, car=car, suffix="8")
        db.session.commit()

        with pytest.raises(RinaContextResolutionError):
            resolve_rina_vehicle_context(user_id=owner.id, car_id=None)

        context = resolve_rina_vehicle_context(user_id=owner.id, car_id=car.id)
        assert context.car_id == car.id
        assert context.vehicle.car_id == car.id
        assert context.authority == AUTHORITY_OWNER
        assert context.visibility_scope == ("client",)
        assert context.vehicle.verification_state == "not_recorded"

        with pytest.raises(RinaVehicleAuthorityDenied):
            resolve_rina_vehicle_context(user_id=owner.id, car_id=other_car.id)


def test_revoked_driver_assignment_immediately_loses_rina_access(app):
    with app.app_context():
        owner = _user(suffix="10")
        driver = _user(suffix="11", role="driver")
        car = _car(suffix="10")
        _own(owner=owner, car=car, suffix="10")
        assignment = CarDriver(car_id=car.id, user_id=driver.id, is_active=True)
        db.session.add(assignment)
        db.session.commit()

        assert (
            resolve_rina_vehicle_context(user_id=driver.id, car_id=car.id).authority
            == AUTHORITY_DRIVER
        )

        assignment.is_active = False
        db.session.commit()

        with pytest.raises(RinaVehicleAuthorityDenied):
            resolve_rina_vehicle_context(user_id=driver.id, car_id=car.id)


def test_unknown_action_is_denied_even_for_administrator(app):
    with app.app_context():
        administrator = _user(suffix="12", role="admin")
        car = _car(suffix="12")
        db.session.commit()

        authority = resolve_rina_authority(
            user_id=administrator.id,
            car_id=car.id,
        )

        with pytest.raises(RinaVehicleAuthorityDenied):
            require_rina_action(
                authority_context=authority,
                action="provider.override_permissions",
            )
