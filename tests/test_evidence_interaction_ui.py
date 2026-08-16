from __future__ import annotations

from datetime import datetime
import hashlib

from evidence.models import VehicleEvidence
from extensions import db
from models import Car, CarOwnership, User


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Evidence Interaction UI User {suffix}",
        email=f"evidence-interaction-ui-{suffix}@example.com",
        phone_number=f"+234812000{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 16, 23, 45, 0),
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
        vin=f"W1NEVIDINTUI{suffix:04d}",
        current_mileage=17000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"EU-{suffix:03d}-LA",
            mileage_at_transfer=17000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _pending_image(
    *,
    car: Car,
    uploader: User,
    suffix: int,
    visibility: str = "client",
) -> VehicleEvidence:
    payload = f"interaction-ui-pending-{suffix}".encode()
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
        object_key=f"evidence/interaction-ui/{suffix:032x}.jpg",
        safe_display_name=f"vehicle-evidence-interaction-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
        uploaded_at=datetime(2026, 8, 16, 23, 50, suffix % 60),
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


def test_owner_vehicle_page_shows_image_submission_only_when_intake_enabled(app, client):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=1)
        car = _owned_car(owner, suffix=1)
        owner_email = owner.email
        car_id = car.id

    _login(client, owner_email)
    response = client.get(f"/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Add evidence for review" in html
    assert "Submit for review" in html
    assert "Support a reported concern" in html
    assert "Support a consultation" in html
    assert 'accept="image/jpeg,image/png,image/webp"' in html
    assert "Maximum 2 MB" in html
    assert "I consent to this sanitized image being stored privately" in html
    assert f"/evidence/vehicles/{car_id}/images" in html
    assert "Pending evidence" not in html
    assert "evidence-record.js" in html


def test_owner_upload_controls_are_absent_when_intake_flag_is_off(app, client):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = False
    with app.app_context():
        owner = _user(suffix=2)
        car = _owned_car(owner, suffix=2)
        owner_email = owner.email
        car_id = car.id

    _login(client, owner_email)
    response = client.get(f"/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Reviewed Evidence Record" in html
    assert "Add evidence for review" not in html
    assert "Submit for review" not in html


def test_owner_never_receives_advisor_pending_queue_metadata(app, client):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    app.config["EVIDENCE_RETRIEVAL_ENABLED"] = True
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=3)
        other_uploader = _user(suffix=4)
        car = _owned_car(owner, suffix=3)
        evidence = _pending_image(
            car=car,
            uploader=other_uploader,
            suffix=3,
            visibility="advisor",
        )
        owner_email = owner.email
        car_id = car.id
        uploader_name = other_uploader.name
        object_key = evidence.object_key
        sha256 = evidence.sha256

    _login(client, owner_email)
    response = client.get(f"/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Pending evidence" not in html
    assert "Open private image" not in html
    assert uploader_name not in html
    assert object_key not in html
    assert sha256 not in html


def test_advisor_pending_queue_renders_private_preview_and_hidden_review_controls(
    app,
    client,
):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    app.config["EVIDENCE_RETRIEVAL_ENABLED"] = True
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=5)
        advisor = _user(suffix=6, role="admin")
        car = _owned_car(owner, suffix=4)
        evidence = _pending_image(car=car, uploader=owner, suffix=4)
        advisor_email = advisor.email
        car_id = car.id
        evidence_id = evidence.id
        owner_name = owner.name
        object_key = evidence.object_key
        sha256 = evidence.sha256

    _login(client, advisor_email)
    response = client.get(f"/admin/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Pending evidence" in html
    assert "Open the private image before recording a professional review decision." in html
    assert owner_name in html
    assert "Open private image" in html
    assert f"/evidence/{evidence_id}/grant" in html
    assert f"/admin/evidence/{evidence_id}/review" in html
    assert f'data-evidence-review-controls="{evidence_id}"' in html
    assert "Save review decision" in html
    assert "Accept into care record" in html
    assert "Do not use as care evidence" in html
    assert "temporary preview is delivered through Aura&#39;s authenticated private retrieval flow" in html
    assert object_key not in html
    assert sha256 not in html
    assert "storage_provider" not in html


def test_advisor_pending_queue_does_not_offer_review_when_private_preview_is_off(
    app,
    client,
):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    app.config["EVIDENCE_RETRIEVAL_ENABLED"] = False
    app.config["EVIDENCE_ADVISOR_REVIEW_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=7)
        advisor = _user(suffix=8, role="admin")
        car = _owned_car(owner, suffix=5)
        evidence = _pending_image(car=car, uploader=owner, suffix=5)
        advisor_email = advisor.email
        car_id = car.id
        evidence_id = evidence.id

    _login(client, advisor_email)
    response = client.get(f"/admin/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Pending evidence" in html
    assert "Private preview is not enabled yet." in html
    assert "Open private image" not in html
    assert "Save review decision" not in html
    assert f"/evidence/{evidence_id}/grant" not in html


def test_evidence_interaction_javascript_preserves_private_flow_contract(app, client):
    response = client.get("/static/js/evidence-record.js")
    assert response.status_code == 200
    script = response.get_data(as_text=True)

    assert "2 * 1024 * 1024" in script
    assert '"image/jpeg"' in script
    assert '"image/png"' in script
    assert '"image/webp"' in script
    assert "grant.grant_token" in script
    assert "grant.content_endpoint" in script
    assert "JSON.stringify({ grant_token: grant.grant_token })" in script
    assert "URL.createObjectURL(blob)" in script
    assert "URL.revokeObjectURL(activeObjectUrl)" in script
    assert '"advisor_verified"' in script
    assert '"sufficient_for_record"' in script
    assert '"insufficient_quality"' in script
    assert '"privacy_restriction"' in script
    assert "Uploading securely" in script
    assert "Submitted privately. Pending advisor review." in script
