from __future__ import annotations

from datetime import datetime
import hashlib

from evidence.models import VehicleEvidence
from extensions import db
from models import Car, CarDriver, CarFault, CarOwnership, User


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user", verified: bool = True) -> User:
    user = User(
        name=f"Evidence Interaction User {suffix}",
        email=f"evidence-interaction-{suffix}@example.com",
        phone_number=f"+234811000{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=(
            datetime(2026, 8, 16, 23, 30, 0) if verified else None
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
        vin=f"W1NEVIDINTERACT{suffix:02d}",
        current_mileage=16000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"EI-{suffix:03d}-LA",
            mileage_at_transfer=16000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _assign_driver(car: Car, driver: User) -> None:
    db.session.add(CarDriver(car_id=car.id, user_id=driver.id, is_active=True))
    db.session.commit()


def _evidence(*, car: Car, uploader: User, suffix: int) -> VehicleEvidence:
    payload = f"evidence-interaction-ui-{suffix}".encode()
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
        object_key=f"evidence/interaction/{suffix:032x}.jpg",
        safe_display_name=f"vehicle-evidence-interaction-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
        uploaded_at=datetime(2026, 8, 16, 23, 35, 0),
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def _concern(*, car: Car, reporter: User) -> CarFault:
    concern = CarFault(
        car_id=car.id,
        title="Steering vibration under review",
        category="observation",
        description="Steering vibration reported during motorway driving.",
        status="reported",
        reported_by=reporter.id,
        source="client",
        reported_at=datetime(2026, 8, 16, 23, 40, 0),
    )
    db.session.add(concern)
    db.session.commit()
    return concern


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


def test_interaction_get_surfaces_are_registered_but_disabled_by_default(app, client):
    with app.app_context():
        owner = _user(suffix=1)
        advisor = _user(suffix=2, role="admin")
        car = _owned_car(owner, suffix=1)
        owner_email = owner.email
        advisor_email = advisor.email
        car_id = car.id

    _login(client, owner_email)
    submit = client.get(f"/evidence/vehicles/{car_id}/submit")
    assert submit.status_code == 503
    assert "Evidence submission is not enabled yet" in submit.get_data(as_text=True)

    with app.test_client() as advisor_client:
        _login(advisor_client, advisor_email)
        pending = advisor_client.get(f"/admin/evidence/vehicles/{car_id}/pending")
    assert pending.status_code == 503
    assert "Evidence review is not enabled yet" in pending.get_data(as_text=True)


def test_verified_owner_submission_page_uses_existing_upload_endpoint(app, client):
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=3)
        car = _owned_car(owner, suffix=2)
        owner_email = owner.email
        car_id = car.id

    _login(client, owner_email)
    response = client.get(f"/evidence/vehicles/{car_id}/submit")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Submit one image for review" in html
    assert f'action="/evidence/vehicles/{car_id}/images"' in html
    assert "Reported concern support" in html
    assert "Consultation support" in html
    assert "evidence-interaction.css" in html
    assert "stored privately" in html.lower()
    for forbidden in ("object_key", "sha256", "storage_provider", "safe_display_name"):
        assert forbidden not in html


def test_driver_submission_choices_are_limited_to_operational_purposes(app, client):
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=4)
        driver = _user(suffix=5, role="driver")
        car = _owned_car(owner, suffix=3)
        _assign_driver(car, driver)
        driver_email = driver.email
        car_id = car.id

    _login(client, driver_email)
    response = client.get(f"/evidence/vehicles/{car_id}/submit")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Reported concern support" in html
    assert "Driver observation" in html
    assert "Consultation support" not in html
    assert "Assessment evidence" not in html
    assert f'/driver/cars/{car_id}' in html


def test_submission_surface_redirects_unverified_identity_and_denies_outsider(app, client):
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=6)
        outsider = _user(suffix=7)
        unverified = _user(suffix=8, verified=False)
        car = _owned_car(owner, suffix=4)
        outsider_email = outsider.email
        unverified_email = unverified.email
        car_id = car.id

    _login(client, unverified_email)
    response = client.get(f"/evidence/vehicles/{car_id}/submit", follow_redirects=False)
    assert response.status_code in {302, 303}
    assert "/auth/verification-required" in response.headers["Location"]

    with app.test_client() as outsider_client:
        _login(outsider_client, outsider_email)
        denied = outsider_client.get(f"/evidence/vehicles/{car_id}/submit")
    assert denied.status_code == 403


def test_advisor_pending_queue_exposes_safe_metadata_only(app, client):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=9)
        advisor = _user(suffix=10, role="admin")
        car = _owned_car(owner, suffix=5)
        evidence = _evidence(car=car, uploader=owner, suffix=1)
        advisor_email = advisor.email
        car_id = car.id
        evidence_id = evidence.id
        object_key = evidence.object_key
        digest = evidence.sha256

    _login(client, advisor_email)
    response = client.get(f"/admin/evidence/vehicles/{car_id}/pending")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Evidence awaiting review" in html
    assert "Evidence Interaction User 9" in html
    assert "Reported concern support" in html
    assert f"/admin/evidence/{evidence_id}/workspace" in html
    assert object_key not in html
    assert digest not in html
    for forbidden in ("object_key", "sha256", "storage_provider", "safe_display_name"):
        assert forbidden not in html


def test_owner_cannot_open_advisor_pending_queue(app, client):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=11)
        car = _owned_car(owner, suffix=6)
        owner_email = owner.email
        car_id = car.id

    _login(client, owner_email)
    response = client.get(f"/admin/evidence/vehicles/{car_id}/pending")
    assert response.status_code == 403


def test_advisor_workspace_uses_existing_private_and_review_endpoints(app, client):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    app.config["EVIDENCE_RETRIEVAL_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=12)
        advisor = _user(suffix=13, role="admin")
        car = _owned_car(owner, suffix=7)
        evidence = _evidence(car=car, uploader=owner, suffix=2)
        concern = _concern(car=car, reporter=owner)
        advisor_email = advisor.email
        evidence_id = evidence.id
        object_key = evidence.object_key
        digest = evidence.sha256
        concern_title = concern.title

    _login(client, advisor_email)
    response = client.get(f"/admin/evidence/{evidence_id}/workspace")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Evidence Review Workspace" in html
    assert "Securely View Image" in html
    assert f'/evidence/{evidence_id}/grant' in html
    assert f'/admin/evidence/{evidence_id}/review' in html
    assert f'/admin/evidence/{evidence_id}/links/reported-concerns/' in html
    assert concern_title in html
    assert "Accept Evidence" in html
    assert "Reject Evidence" in html
    assert "evidence-review-action\" type=\"submit\" disabled" in html
    assert "Review actions unlock only after the private image has been loaded" in html
    assert "content_endpoint" in html
    assert object_key not in html
    assert digest not in html
    for forbidden in ("object_key", "sha256", "storage_provider", "safe_display_name"):
        assert forbidden not in html


def test_completed_review_disappears_from_pending_queue_and_workspace(app, client):
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=14)
        advisor = _user(suffix=15, role="admin")
        car = _owned_car(owner, suffix=8)
        evidence = _evidence(car=car, uploader=owner, suffix=3)
        evidence.review_status = "accepted"
        evidence.reviewed_by_user_id = advisor.id
        evidence.reviewed_at = datetime(2026, 8, 16, 23, 50, 0)
        evidence.review_reason_code = "advisor_verified"
        db.session.commit()
        advisor_email = advisor.email
        car_id = car.id
        evidence_id = evidence.id

    _login(client, advisor_email)
    queue = client.get(f"/admin/evidence/vehicles/{car_id}/pending")
    assert queue.status_code == 200
    assert f"/admin/evidence/{evidence_id}/workspace" not in queue.get_data(as_text=True)

    workspace = client.get(f"/admin/evidence/{evidence_id}/workspace")
    assert workspace.status_code == 404


def test_driver_vehicle_page_surfaces_submission_action_only_when_enabled(app, client):
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=16)
        driver = _user(suffix=17, role="driver")
        car = _owned_car(owner, suffix=9)
        _assign_driver(car, driver)
        driver_email = driver.email
        car_id = car.id

    _login(client, driver_email)
    response = client.get(f"/driver/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Supporting Image" in html
    assert f"/evidence/vehicles/{car_id}/submit" in html
