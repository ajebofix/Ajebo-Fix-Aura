from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib

from evidence.models import VehicleEvidence
from evidence.storage import RetrievedEvidenceObject, StoredEvidenceObject
from extensions import db
from models import Car, CarOwnership, User


PASSWORD = "Password123"


@dataclass(frozen=True)
class Identity:
    user_id: int
    email: str
    car_id: int | None = None


class RoutePrivateStore:
    provider_name = "test-private"

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, *, object_key: str, payload: bytes, content_type: str):
        self.objects[object_key] = payload
        return StoredEvidenceObject(provider=self.provider_name, object_key=object_key, byte_size=len(payload))

    def get_bytes(self, *, object_key: str, max_bytes: int):
        payload = self.objects[object_key]
        return RetrievedEvidenceObject(provider=self.provider_name, object_key=object_key, payload=payload, byte_size=len(payload))

    def delete(self, *, object_key: str) -> None:
        self.objects.pop(object_key, None)

    def exists(self, *, object_key: str) -> bool:
        return object_key in self.objects


def _create_identity(*, suffix: int, role: str = "user", own_car: bool = True) -> Identity:
    email = f"private-evidence-route-{suffix}@example.com"
    user = User(name=f"Private Evidence Route User {suffix}", email=email, phone_number=f"+234833000{suffix:04d}", role=role, is_active=True, email_verified_at=datetime(2026, 8, 16, 12, 0, 0))
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    car_id = None
    if own_car:
        car = Car(brand="Mercedes-Benz", model="E 450", year=2024, vin=f"W1NPRIVROUTE{suffix:04d}", current_mileage=6000)
        db.session.add(car)
        db.session.flush()
        db.session.add(CarOwnership(user_id=user.id, car_id=car.id, plate_number=f"PR-{suffix:03d}-LA", mileage_at_transfer=6000, is_active=True))
        car_id = car.id
    user_id = user.id
    db.session.commit()
    return Identity(user_id=user_id, email=email, car_id=car_id)


def _create_evidence(identity: Identity, store: RoutePrivateStore, *, suffix: int):
    assert identity.car_id is not None
    payload = f"private-route-object-{suffix}".encode()
    key = f"evidence/{suffix:02x}/{suffix:032x}.jpg"
    evidence = VehicleEvidence(car_id=identity.car_id, uploaded_by_user_id=identity.user_id, evidence_type="image", purpose="concern_support", source_channel="web", visibility="client", review_status="pending_review", storage_provider=store.provider_name, storage_state="available", object_key=key, safe_display_name=f"vehicle-evidence-{suffix}.jpg", content_type="image/jpeg", byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), consent_basis="explicit_web_upload", lawful_purpose="vehicle_care", uploaded_at=datetime(2026, 8, 16, 13, 0, 0))
    db.session.add(evidence)
    db.session.commit()
    store.objects[key] = payload
    return evidence, payload


def _csrf(client) -> str:
    with client.session_transaction() as flask_session:
        return str(flask_session["_csrf_token"])


def _login(client, email: str) -> None:
    client.get("/auth/login")
    response = client.post("/auth/login", data={"email": email, "password": PASSWORD, "csrf_token": _csrf(client)}, follow_redirects=False)
    assert response.status_code in {302, 303}
    client.get("/")
    _csrf(client)


def _enable_retrieval(app, store: RoutePrivateStore) -> None:
    app.config["EVIDENCE_RETRIEVAL_ENABLED"] = True
    app.config["EVIDENCE_RETRIEVAL_GRANT_SECONDS"] = "60"
    app.extensions["evidence_storage_provider"] = store


def test_private_retrieval_is_disabled_by_default(app, client):
    identity = _create_identity(suffix=1)
    store = RoutePrivateStore()
    evidence, _payload = _create_evidence(identity, store, suffix=1)
    _login(client, identity.email)
    response = client.post(f"/evidence/{evidence.id}/grant", data={"csrf_token": _csrf(client)})
    assert response.status_code == 503
    assert response.get_json()["error"] == "evidence_retrieval_unavailable"


def test_owner_retrieves_private_bytes_through_post_only_grant_flow(app, client):
    identity = _create_identity(suffix=2)
    store = RoutePrivateStore()
    evidence, expected = _create_evidence(identity, store, suffix=2)
    _enable_retrieval(app, store)
    _login(client, identity.email)
    grant_response = client.post(f"/evidence/{evidence.id}/grant", data={"csrf_token": _csrf(client)})
    assert grant_response.status_code == 201
    grant_payload = grant_response.get_json()
    assert grant_payload["evidence_id"] == evidence.id
    assert grant_payload["expires_in_seconds"] == 60
    assert "object_key" not in str(grant_payload)
    assert "r2" not in str(grant_payload).lower()
    content_response = client.post(grant_payload["content_endpoint"], data={"csrf_token": _csrf(client), "grant_token": grant_payload["grant_token"]})
    assert content_response.status_code == 200
    assert content_response.data == expected
    assert content_response.mimetype == "image/jpeg"
    assert content_response.headers["X-Aura-Evidence-Review-Status"] == "pending_review"
    assert content_response.headers["X-Aura-Evidence-Unreviewed"] == "true"
    assert "attachment" in content_response.headers["Content-Disposition"]
    assert content_response.headers["Cache-Control"] == "no-store"


def test_retrieval_grant_policy_is_required_when_feature_enabled(app, client):
    identity = _create_identity(suffix=3)
    store = RoutePrivateStore()
    evidence, _payload = _create_evidence(identity, store, suffix=3)
    app.config["EVIDENCE_RETRIEVAL_ENABLED"] = True
    app.config["EVIDENCE_RETRIEVAL_GRANT_SECONDS"] = None
    app.extensions["evidence_storage_provider"] = store
    _login(client, identity.email)
    response = client.post(f"/evidence/{evidence.id}/grant", data={"csrf_token": _csrf(client)})
    assert response.status_code == 503
    assert response.get_json()["error"] == "evidence_retrieval_configuration_unavailable"


def test_grant_cannot_cross_accounts_or_vehicles(app, client):
    owner = _create_identity(suffix=4)
    other = _create_identity(suffix=5)
    store = RoutePrivateStore()
    evidence, _payload = _create_evidence(owner, store, suffix=4)
    _enable_retrieval(app, store)
    _login(client, other.email)
    response = client.post(f"/evidence/{evidence.id}/grant", data={"csrf_token": _csrf(client)})
    assert response.status_code == 403
    assert response.get_json() == {"error": "evidence_access_denied"}


def test_owner_cannot_call_advisor_deletion_route(app, client):
    identity = _create_identity(suffix=6)
    store = RoutePrivateStore()
    evidence, _payload = _create_evidence(identity, store, suffix=6)
    app.config["EVIDENCE_ADVISOR_DELETION_ENABLED"] = True
    app.extensions["evidence_storage_provider"] = store
    _login(client, identity.email)
    response = client.post(f"/evidence/{evidence.id}/delete", data={"csrf_token": _csrf(client), "reason_code": "invalid_upload"})
    assert response.status_code == 403
    assert response.get_json() == {"error": "evidence_access_denied"}
    assert evidence.object_key in store.objects


def test_advisor_deletion_route_returns_tombstone_not_object_identifier(app):
    owner = _create_identity(suffix=7)
    advisor = _create_identity(suffix=8, role="admin", own_car=False)
    store = RoutePrivateStore()
    evidence, _payload = _create_evidence(owner, store, suffix=7)
    app.config["EVIDENCE_ADVISOR_DELETION_ENABLED"] = True
    app.extensions["evidence_storage_provider"] = store
    with app.test_client() as advisor_client:
        _login(advisor_client, advisor.email)
        response = advisor_client.post(f"/evidence/{evidence.id}/delete", data={"csrf_token": _csrf(advisor_client), "reason_code": "invalid_upload"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["review_status"] == "deleted"
    assert payload["storage_state"] == "deleted"
    serialized = str(payload).lower()
    assert "object_key" not in serialized
    assert "bucket" not in serialized
    assert evidence.object_key not in store.objects
