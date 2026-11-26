"""Database-backed synthetic tests of the unregistered owner eligibility gate."""
from datetime import datetime, timedelta, timezone

import pytest
from flask_login import login_user

from extensions import db
from models import Car, CarOwnership, User
from partnerships.prince_owner_destination import OwnerDestination, resolve_owner_destination


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def destination(app):
    owner = User(name="Synthetic owner", email="owner@example.test",
                 phone_number="synthetic-owner", role="user", is_active=True,
                 email_verified_at=_now())
    other = User(name="Synthetic other", email="other@example.test",
                 phone_number="synthetic-other", role="user", is_active=True,
                 email_verified_at=_now())
    for user in (owner, other):
        user.set_password("SyntheticTestPassword123!")
    car = Car(brand="Synthetic", model="Training", vin="DEMX123456789ABCD")
    db.session.add_all([owner, other, car])
    db.session.flush()
    ownership = CarOwnership(user_id=owner.id, car_id=car.id, is_active=True,
                             start_date=_now() - timedelta(days=1))
    db.session.add(ownership)
    db.session.commit()
    return owner, other, car, ownership


def test_fresh_current_owner_gets_internal_facts_only(app, destination):
    owner, _, car, ownership = destination
    with app.test_request_context():
        assert login_user(owner)
        assert resolve_owner_destination(car.id) == OwnerDestination(
            owner.id, car.id, ownership.id, "DEMX123456789ABCD",
        )
        assert resolve_owner_destination(car.id + 10000) is None
        assert resolve_owner_destination(str(car.id)) is None
        assert resolve_owner_destination(True) is None


def test_anonymous_denied(app, destination):
    _, _, car, _ = destination
    with app.test_request_context():
        assert resolve_owner_destination(car.id) is None


@pytest.mark.parametrize("reason", [
    "other_actor", "nonfresh", "admin", "driver", "inactive", "unverified",
    "ended", "inactive_ownership", "ambiguous", "future_start", "invalid_vin",
])
def test_owner_gate_fails_closed(app, destination, reason):
    owner, other, car, ownership = destination
    actor = owner
    if reason == "other_actor":
        actor = other
    elif reason == "admin":
        owner.role = "admin"
    elif reason == "driver":
        owner.role = "driver"
    elif reason == "unverified":
        owner.email_verified_at = None
    elif reason == "ended":
        ownership.end_date = _now()
    elif reason == "inactive_ownership":
        ownership.is_active = False
    elif reason == "ambiguous":
        db.session.add(CarOwnership(user_id=other.id, car_id=car.id, is_active=True,
                                    start_date=_now() - timedelta(days=1)))
    elif reason == "future_start":
        ownership.start_date = _now() + timedelta(days=1)
    elif reason == "invalid_vin":
        car.vin = "INVALID"
    db.session.commit()
    with app.test_request_context():
        assert login_user(actor, fresh=reason != "nonfresh")
        if reason == "inactive":
            owner.is_active = False
            db.session.commit()
        assert resolve_owner_destination(car.id) is None
