from __future__ import annotations

from datetime import datetime
import hashlib

from evidence.models import VehicleEvidence
from evidence.review import review_evidence
from extensions import db
from models import Car, CarOwnership, User


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user", verified: bool = True) -> User:
    user = User(
        name=f"Evidence Timeline Route User {suffix}",
        email=f"evidence-timeline-route-{suffix}@example.com",
        phone_number=f"+234888000{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=(
            datetime(2026, 8, 16, 22, 30, 0) if verified else None
        ),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _owned_car(owner: User, *, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="E 450",
        year=2024,
        vin=f"W1NEVIDTLROUTE{suffix:03d}",
        current_mileage=8000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"TR-{suffix:03d}-LA",
            mileage_at_transfer=8000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _reviewed_evidence(
    *,
    car: Car,
    uploader: User,
    advisor: User,
    suffix: int,
    visibility: str = "client",
) -> VehicleEvidence:
    payload = f"timeline-route-evidence-{suffix}".encode()
    evidence = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=uploader.id,
        evidence_type="image",
        purpose="concern_support",
        source_channel="web",
        visibility=visibility,
        review_status="pending_review",
        storage_provider="test-private",
        storage_state="available",
        object_key=f"evidence/{suffix:02x}/{suffix:032x}.jpg",
        safe_display_name=f"vehicle-evidence-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
        uploaded_at=datetime(2026, 8, 16, 22, 35, 0),
    )
    db.session.add(evidence)
    db.session.commit()
    review_evidence(
        reviewer_user_id=advisor.id,
        evidence_id=evidence.id,
        decision="accepted",
        reason_code="advisor_verified",
    )
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


def test_timeline_routes_are_registered_but_disabled_by_default(app, client):
    with app.app_context():
        owner = _user(suffix=1)
        car = _owned_car(owner, suffix=1)
        owner_email = owner.email
        car_id = car.id

    _login(client, owner_email)
    response = client.get(f"/evidence/vehicles/{car_id}/timeline")
    assert response.status_code == 503
    assert response.get_json()["error"] == "evidence_timeline_unavailable"


def test_verified_owner_receives_client_safe_timeline_without_storage_metadata(
    app,
    client,
):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=2)
        advisor = _user(suffix=3, role="admin")
        car = _owned_car(owner, suffix=2)
        evidence = _reviewed_evidence(
            car=car,
            uploader=owner,
            advisor=advisor,
            suffix=2,
        )
        owner_email = owner.email
        car_id = car.id
        evidence_id = evidence.id

    _login(client, owner_email)
    response = client.get(
        f"/evidence/vehicles/{car_id}/timeline",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["viewer_authority"] == "owner"
    assert payload["record_count"] == 1
    assert payload["records"][0]["evidence_id"] == evidence_id
    serialized = str(payload).lower()
    for forbidden in (
        "object_key",
        "sha256",
        "storage_provider",
        "storage_state",
        "safe_display_name",
        "review_reason_code",
        "uploaded_by_user_id",
    ):
        assert forbidden not in serialized


def test_unverified_owner_is_blocked_from_timeline(app, client):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=4, verified=False)
        car = _owned_car(owner, suffix=3)
        owner_email = owner.email
        car_id = car.id

    _login(client, owner_email)
    response = client.get(
        f"/evidence/vehicles/{car_id}/timeline",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "email_verification_required"


def test_outsider_cannot_read_client_timeline(app, client):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=5)
        outsider = _user(suffix=6)
        car = _owned_car(owner, suffix=4)
        outsider_email = outsider.email
        car_id = car.id

    _login(client, outsider_email)
    response = client.get(f"/evidence/vehicles/{car_id}/timeline")
    assert response.status_code == 403
    assert response.get_json() == {"error": "evidence_timeline_access_denied"}


def test_owner_cannot_use_advisor_timeline_surface(app, client):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=7)
        car = _owned_car(owner, suffix=5)
        owner_email = owner.email
        car_id = car.id

    _login(client, owner_email)
    response = client.get(f"/admin/evidence/vehicles/{car_id}/timeline")
    assert response.status_code == 403
    assert response.get_json() == {"error": "evidence_timeline_access_denied"}


def test_advisor_timeline_exposes_governance_codes_but_not_storage_secrets(
    app,
    client,
):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=8)
        advisor = _user(suffix=9, role="admin")
        car = _owned_car(owner, suffix=6)
        evidence = _reviewed_evidence(
            car=car,
            uploader=owner,
            advisor=advisor,
            suffix=3,
            visibility="advisor",
        )
        advisor_email = advisor.email
        car_id = car.id
        evidence_id = evidence.id
        owner_id = owner.id

    _login(client, advisor_email)
    response = client.get(
        f"/admin/evidence/vehicles/{car_id}/timeline",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    record = payload["records"][0]
    assert record["evidence_id"] == evidence_id
    assert record["visibility"] == "advisor"
    assert record["review_reason_code"] == "advisor_verified"
    assert record["uploaded_by_user_id"] == owner_id
    serialized = str(payload).lower()
    assert "object_key" not in serialized
    assert "sha256" not in serialized
    assert "storage_provider" not in serialized
    assert "bucket" not in serialized
