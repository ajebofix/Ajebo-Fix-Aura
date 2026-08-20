from __future__ import annotations

from datetime import datetime

from extensions import db
from models import Car, CarOwnership, User
from security.session_registry import UserSession

PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Consultation Trace {role} {suffix}",
        email=f"consultation-trace-{role}-{suffix}@example.com",
        phone_number=f"+234895100{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 20, 8, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _owned_car(owner: User, *, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin=f"W1NTRACE{suffix:09d}",
        current_mileage=42000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"TR-{suffix:03d}-LA",
            mileage_at_transfer=42000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _csrf(client) -> str:
    with client.session_transaction() as browser_session:
        return str(browser_session["_csrf_token"])


def _login(client, email: str):
    client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "email": email,
            "password": PASSWORD,
            "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )
    print("LOGIN", email, response.status_code, response.headers.get("Location"))
    with client.session_transaction() as browser_session:
        print(
            "SESSION_AFTER_LOGIN",
            {
                "has_user_id": bool(browser_session.get("_user_id")),
                "fresh": browser_session.get("_fresh"),
                "has_session_token": bool(browser_session.get("session_token")),
                "has_session_token_hash": bool(browser_session.get("session_token_hash")),
                "has_csrf": bool(browser_session.get("_csrf_token")),
            },
        )
    return response


def test_trace_advisor_queue_redirect(app):
    owner_client = app.test_client()
    advisor_client = app.test_client()

    with app.app_context():
        owner = _user(suffix=1)
        advisor = _user(suffix=2, role="admin")
        car = _owned_car(owner, suffix=1)
        owner_email = owner.email
        advisor_email = advisor.email
        advisor_id = advisor.id
        car_id = car.id

    owner_login = _login(owner_client, owner_email)
    assert owner_login.status_code in {302, 303}

    owner_client.get(f"/cars/{car_id}/consultations/book")
    owner_response = owner_client.post(
        f"/cars/{car_id}/consultations/book",
        data={
            "preferred_time": "2026-08-23T15:00",
            "description": "Owner preference.",
            "csrf_token": _csrf(owner_client),
        },
        follow_redirects=False,
    )
    print("OWNER_BOOK", owner_response.status_code, owner_response.headers.get("Location"))

    advisor_login = _login(advisor_client, advisor_email)
    assert advisor_login.status_code in {302, 303}

    with advisor_client.session_transaction() as browser_session:
        advisor_hash = browser_session.get("session_token_hash")
        print(
            "SESSION_BEFORE_QUEUE",
            {
                "has_user_id": bool(browser_session.get("_user_id")),
                "fresh": browser_session.get("_fresh"),
                "has_session_token": bool(browser_session.get("session_token")),
                "has_session_token_hash": bool(advisor_hash),
                "has_csrf": bool(browser_session.get("_csrf_token")),
            },
        )

    with app.app_context():
        records = UserSession.query.filter_by(user_id=advisor_id).all()
        print(
            "ADVISOR_SESSION_RECORDS",
            [
                {
                    "hash_matches": record.token_hash == advisor_hash,
                    "revoked": record.revoked_at is not None,
                    "reason": record.revoked_reason,
                    "not_expired": record.expires_at > datetime.utcnow(),
                }
                for record in records
            ],
        )

    queue = advisor_client.get("/admin/consultations", follow_redirects=False)
    print("QUEUE", queue.status_code, queue.headers.get("Location"))

    with advisor_client.session_transaction() as browser_session:
        print(
            "SESSION_AFTER_QUEUE",
            {
                "has_user_id": bool(browser_session.get("_user_id")),
                "fresh": browser_session.get("_fresh"),
                "has_session_token": bool(browser_session.get("session_token")),
                "has_session_token_hash": bool(browser_session.get("session_token_hash")),
                "flashes": browser_session.get("_flashes"),
            },
        )

    assert queue.status_code == 200, queue.headers.get("Location")
