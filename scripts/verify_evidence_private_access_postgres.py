"""Verify protected evidence retrieval/deletion/reconciliation on PostgreSQL."""

from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402
from evidence.models import VehicleEvidence  # noqa: E402
from evidence.retrieval import (  # noqa: E402
    create_retrieval_grant,
    delete_evidence,
    reconcile_evidence_storage,
    retrieve_private_content,
)
from evidence.storage import RetrievedEvidenceObject, StoredEvidenceObject  # noqa: E402
from extensions import db  # noqa: E402
from models import Car, CarOwnership, User  # noqa: E402


class FakePrivateStore:
    provider_name = "ci-private"

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, *, object_key: str, payload: bytes, content_type: str):
        self.objects[object_key] = payload
        return StoredEvidenceObject(
            provider=self.provider_name,
            object_key=object_key,
            byte_size=len(payload),
        )

    def get_bytes(self, *, object_key: str, max_bytes: int):
        payload = self.objects[object_key]
        return RetrievedEvidenceObject(
            provider=self.provider_name,
            object_key=object_key,
            payload=payload,
            byte_size=len(payload),
        )

    def delete(self, *, object_key: str) -> None:
        self.objects.pop(object_key, None)

    def exists(self, *, object_key: str) -> bool:
        return object_key in self.objects


def _evidence(*, car_id: int, uploader_id: int, store: FakePrivateStore, suffix: int):
    payload = f"postgres-private-evidence-{suffix}".encode()
    key = f"evidence/{suffix:02x}/{suffix:032x}.jpg"
    row = VehicleEvidence(
        car_id=car_id,
        uploaded_by_user_id=uploader_id,
        evidence_type="image",
        purpose="concern_support",
        source_channel="web",
        visibility="client",
        review_status="pending_review",
        storage_provider=store.provider_name,
        storage_state="available",
        object_key=key,
        safe_display_name=f"vehicle-evidence-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
        uploaded_at=datetime(2026, 8, 16, 18, 0, 0),
    )
    db.session.add(row)
    db.session.commit()
    store.objects[key] = payload
    return row, payload


def main() -> None:
    with app.app_context():
        signing_key = app.config.get("SECRET_KEY")
        if not signing_key:
            raise SystemExit("SECRET_KEY is required for private-access verification")

        now = datetime(2026, 8, 16, 18, 0, 0)
        owner = User(
            name="Private Access Owner",
            email="private-access-owner@example.com",
            phone_number="+2348000000164",
            role="user",
            is_active=True,
            email_verified_at=now,
        )
        owner.set_password("Password123")
        advisor = User(
            name="Private Access Advisor",
            email="private-access-advisor@example.com",
            phone_number="+2348000000165",
            role="admin",
            is_active=True,
            email_verified_at=now,
        )
        advisor.set_password("Password123")
        db.session.add_all([owner, advisor])
        db.session.flush()

        car = Car(
            brand="Mercedes-Benz",
            model="E 450",
            year=2024,
            vin="W1NPRIVATEACCESS164",
            current_mileage=7000,
        )
        db.session.add(car)
        db.session.flush()
        db.session.add(
            CarOwnership(
                user_id=owner.id,
                car_id=car.id,
                plate_number="PA-164-LA",
                mileage_at_transfer=7000,
                is_active=True,
            )
        )
        db.session.commit()

        store = FakePrivateStore()
        row, expected = _evidence(
            car_id=car.id,
            uploader_id=owner.id,
            store=store,
            suffix=1,
        )
        grant = create_retrieval_grant(
            user_id=owner.id,
            evidence_id=row.id,
            secret_key=signing_key,
            grant_seconds=60,
        )
        content = retrieve_private_content(
            user_id=owner.id,
            evidence_id=row.id,
            token=grant.token,
            secret_key=signing_key,
            grant_seconds=60,
            storage_provider=store,
        )
        if content.payload != expected:
            raise SystemExit("Private PostgreSQL evidence retrieval changed object bytes")

        deleted = delete_evidence(
            user_id=advisor.id,
            evidence_id=row.id,
            reason_code="invalid_upload",
            storage_provider=store,
        )
        if deleted.storage_state != "deleted" or row.object_key in store.objects:
            raise SystemExit("Advisor deletion did not remove private object")

        missing, _missing_payload = _evidence(
            car_id=car.id,
            uploader_id=owner.id,
            store=store,
            suffix=2,
        )
        store.objects.pop(missing.object_key)
        summary = reconcile_evidence_storage(storage_provider=store)
        db.session.refresh(missing)
        if summary.repaired < 1 or missing.storage_state != "failed":
            raise SystemExit("Missing private object was not reconciled fail-closed")
        if missing.storage_failure_reason_code != "missing_object":
            raise SystemExit("Missing private object lacks safe reconciliation reason")

        print("Wave 1.4 private evidence access verified on PostgreSQL without network calls.")


if __name__ == "__main__":
    main()
