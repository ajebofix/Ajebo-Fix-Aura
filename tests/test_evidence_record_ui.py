from __future__ import annotations

from datetime import datetime
import hashlib

from evidence.models import VehicleEvidence
from evidence.review import link_evidence_to_reported_concern, review_evidence
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
        name=f"Evidence Record UI User {suffix}",
        email=f"evidence-record-ui-{suffix}@example.com",
        phone_number=f"+234899000{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=(
            datetime(2026, 8, 16, 23, 0, 0) if verified else None
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
        vin=f"W1NEVIDRECORDUI{suffix:02d}",
        current_mileage=15000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"ER-{suffix:03d}-LA",
            mileage_at_transfer=15000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _evidence(
    *,
    car: Car,
    uploader: User,
    suffix: int,
    visibility: str = "client",
    purpose: str = "concern_support",
) -> VehicleEvidence:
    payload = f"evidence-record-ui-{suffix}".encode()
    evidence = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=uploader.id,
        evidence_type="image",
        purpose=purpose,
        source_channel="web",
        visibility=visibility,
        review_status="pending_review",
        storage_provider="test-private",
        storage_state="available",
        object_key=f"evidence/ui/{suffix:032x}.jpg",
        safe_display_name=f"vehicle-evidence-ui-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
        uploaded_at=datetime(2026, 8, 16, 23, 5, 0),
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def _concern(*, car: Car, reporter: User, suffix: int) -> CarFault:
    concern = CarFault(
        car_id=car.id,
        title=f"Reviewed evidence concern {suffix}",
        category="observation",
        description="Controlled concern for evidence-record UI tests.",
        status="reported",
        reported_by=reporter.id,
        source="client",
        reported_at=datetime(2026, 8, 16, 23, 10, 0),
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


def test_vehicle_page_does_not_render_evidence_record_when_feature_is_off(app, client):
    with app.app_context():
        owner = _user(suffix=1)
        car = _owned_car(owner, suffix=1)
        owner_email = owner.email
        car_id = car.id

    _login(client, owner_email)
    response = client.get(f"/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Reviewed Evidence Record" not in html
    assert "evidence-record.css" not in html


def test_verified_owner_sees_empty_reviewed_evidence_record_when_enabled(app, client):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
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
    assert "Evidence submitted for this vehicle that has completed professional review." in html
    assert "Supporting material that has been reviewed and attached" not in html
    assert "No reviewed evidence has been added yet." in html
    assert "after professional review is completed" in html
    assert "evidence-record.css" in html


def test_owner_vehicle_page_renders_safe_reviewed_evidence_and_concern_link(app, client):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=3)
        advisor = _user(suffix=4, role="admin")
        car = _owned_car(owner, suffix=3)
        evidence = _evidence(car=car, uploader=owner, suffix=3)
        internal = _evidence(
            car=car,
            uploader=advisor,
            suffix=4,
            visibility="internal",
            purpose="assessment_evidence",
        )
        concern = _concern(car=car, reporter=owner, suffix=1)

        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )
        link_evidence_to_reported_concern(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            concern_id=concern.id,
        )
        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=internal.id,
            decision="accepted",
            reason_code="sufficient_for_record",
        )

        owner_email = owner.email
        car_id = car.id
        concern_title = concern.title
        secret_object_key = evidence.object_key
        secret_sha256 = evidence.sha256

    _login(client, owner_email)
    response = client.get(f"/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Reviewed Evidence Record" in html
    assert "Reported concern support" in html
    assert "Accepted" in html
    assert "Reviewed and accepted into the vehicle care record." in html
    assert concern_title in html
    assert "Associated care record" in html
    assert "Submitted by you" in html
    assert "Assessment evidence" not in html

    for forbidden in (
        secret_object_key,
        secret_sha256,
        "storage_provider",
        "storage_state",
        "Review basis:",
        "Uploader ID",
    ):
        assert forbidden not in html


def test_unverified_owner_does_not_receive_evidence_record_in_vehicle_html(app, client):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=5, verified=False)
        advisor = _user(suffix=6, role="admin")
        car = _owned_car(owner, suffix=4)
        evidence = _evidence(car=car, uploader=owner, suffix=5)
        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )
        owner_email = owner.email
        car_id = car.id

    _login(client, owner_email)
    response = client.get(f"/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Reviewed Evidence Record" not in html
    assert "evidence-record.css" not in html


def test_advisor_vehicle_page_shows_governance_context_without_storage_details(app, client):
    app.config["EVIDENCE_TIMELINE_ENABLED"] = True
    with app.app_context():
        owner = _user(suffix=7)
        advisor = _user(suffix=8, role="admin")
        car = _owned_car(owner, suffix=5)
        evidence = _evidence(
            car=car,
            uploader=advisor,
            suffix=6,
            visibility="advisor",
            purpose="assessment_evidence",
        )
        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="rejected",
            reason_code="not_relevant",
        )
        advisor_email = advisor.email
        car_id = car.id
        secret_object_key = evidence.object_key
        secret_sha256 = evidence.sha256

    _login(client, advisor_email)
    response = client.get(f"/admin/cars/{car_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Reviewed Evidence Record" in html
    assert "Evidence submitted for this vehicle that has completed professional review." in html
    assert "Assessment evidence" in html
    assert "Not used" in html
    assert "Visibility: Advisor" in html
    assert "Review basis: Not Relevant" in html
    assert secret_object_key not in html
    assert secret_sha256 not in html
    assert "storage_provider" not in html
    assert "storage_state" not in html
