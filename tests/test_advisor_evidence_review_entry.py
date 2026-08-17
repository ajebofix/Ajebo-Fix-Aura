from __future__ import annotations

from datetime import datetime
import hashlib

from evidence.models import VehicleEvidence
from extensions import db
from models import Car, CarOwnership, User


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user", verified: bool = True) -> User:
    user = User(
        name=f"Advisor Evidence Entry {suffix}",
        email=f"advisor-evidence-entry-{suffix}@example.com",
        phone_number=f"+234897100{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=(datetime(2026, 8, 17, 23, 0, 0) if verified else None),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _owned_car(owner: User, *, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2021,
        vin=f"W1NADVEVIDENT{suffix:05d}",
        current_mileage=42000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"AE-{suffix:03d}-LA",
            mileage_at_transfer=42000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _pending_evidence(*, car: Car, uploader: User, suffix: int) -> VehicleEvidence:
    payload = f"advisor-review-entry-{suffix}".encode()
    evidence = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=uploader.id,
        evidence_type="image",
        purpose="concern_support",
        source_channel="web",
        visibility="client",
        review_status="pending_review",
        storage_provider="test-private",
        storage_state="available",
        object_key=f"evidence/advisor-entry/{suffix:032x}.jpg",
        safe_display_name=f"advisor-entry-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
        uploaded_at=datetime(2026, 8, 17, 23, 5, 0),
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def _csrf(client) -> str:
    with client.session_transaction() as flask_session:
        return str(flask_session["_csrf_token"])


def _login(client, email: str) -> None:
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
    assert response.status_code in {302, 303}
    client.get("/")
    _csrf(client)


def test_verified_advisor_sees_review_entry_before_timeline_cutover(app, client):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    app.config["EVIDENCE_RETRIEVAL_ENABLED"] = True
    app.config["EVIDENCE_TIMELINE_ENABLED"] = False

    with app.app_context():
        owner = _user(suffix=1)
        advisor = _user(suffix=2, role="admin")
        car = _owned_car(owner, suffix=1)
        _pending_evidence(car=car, uploader=owner, suffix=1)
        advisor_email = advisor.email
        car_id = car.id

    _login(client, advisor_email)
    response = client.get(f"/admin/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Pending Evidence" in html
    assert "1 pending" in html
    assert "Review Pending Evidence" in html
    assert f'/admin/evidence/vehicles/{car_id}/pending' in html
    assert "Reviewed Evidence Record" not in html


def test_advisor_review_entry_stays_hidden_when_review_feature_is_off(app, client):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = False
    app.config["EVIDENCE_RETRIEVAL_ENABLED"] = True
    app.config["EVIDENCE_TIMELINE_ENABLED"] = False

    with app.app_context():
        owner = _user(suffix=3)
        advisor = _user(suffix=4, role="admin")
        car = _owned_car(owner, suffix=2)
        advisor_email = advisor.email
        car_id = car.id

    _login(client, advisor_email)
    response = client.get(f"/admin/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Pending Evidence" not in html
    assert "Review Pending Evidence" not in html
