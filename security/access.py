"""Central authorization helpers for Aura vehicle resources."""

from __future__ import annotations

from flask import abort
from flask_login import current_user

from models import Car, CarDriver, CarOwnership


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

    if allow_advisor and getattr(current_user, "is_admin", False):
        return car

    if allow_owner:
        ownership = CarOwnership.query.filter_by(
            car_id=car_id,
            user_id=current_user.id,
            is_active=True,
        ).first()
        if ownership:
            return car

    if allow_driver:
        assignment = CarDriver.query.filter_by(
            car_id=car_id,
            user_id=current_user.id,
            is_active=True,
        ).first()
        if assignment:
            return car

    abort(403)


def require_advisor() -> None:
    """Abort unless the current user has advisor authority."""

    if not current_user.is_authenticated:
        abort(401)

    if not getattr(current_user, "is_admin", False):
        abort(403)
