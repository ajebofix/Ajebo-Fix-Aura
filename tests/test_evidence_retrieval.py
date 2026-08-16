from __future__ import annotations

from datetime import datetime
import hashlib

import pytest

from evidence.models import EvidenceLink, VehicleEvidence
from evidence.retrieval import (
    EvidenceDeletionConflict,
    EvidenceNotAvailableError,
    EvidenceRetrievalAccessError,
    create_retrieval_grant,
    delete_evidence,
    reconcile_evidence_storage,
    retrieve_private_content,
)
from evidence.storage import (
    EvidenceStorageError,
    RetrievedEvidenceObject,
    StoredEvidenceObject,
)
from extensions import db
from models import Car, CarDriver, CarOwnership, User


SECRET = "retrieval-test-secret"
GRANT_SECONDS = 60
PASSWORD = "Password123"


class MemoryPrivateStore:
    provider_name = "test-private"

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.read_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.fail_delete = False

    def put_bytes(self, *, object_key: str, payload: bytes, content_type: str):
        self.objects[object_key] = payload
        return StoredEvidenceObject(provider=self.provider_name, object_key=object_key, byte_size=len(payload))

    def get_bytes(self, *, object_key: str, max_bytes: int):
        self.read_calls.append(object_key)
        if object_key not in self.objects:
            raise EvidenceStorageError("missing")
        payload = self.objects[object_key]
        if len(payload) > max_bytes:
            raise EvidenceStorageError("oversized")
        return RetrievedEvidenceObject(provider=self.provider_name, object_key=object_key, payload=payload, byte_size=len(payload))

    def delete(self, *, object_key: str) -> None:
        self.delete_calls.append(object_key)
        if self.fail_delete:
            raise EvidenceStorageError("synthetic delete failure")
        self.objects.pop(object_key, None)

    def exists(self, *, object_key: str) -> bool:
        return object_key in self.objects


def _create_user(*, suffix: int, role: str = "user") -> User:
    user = User(name=f"Evidence Retrieval User {suffix}", email=f"evidence-retrieval-{suffix}@example.com", phone_number=f"+234822000{suffix:04d}", role=role, is_active=True, email_verified_at=datetime(2026, 8, 16, 12, 0, 0))
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _create_owned_car(owner: User, *, suffix: int) -> Car:
    car = Car(brand="Mercedes-Benz", model="GLE 450", year=2024, vin=f"W1NEVIDRETRIEVE{suffix:03d}", current_mileage=10000)
    db.session.add(car)
    db.session.flush()
    db.session.add(CarOwnership(user_id=owner.id, car_id=car.id, plate_number=f"RV-{suffix:03d}-LA", mileage_at_transfer=10000, is_active=True))
    db.session.commit()
    return car


def _create_evidence(*, car: Car, uploader: User, store: MemoryPrivateStore, suffix: int, visibility: str = "client", review_status: str = "pending_review", payload: bytes | None = None) -> VehicleEvidence:
    object_payload = payload or f"sanitized-evidence-{suffix}".encode()
    object_key = f"evidence/{suffix:02x}/{suffix:032x}.jpg"
    evidence = VehicleEvidence(car_id=car.id, uploaded_by_user_id=uploader.id, evidence_type="image", purpose="concern_support", source_channel="web", visibility=visibility, review_status=review_status, storage_provider=store.provider_name, storage_state="available", object_key=object_key, safe_display_name=f"vehicle-evidence-{suffix}.jpg", content_type="image/jpeg", byte_size=len(object_payload), sha256=hashlib.sha256(object_payload).hexdigest(), consent_basis="explicit_web_upload", lawful_purpose="vehicle_care", uploaded_at=datetime(2026, 8, 16, 12, suffix % 60, 0))
    db.session.add(evidence)
    db.session.commit()
    store.objects[object_key] = object_payload
    return evidence


def test_owner_gets_short_lived_grant_and_private_verified_content(app):
    with app.app_context():
        owner = _create_user(suffix=1)
        car = _create_owned_car(owner, suffix=1)
        store = MemoryPrivateStore()
        evidence = _create_evidence(car=car, uploader=owner, store=store, suffix=1)
        grant = create_retrieval_grant(user_id=owner.id, evidence_id=evidence.id, secret_key=SECRET, grant_seconds=GRANT_SECONDS)
        assert grant.evidence_id == evidence.id
        assert grant.expires_in_seconds == GRANT_SECONDS
        assert grant.token
        content = retrieve_private_content(user_id=owner.id, evidence_id=evidence.id, token=grant.token, secret_key=SECRET, grant_seconds=GRANT_SECONDS, storage_provider=store)
        assert content.payload == store.objects[evidence.object_key]
        assert content.content_type == "image/jpeg"
        assert content.unreviewed is True
        assert store.read_calls == [evidence.object_key]


def test_owner_cannot_retrieve_advisor_visibility(app):
    with app.app_context():
        owner = _create_user(suffix=2)
        advisor = _create_user(suffix=3, role="admin")
        car = _create_owned_car(owner, suffix=2)
        store = MemoryPrivateStore()
        evidence = _create_evidence(car=car, uploader=advisor, store=store, suffix=2, visibility="advisor")
        with pytest.raises(EvidenceRetrievalAccessError):
            create_retrieval_grant(user_id=owner.id, evidence_id=evidence.id, secret_key=SECRET, grant_seconds=GRANT_SECONDS)


def test_driver_can_only_retrieve_own_client_evidence(app):
    with app.app_context():
        owner = _create_user(suffix=4)
        driver_one = _create_user(suffix=5, role="driver")
        driver_two = _create_user(suffix=6, role="driver")
        car = _create_owned_car(owner, suffix=3)
        db.session.add_all([CarDriver(car_id=car.id, user_id=driver_one.id, is_active=True), CarDriver(car_id=car.id, user_id=driver_two.id, is_active=True)])
        db.session.commit()
        store = MemoryPrivateStore()
        own = _create_evidence(car=car, uploader=driver_one, store=store, suffix=3)
        other = _create_evidence(car=car, uploader=driver_two, store=store, suffix=4)
        assert create_retrieval_grant(user_id=driver_one.id, evidence_id=own.id, secret_key=SECRET, grant_seconds=GRANT_SECONDS).token
        with pytest.raises(EvidenceRetrievalAccessError):
            create_retrieval_grant(user_id=driver_one.id, evidence_id=other.id, secret_key=SECRET, grant_seconds=GRANT_SECONDS)


def test_relationship_revocation_invalidates_existing_grant_before_storage_read(app):
    with app.app_context():
        owner = _create_user(suffix=7)
        driver = _create_user(suffix=8, role="driver")
        car = _create_owned_car(owner, suffix=4)
        assignment = CarDriver(car_id=car.id, user_id=driver.id, is_active=True)
        db.session.add(assignment)
        db.session.commit()
        store = MemoryPrivateStore()
        evidence = _create_evidence(car=car, uploader=driver, store=store, suffix=5)
        grant = create_retrieval_grant(user_id=driver.id, evidence_id=evidence.id, secret_key=SECRET, grant_seconds=GRANT_SECONDS)
        assignment.is_active = False
        db.session.commit()
        with pytest.raises(EvidenceRetrievalAccessError):
            retrieve_private_content(user_id=driver.id, evidence_id=evidence.id, token=grant.token, secret_key=SECRET, grant_seconds=GRANT_SECONDS, storage_provider=store)
        assert store.read_calls == []


def test_grant_is_bound_to_authenticated_user_and_evidence(app):
    with app.app_context():
        owner = _create_user(suffix=9)
        other_owner = _create_user(suffix=10)
        car = _create_owned_car(owner, suffix=5)
        other_car = _create_owned_car(other_owner, suffix=6)
        store = MemoryPrivateStore()
        evidence = _create_evidence(car=car, uploader=owner, store=store, suffix=6)
        other = _create_evidence(car=other_car, uploader=other_owner, store=store, suffix=7)
        grant = create_retrieval_grant(user_id=owner.id, evidence_id=evidence.id, secret_key=SECRET, grant_seconds=GRANT_SECONDS)
        with pytest.raises(EvidenceRetrievalAccessError):
            retrieve_private_content(user_id=other_owner.id, evidence_id=other.id, token=grant.token, secret_key=SECRET, grant_seconds=GRANT_SECONDS, storage_provider=store)
        assert store.read_calls == []


def test_integrity_mismatch_fails_closed_and_marks_storage_failed(app):
    with app.app_context():
        owner = _create_user(suffix=11)
        car = _create_owned_car(owner, suffix=7)
        store = MemoryPrivateStore()
        evidence = _create_evidence(car=car, uploader=owner, store=store, suffix=8)
        grant = create_retrieval_grant(user_id=owner.id, evidence_id=evidence.id, secret_key=SECRET, grant_seconds=GRANT_SECONDS)
        store.objects[evidence.object_key] = b"different-but-same-size"[: evidence.byte_size]
        with pytest.raises(EvidenceNotAvailableError, match="integrity"):
            retrieve_private_content(user_id=owner.id, evidence_id=evidence.id, token=grant.token, secret_key=SECRET, grant_seconds=GRANT_SECONDS, storage_provider=store)
        db.session.refresh(evidence)
        assert evidence.storage_state == "failed"
        assert evidence.storage_failure_reason_code == "retrieval_integrity_mismatch"


def test_owner_cannot_delete_evidence_in_first_deletion_slice(app):
    with app.app_context():
        owner = _create_user(suffix=12)
        car = _create_owned_car(owner, suffix=8)
        store = MemoryPrivateStore()
        evidence = _create_evidence(car=car, uploader=owner, store=store, suffix=9)
        with pytest.raises(EvidenceRetrievalAccessError, match="advisor"):
            delete_evidence(user_id=owner.id, evidence_id=evidence.id, reason_code="invalid_upload", storage_provider=store)
        assert evidence.object_key in store.objects


def test_advisor_deletion_removes_private_object_and_preserves_tombstone(app):
    with app.app_context():
        owner = _create_user(suffix=13)
        advisor = _create_user(suffix=14, role="admin")
        car = _create_owned_car(owner, suffix=9)
        store = MemoryPrivateStore()
        evidence = _create_evidence(car=car, uploader=owner, store=store, suffix=10)
        result = delete_evidence(user_id=advisor.id, evidence_id=evidence.id, reason_code="invalid_upload", storage_provider=store)
        assert result.storage_state == "deleted"
        assert result.storage_delete_pending is False
        assert evidence.object_key not in store.objects
        db.session.refresh(evidence)
        assert evidence.review_status == "deleted"
        assert evidence.deleted_at is not None
        assert evidence.storage_state == "deleted"
        assert evidence.reviewed_by_user_id == advisor.id
        assert evidence.review_reason_code == "invalid_upload"
        with pytest.raises(EvidenceNotAvailableError):
            create_retrieval_grant(user_id=owner.id, evidence_id=evidence.id, secret_key=SECRET, grant_seconds=GRANT_SECONDS)


def test_deletion_failure_leaves_inaccessible_retryable_delete_pending(app):
    with app.app_context():
        owner = _create_user(suffix=15)
        advisor = _create_user(suffix=16, role="admin")
        car = _create_owned_car(owner, suffix=10)
        store = MemoryPrivateStore()
        evidence = _create_evidence(car=car, uploader=owner, store=store, suffix=11)
        store.fail_delete = True
        result = delete_evidence(user_id=advisor.id, evidence_id=evidence.id, reason_code="duplicate", storage_provider=store)
        assert result.storage_delete_pending is True
        db.session.refresh(evidence)
        assert evidence.review_status == "deleted"
        assert evidence.storage_state == "delete_pending"
        with pytest.raises(EvidenceNotAvailableError):
            create_retrieval_grant(user_id=owner.id, evidence_id=evidence.id, secret_key=SECRET, grant_seconds=GRANT_SECONDS)


def test_accepted_or_linked_evidence_cannot_be_immediately_deleted(app):
    with app.app_context():
        owner = _create_user(suffix=17)
        advisor = _create_user(suffix=18, role="admin")
        car = _create_owned_car(owner, suffix=11)
        store = MemoryPrivateStore()
        accepted = _create_evidence(car=car, uploader=owner, store=store, suffix=12, review_status="accepted")
        with pytest.raises(EvidenceDeletionConflict, match="Accepted"):
            delete_evidence(user_id=advisor.id, evidence_id=accepted.id, reason_code="operational_correction", storage_provider=store)
        pending = _create_evidence(car=car, uploader=owner, store=store, suffix=13)
        link = EvidenceLink(evidence_id=pending.id, car_id=car.id, subject_type="reported_concern", subject_id=999001, relationship_type="supports", created_by_user_id=advisor.id)
        db.session.add(link)
        db.session.commit()
        with pytest.raises(EvidenceDeletionConflict, match="Linked"):
            delete_evidence(user_id=advisor.id, evidence_id=pending.id, reason_code="operational_correction", storage_provider=store)


def test_reconciliation_repairs_missing_delete_pending_and_orphan_risk(app):
    with app.app_context():
        owner = _create_user(suffix=19)
        car = _create_owned_car(owner, suffix=12)
        store = MemoryPrivateStore()
        missing = _create_evidence(car=car, uploader=owner, store=store, suffix=14)
        store.objects.pop(missing.object_key)
        delete_pending = _create_evidence(car=car, uploader=owner, store=store, suffix=15)
        delete_pending.review_status = "deleted"
        delete_pending.deleted_at = datetime(2026, 8, 16, 15, 0, 0)
        delete_pending.storage_state = "delete_pending"
        orphan = _create_evidence(car=car, uploader=owner, store=store, suffix=16)
        orphan.storage_state = "failed"
        orphan.storage_failure_reason_code = "finalization_failed_orphan_risk"
        db.session.commit()
        summary = reconcile_evidence_storage(storage_provider=store)
        assert summary.repaired == 3
        assert summary.failed == 0
        db.session.refresh(missing)
        db.session.refresh(delete_pending)
        db.session.refresh(orphan)
        assert missing.storage_state == "failed"
        assert missing.storage_failure_reason_code == "missing_object"
        assert delete_pending.storage_state == "deleted"
        assert delete_pending.object_key not in store.objects
        assert orphan.storage_state == "failed"
        assert orphan.storage_failure_reason_code == "orphan_cleanup_completed"
        assert orphan.object_key not in store.objects
