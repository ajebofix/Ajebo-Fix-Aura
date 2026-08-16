"""Exercise Wave 1.4 image intake on PostgreSQL with an in-process fake store."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402
from evidence.intake import EvidenceIntakeError, create_image_evidence  # noqa: E402
from evidence.models import VehicleEvidence  # noqa: E402
from evidence.storage import (  # noqa: E402
    EvidenceStorageError,
    StoredEvidenceObject,
)
from extensions import db  # noqa: E402
from models import Car, CarOwnership, User  # noqa: E402


class FakePrivateStore:
    provider_name = "ci-private"

    def __init__(self, *, fail_put: bool = False):
        self.fail_put = fail_put
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, *, object_key: str, payload: bytes, content_type: str):
        if self.fail_put:
            raise EvidenceStorageError("synthetic storage failure")
        self.objects[object_key] = payload
        return StoredEvidenceObject(
            provider=self.provider_name,
            object_key=object_key,
            byte_size=len(payload),
            etag="ci-etag",
        )

    def delete(self, *, object_key: str) -> None:
        self.objects.pop(object_key, None)

    def exists(self, *, object_key: str) -> bool:
        return object_key in self.objects


def _jpeg() -> bytes:
    image = Image.new("RGB", (80, 50), (50, 100, 150))
    exif = Image.Exif()
    exif[0x010E] = "must not survive sanitization"
    output = BytesIO()
    image.save(output, format="JPEG", quality=92, exif=exif)
    image.close()
    return output.getvalue()


def main() -> None:
    with app.app_context():
        now = datetime(2026, 8, 16, 18, 0, 0)
        owner = User(
            name="Evidence PostgreSQL Owner",
            email="evidence-postgres@example.com",
            phone_number="+2348000000154",
            role="user",
            is_active=True,
            email_verified_at=now,
        )
        owner.set_password("Password123")
        db.session.add(owner)
        db.session.flush()

        car = Car(
            brand="Mercedes-Benz",
            model="GLE 450",
            year=2024,
            vin="WDDEVIDINTAKE00154",
            current_mileage=15000,
        )
        db.session.add(car)
        db.session.flush()
        db.session.add(
            CarOwnership(
                user_id=owner.id,
                car_id=car.id,
                plate_number="EI-154-LA",
                mileage_at_transfer=15000,
                is_active=True,
            )
        )
        db.session.commit()

        raw = _jpeg()
        store = FakePrivateStore()
        result = create_image_evidence(
            user_id=owner.id,
            car_id=car.id,
            file_stream=BytesIO(raw),
            declared_content_type="image/jpeg",
            purpose="concern_support",
            consent_confirmed=True,
            storage_provider=store,
        )

        record = db.session.get(VehicleEvidence, result.evidence_id)
        if record is None or record.storage_state != "available":
            raise SystemExit("Successful image intake did not finalize on PostgreSQL")
        if record.object_key not in store.objects:
            raise SystemExit("Finalized evidence is missing from the fake private store")
        if store.objects[record.object_key] == raw:
            raise SystemExit("Raw untrusted bytes reached private storage")

        with Image.open(BytesIO(store.objects[record.object_key])) as cleaned:
            cleaned.load()
            if len(cleaned.getexif()) != 0:
                raise SystemExit("Sanitized PostgreSQL intake retained EXIF metadata")

        failed_store = FakePrivateStore(fail_put=True)
        try:
            create_image_evidence(
                user_id=owner.id,
                car_id=car.id,
                file_stream=BytesIO(raw),
                declared_content_type="image/jpeg",
                purpose="concern_support",
                consent_confirmed=True,
                storage_provider=failed_store,
            )
        except EvidenceIntakeError:
            pass
        else:
            raise SystemExit("Synthetic private-storage failure was not surfaced")

        failed_rows = VehicleEvidence.query.filter_by(storage_state="failed").all()
        if len(failed_rows) != 1:
            raise SystemExit("Failed private-storage write was not durably reconciled")
        if failed_rows[0].storage_failure_reason_code != "write_failed":
            raise SystemExit("Failed storage write lacks the expected reason code")

        print("Wave 1.4 image intake verified on PostgreSQL with no external storage call.")


if __name__ == "__main__":
    main()
