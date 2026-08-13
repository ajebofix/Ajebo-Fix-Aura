"""Central authorization helpers for Aura vehicle resources."""

from __future__ import annotations

from flask import abort
from flask_login import current_user

from models import Car, CarDriver, CarOwnership, User


VEHICLE_AUTHORITY_OWNER = "owner"
VEHICLE_AUTHORITY_DRIVER = "driver"
VEHICLE_AUTHORITY_ADVISOR = "advisor"


def resolve_vehicle_authority(user_id: int, car_id: int) -> str | None:
    """Return the strongest proven authority a user holds for one vehicle.

    Authority is derived from the same persisted relationships used by Aura's
    vehicle-access gates. A global user role alone never implies ownership.
    Advisors are represented by the current ``admin`` role until Aura gains a
    dedicated advisor identity model.
    """

    user = User.query.filter_by(id=user_id, is_active=True).first()
    if user is None:
        return None

    if getattr(user, "is_admin", False):
        return VEHICLE_AUTHORITY_ADVISOR

    ownership = CarOwnership.query.filter_by(
        car_id=car_id,
        user_id=user_id,
        is_active=True,
    ).first()
    if ownership is not None:
        return VEHICLE_AUTHORITY_OWNER

    assignment = CarDriver.query.filter_by(
        car_id=car_id,
        user_id=user_id,
        is_active=True,
    ).first()
    if assignment is not None:
        return VEHICLE_AUTHORITY_DRIVER

    return None


def require_vehicle_access(
    car_id: int,
    *,
    allow_owner: bool = True,
    allow_driver: bool = False,
    allow_advisor: bool = False,
) -> Car:
    """Return the car only when the current user is authorized to access it."""

    if not current_user.is_authenticated:
        abort(401)

    car = Car.query.get_or_404(car_id)

    authority = resolve_vehicle_authority(current_user.id, car_id)

    if allow_advisor and authority == VEHICLE_AUTHORITY_ADVISOR:
        return car

    if allow_owner and authority == VEHICLE_AUTHORITY_OWNER:
        return car

    if allow_driver and authority == VEHICLE_AUTHORITY_DRIVER:
        return car

    abort(403)


def require_advisor() -> None:
    """Abort unless the current user has advisor authority."""

    if not current_user.is_authenticated:
        abort(401)

    if not getattr(current_user, "is_admin", False):
        abort(403)
