from __future__ import annotations

from datetime import datetime
from io import BytesIO

from PIL import Image

from evidence.models import VehicleEvidence
from evidence.storage import StoredEvidenceObject
from extensions import db
from models import Car, CarOwnership, User


PASSWORD = "Password123"


class RouteStorageProvider:
    provider_name = "test-private"

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, *, object_key: str, payload: bytes, content_type: str):
        self.objects[object_key] = payload
        return StoredEvidenceObject(
            provider=self.provider_name,
            object_key=object_key,
            byte_size=len(payload),
            etag="route-etag",
        )

    def delete(self, *, object_key: str) -> None:
        self.objects.pop(object_key, None)

    def exists(self, *, object_key: str) -> bool:
        return object_key in self.objects


def _jpeg() -> bytes:
    image = Image.new("RGB", (36, 24), (40, 80, 120))
    output = BytesIO()
    image.save(output, format="JPEG", quality=90)
    image.close()
    return output.getvalue()


def _create_owner(*, suffix: int, verified: bool = True) -> tuple[User, Car]:
    user = User(
        name=f"Route Owner {suffix}",
        email=f"evidence-route-{suffix}@example.com",
        phone_number=f"+234811000{suffix:04d}",
        role="user",
        is_active=True,
        email_verified_at=(datetime(2026, 8, 16, 12, 0, 0) if verified else None),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()

    car = Car(
        brand="Mercedes-Benz",
        model="GLC 300",
        year=2024,
        vin=f"W1NEVIDROUTE{suffix:04d}",
        current_mileage=8000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=user.id,
            car_id=car.id,
            plate_number=f"ER-{suffix:03d}-LA",
            mileage_at_transfer=8000,
            is_active=True,
        )
    )
    db.session.commit()
    return user, car


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
    # Login clears the old session token. A safe request seeds the new token.
    client.get("/")
    _csrf(client)


def _upload_payload(client, *, include_csrf: bool = True):
    data: dict[str, object] = {
        "purpose": "concern_support",
        "consent_confirmed": "on",
        "image": (BytesIO(_jpeg()), "../../client-original-name.jpg", "image/jpeg"),
    }
    if include_csrf:
        data["csrf_token"] = _csrf(client)
    return data


def test_image_intake_is_feature_disabled_by_default(app, client):
    with app.app_context():
        owner, car = _create_owner(suffix=1)
    _login(client, owner)

    response = client.post(
        f"/evidence/vehicles/{car.id}/images",
        data={"csrf_token": _csrf(client)},
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "evidence_intake_unavailable"


def test_unverified_account_cannot_upload_evidence(app, client):
    with app.app_context():
        owner, car = _create_owner(suffix=2, verified=False)
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    _login(client, owner)

    response = client.post(
        f"/evidence/vehicles/{car.id}/images",
        data={"csrf_token": _csrf(client)},
        content_type="multipart/form-data",
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "email_verification_required"


def test_global_csrf_protection_applies_to_multipart_evidence_route(app, client):
    with app.app_context():
        owner, car = _create_owner(suffix=3)
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    app.extensions["evidence_storage_provider"] = RouteStorageProvider()
    _login(client, owner)

    response = client.post(
        f"/evidence/vehicles/{car.id}/images",
        data=_upload_payload(client, include_csrf=False),
        content_type="multipart/form-data",
    )

    assert response.status_code == 400


def test_verified_owner_upload_returns_minimized_pending_review_contract(app, client):
    with app.app_context():
        owner, car = _create_owner(suffix=4)
    provider = RouteStorageProvider()
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    app.extensions["evidence_storage_provider"] = provider
    _login(client, owner)

    response = client.post(
        f"/evidence/vehicles/{car.id}/images",
        data=_upload_payload(client),
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    evidence = payload["evidence"]
    assert evidence["car_id"] == car.id
    assert evidence["type"] == "image"
    assert evidence["purpose"] == "concern_support"
    assert evidence["visibility"] == "client"
    assert evidence["review_status"] == "pending_review"
    assert evidence["storage_state"] == "available"
    assert "diagnosis" in payload["message"].lower()

    serialized = str(payload).lower()
    for prohibited in (
        "object_key",
        "r2_bucket",
        "access_key",
        "secret",
        "sha256",
        "client-original-name",
    ):
        assert prohibited not in serialized

    with app.app_context():
        record = VehicleEvidence.query.one()
        assert record.object_key in provider.objects
        assert "client-original-name" not in record.object_key
        assert "client-original-name" not in record.safe_display_name


def test_route_hides_cross_vehicle_existence_behind_generic_access_error(app, client):
    with app.app_context():
        owner, _owned_car = _create_owner(suffix=5)
        other_owner, other_car = _create_owner(suffix=6)
        assert other_owner.id != owner.id
    app.config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = True
    app.extensions["evidence_storage_provider"] = RouteStorageProvider()
    _login(client, owner)

    response = client.post(
        f"/evidence/vehicles/{other_car.id}/images",
        data=_upload_payload(client),
        content_type="multipart/form-data",
    )

    assert response.status_code == 403
    payload = response.get_json()
    assert payload == {
        "error": "evidence_access_denied",
        "message": "This evidence action is not available for the selected vehicle.",
    }
