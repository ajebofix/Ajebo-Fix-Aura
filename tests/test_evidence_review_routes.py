from __future__ import annotations

from datetime import datetime
import hashlib

from evidence.models import EvidenceLink, VehicleEvidence
from extensions import db
from models import Car, CarFault, CarOwnership, User


PASSWORD = "Password123"


def _user(
    *,
    suffix: int,
    role: str = "user",
    verified: bool = True,
) -> User:
    user = User(
        name=f"Evidence Review Route User {suffix}",
        email=f"evidence-review-route-{suffix}@example.com",
        phone_number=f"+234855000{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=(
            datetime(2026, 8, 16, 20, 0, 0) if verified else None
        ),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _owned_car(owner: User, *, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1NEVIDREVIEWRT{suffix:03d}",
        current_mileage=11000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"RR-{suffix:03d}-LA",
            mileage_at_transfer=11000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _evidence(*, car: Car, uploader: User, suffix: int) -> VehicleEvidence:
    payload = f"review-route-evidence-{suffix}".encode()
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
        object_key=f"evidence/{suffix:02x}/{suffix:032x}.jpg",
        safe_display_name=f"vehicle-evidence-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
        uploaded_at=datetime(2026, 8, 16, 20, 15, 0),
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def _concern(*, car: Car, reporter: User, suffix: int) -> CarFault:
    concern = CarFault(
        car_id=car.id,
        title=f"Evidence review route concern {suffix}",
        category="observation",
        description="Controlled route test concern.",
        status="reported",
        reported_by=reporter.id,
        source="client",
        reported_at=datetime(2026, 8, 16, 20, 30, 0),
    )
    db.session.add(concern)
    db.session.commit()
    return concern


def _csrf(client) -> str:
    with client.session_transaction() as flask_session:
        return str(flask_session["_csrf_token"])


def _login(client, user: User) -> None:
    client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "email": user.email,
            "password": PASSWORD,
            "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    client.get("/")
    _csrf(client)


def test_review_routes_are_registered_but_disabled_by_default(app, client):
    with app.app_context():
        owner = _user(suffix=1)
        advisor = _user(suffix=2, role="admin")
        car = _owned_car(owner, suffix=1)
        evidence = _evidence(car=car, uploader=owner, suffix=1)
        advisor_email = advisor.email
        evidence_id = evidence.id

    advisor = User.query.filter_by(email=advisor_email).first()
    _login(client, advisor)
    response = client.post(
        f"/admin/evidence/{evidence_id}/review",
        data={
            "csrf_token": _csrf(client),
            "decision": "accepted",
            "reason_code": "advisor_verified",
        },
    )
    assert response.status_code == 503
    assert response.get_json() == {"error": "evidence_review_unavailable"}


def test_unverified_advisor_is_blocked_before_review(app, client):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=3)
        advisor = _user(suffix=4, role="admin", verified=False)
        car = _owned_car(owner, suffix=2)
        evidence = _evidence(car=car, uploader=owner, suffix=2)
        advisor_email = advisor.email
        evidence_id = evidence.id

    advisor = User.query.filter_by(email=advisor_email).first()
    _login(client, advisor)
    response = client.post(
        f"/admin/evidence/{evidence_id}/review",
        data={
            "csrf_token": _csrf(client),
            "decision": "accepted",
            "reason_code": "advisor_verified",
        },
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "email_verification_required"


def test_owner_cannot_use_advisor_review_route(app, client):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=5)
        car = _owned_car(owner, suffix=3)
        evidence = _evidence(car=car, uploader=owner, suffix=3)
        owner_email = owner.email
        evidence_id = evidence.id

    owner = User.query.filter_by(email=owner_email).first()
    _login(client, owner)
    response = client.post(
        f"/admin/evidence/{evidence_id}/review",
        data={
            "csrf_token": _csrf(client),
            "decision": "accepted",
            "reason_code": "advisor_verified",
        },
    )
    assert response.status_code == 403
    assert response.get_json() == {"error": "evidence_review_access_denied"}


def test_advisor_review_route_accepts_without_exposing_storage_or_diagnosis(
    app,
    client,
):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=6)
        advisor = _user(suffix=7, role="admin")
        car = _owned_car(owner, suffix=4)
        evidence = _evidence(car=car, uploader=owner, suffix=4)
        advisor_email = advisor.email
        evidence_id = evidence.id

    advisor = User.query.filter_by(email=advisor_email).first()
    _login(client, advisor)
    response = client.post(
        f"/admin/evidence/{evidence_id}/review",
        data={
            "csrf_token": _csrf(client),
            "decision": "accepted",
            "reason_code": "advisor_verified",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["review_status"] == "accepted"
    assert payload["review_reason_code"] == "advisor_verified"
    serialized = str(payload).lower()
    assert "object_key" not in serialized
    assert "sha256" not in serialized
    assert "bucket" not in serialized
    assert "mechanical diagnosis" in payload["message"].lower()

    with app.app_context():
        evidence = db.session.get(VehicleEvidence, evidence_id)
        assert evidence.review_status == "accepted"
        assert evidence.storage_state == "available"


def test_invalid_review_reason_returns_conflict_without_mutation(app, client):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=8)
        advisor = _user(suffix=9, role="admin")
        car = _owned_car(owner, suffix=5)
        evidence = _evidence(car=car, uploader=owner, suffix=5)
        advisor_email = advisor.email
        evidence_id = evidence.id

    advisor = User.query.filter_by(email=advisor_email).first()
    _login(client, advisor)
    response = client.post(
        f"/admin/evidence/{evidence_id}/review",
        data={
            "csrf_token": _csrf(client),
            "decision": "accepted",
            "reason_code": "looks_like_failed_pump",
        },
    )
    assert response.status_code == 409
    assert response.get_json()["error"] == "evidence_review_conflict"

    with app.app_context():
        evidence = db.session.get(VehicleEvidence, evidence_id)
        assert evidence.review_status == "pending_review"
        assert evidence.reviewed_by_user_id is None


def test_advisor_links_accepted_evidence_to_same_vehicle_concern_idempotently(
    app,
    client,
):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=10)
        advisor = _user(suffix=11, role="admin")
        car = _owned_car(owner, suffix=6)
        evidence = _evidence(car=car, uploader=owner, suffix=6)
        concern = _concern(car=car, reporter=owner, suffix=1)
        advisor_email = advisor.email
        evidence_id = evidence.id
        concern_id = concern.id

    advisor = User.query.filter_by(email=advisor_email).first()
    _login(client, advisor)
    accepted = client.post(
        f"/admin/evidence/{evidence_id}/review",
        data={
            "csrf_token": _csrf(client),
            "decision": "accepted",
            "reason_code": "sufficient_for_record",
        },
    )
    assert accepted.status_code == 200

    link_url = (
        f"/admin/evidence/{evidence_id}/links/reported-concerns/{concern_id}"
    )
    first = client.post(link_url, data={"csrf_token": _csrf(client)})
    assert first.status_code == 201
    first_payload = first.get_json()
    assert first_payload["created"] is True
    assert first_payload["relationship_type"] == "supports"
    assert "does not establish a diagnosis" in first_payload["message"].lower()

    second = client.post(link_url, data={"csrf_token": _csrf(client)})
    assert second.status_code == 200
    assert second.get_json()["created"] is False

    with app.app_context():
        assert EvidenceLink.query.count() == 1


def test_cross_vehicle_concern_link_fails_closed(app, client):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner_one = _user(suffix=12)
        owner_two = _user(suffix=13)
        advisor = _user(suffix=14, role="admin")
        car_one = _owned_car(owner_one, suffix=7)
        car_two = _owned_car(owner_two, suffix=8)
        evidence = _evidence(car=car_one, uploader=owner_one, suffix=7)
        other_concern = _concern(car=car_two, reporter=owner_two, suffix=2)
        advisor_email = advisor.email
        evidence_id = evidence.id
        concern_id = other_concern.id

    advisor = User.query.filter_by(email=advisor_email).first()
    _login(client, advisor)
    accepted = client.post(
        f"/admin/evidence/{evidence_id}/review",
        data={
            "csrf_token": _csrf(client),
            "decision": "accepted",
            "reason_code": "advisor_verified",
        },
    )
    assert accepted.status_code == 200

    response = client.post(
        f"/admin/evidence/{evidence_id}/links/reported-concerns/{concern_id}",
        data={"csrf_token": _csrf(client)},
    )
    assert response.status_code == 403
    assert response.get_json() == {"error": "evidence_link_access_denied"}

    with app.app_context():
        assert EvidenceLink.query.count() == 0


def test_review_route_requires_application_csrf(app, client):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=15)
        advisor = _user(suffix=16, role="admin")
        car = _owned_car(owner, suffix=9)
        evidence = _evidence(car=car, uploader=owner, suffix=8)
        advisor_email = advisor.email
        evidence_id = evidence.id

    advisor = User.query.filter_by(email=advisor_email).first()
    _login(client, advisor)
    response = client.post(
        f"/admin/evidence/{evidence_id}/review",
        data={
            "decision": "accepted",
            "reason_code": "advisor_verified",
        },
    )
    assert response.status_code == 400


def test_review_and_link_routes_are_post_only(app, client):
    with app.app_context():
        owner = _user(suffix=17)
        advisor = _user(suffix=18, role="admin")
        car = _owned_car(owner, suffix=10)
        evidence = _evidence(car=car, uploader=owner, suffix=9)
        concern = _concern(car=car, reporter=owner, suffix=3)
        advisor_email = advisor.email
        evidence_id = evidence.id
        concern_id = concern.id

    advisor = User.query.filter_by(email=advisor_email).first()
    _login(client, advisor)
    assert client.get(f"/admin/evidence/{evidence_id}/review").status_code == 405
    assert client.get(
        f"/admin/evidence/{evidence_id}/links/reported-concerns/{concern_id}"
    ).status_code == 405
