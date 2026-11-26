"""Read-only destination eligibility for a future owner-initiated transfer.

Not an HTTP endpoint, identity proof, ownership mutation, or transfer authority.
Call only from the authenticated Aura owner flow after its normal session
registry checks. Re-read and lock ownership during any future transfer.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from flask_login import current_user, login_fresh

from models import Car, CarOwnership, User
from partnerships.prince_identity_policy import normalize_vin


@dataclass(frozen=True)
class OwnerDestination:
    owner_user_id: int
    car_id: int
    ownership_id: int
    vin: str


def resolve_owner_destination(car_id: int) -> OwnerDestination | None:
    """Return internal destination facts, never a link or publication grant.

    The actor is taken from the authenticated session, not a supplied owner ID.
    No lookup by VIN, registration plate, customer name or workshop record.
    All denials are indistinguishable to a future caller-facing endpoint.
    """
    if not current_user.is_authenticated or not login_fresh():
        return None
    if type(car_id) is not int or car_id <= 0:
        return None
    user = User.query.populate_existing().filter_by(id=current_user.id).one_or_none()
    if (user is None or not user.is_active or user.role != "user"
            or not user.email or user.email_verified_at is None):
        return None
    # Establish that this actor owns the selected car before inspecting it.
    owned = CarOwnership.query.populate_existing().filter_by(
        car_id=car_id, user_id=user.id, is_active=True,
    ).limit(2).all()
    if len(owned) != 1 or owned[0].end_date is not None:
        return None
    active = CarOwnership.query.populate_existing().filter_by(
        car_id=car_id, is_active=True,
    ).limit(2).all()
    if len(active) != 1 or active[0].id != owned[0].id:
        return None
    ownership = active[0]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if ownership.start_date is None or ownership.start_date > now:
        return None
    car = Car.query.populate_existing().filter_by(id=car_id).one_or_none()
    vin = normalize_vin(car.vin) if car is not None else None
    if vin is None:
        return None
    return OwnerDestination(user.id, car.id, ownership.id, vin)
