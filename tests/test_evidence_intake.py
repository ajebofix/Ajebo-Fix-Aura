from __future__ import annotations

from datetime import datetime
from io import BytesIO
import re

import pytest
from PIL import Image

from evidence.image_sanitizer import EvidenceImageValidationError
from evidence.intake import (
    EvidenceIntakeAccessError,
    EvidenceIntakeError,
    create_image_evidence,
)
from evidence.models import VehicleEvidence
from evidence.storage import (
    EvidenceStorageError,
    StoredEvidenceObject,
)
from extensions import db
from models import Car, CarDriver, CarOwnership, User


PASSWORD = "Password123"


class RecordingStorageProvider:
    provider_name = "test-private"

    def __init__(self):
        self.put_calls: list[dict[str, object]] = []
        self.deleted: list[str] = []
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, *, object_key: str, payload: bytes, content_type: str):
        self.put_calls.append(
            {
                "object_key": object_key,
                "payload": payload,
                "content_type": content_type,
            }
        )
        self.objects[object_key] = payload
        return StoredEvidenceObject(
            provider=self.provider_name,
            object_key=object_key,
            byte_size=len(payload),
            etag="test-etag",
        )

    def delete(self, *, object_key: str) -> None:
        self.deleted.append(object_key)
        self.objects.pop(object_key, None)

    def exists(self, *, object_key: str) -> bool:
        return object_key in self.objects


class FailingStorageProvider(RecordingStorageProvider):
    def put_bytes(self, *, object_key: str, payload: bytes, content_type: str):
        raise EvidenceStorageError("synthetic private storage failure")


class ExplodingStream:
    def read(self, *_args, **_kwargs):
        raise AssertionError("untrusted bytes were read before authority was resolved")


def _create_user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Evidence User {suffix}",
        email=f"evidence-user-{suffix}@example.com",
        phone_number=f"+234800001{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 16, 12, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _create_owned_car(owner: User, *, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1NEVIDINTAKE{suffix:04d}",
        current_mileage=12000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"EV-{suffix:03d}-LA",
            mileage_at_transfer=12000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _jpeg_with_metadata() -> bytes:
    image = Image.new("RGB", (64, 40), (120, 50, 30))
    exif = Image.Exif()
    exif[0x010E] = "private source description"
    output = BytesIO()
    image.save(output, format="JPEG", quality=92, exif=exif)
    image.close()
    return output.getvalue()


def test_owner_upload_stores_only_sanitized_private_image(app):
    with app.app_context():
        owner = _create_user(suffix=1)
        car = _create_owned_car(owner, suffix=1)
        raw = _jpeg_with_metadata()
        provider = RecordingStorageProvider()

        result = create_image_evidence(
            user_id=owner.id,
            car_id=car.id,
            file_stream=BytesIO(raw),
            declared_content_type="image/jpeg",
            purpose="concern_support",
            consent_confirmed=True,
            storage_provider=provider,
        )

        assert result.storage_state == "available"
        assert result.review_status == "pending_review"
        assert result.visibility == "client"
        assert len(provider.put_calls) == 1

        call = provider.put_calls[0]
        stored_payload = call["payload"]
        assert isinstance(stored_payload, bytes)
        assert stored_payload != raw
        assert call["content_type"] == "image/jpeg"
        assert re.fullmatch(
            r"evidence/[0-9a-f]{2}/[0-9a-f]{32}\.jpg",
            str(call["object_key"]),
        )
        assert "vehicles" not in str(call["object_key"])

        with Image.open(BytesIO(stored_payload)) as stored_image:
            stored_image.load()
            assert len(stored_image.getexif()) == 0

        record = db.session.get(VehicleEvidence, result.evidence_id)
        assert record is not None
        assert record.storage_state == "available"
        assert record.storage_provider == provider.provider_name
        assert record.object_key == call["object_key"]
        assert record.safe_display_name.startswith("vehicle-evidence-")
        assert "private source description" not in record.safe_display_name
        assert record.sha256 == result.sha256
        assert record.consent_basis == "explicit_web_upload"
        assert record.lawful_purpose == "vehicle_care"
        assert record.review_status == "pending_review"


def test_cross_vehicle_request_fails_before_reading_upload_bytes(app):
    with app.app_context():
        owner = _create_user(suffix=2)
        outsider = _create_user(suffix=3)
        car = _create_owned_car(owner, suffix=2)

        with pytest.raises(EvidenceIntakeAccessError, match="do not have access"):
            create_image_evidence(
                user_id=outsider.id,
                car_id=car.id,
                file_stream=ExplodingStream(),
                declared_content_type="image/jpeg",
                purpose="concern_support",
                consent_confirmed=True,
                storage_provider=RecordingStorageProvider(),
            )

        assert VehicleEvidence.query.count() == 0


def test_driver_is_limited_to_operational_evidence_purposes(app):
    with app.app_context():
        owner = _create_user(suffix=4)
        driver = _create_user(suffix=5, role="driver")
        car = _create_owned_car(owner, suffix=3)
        db.session.add(CarDriver(car_id=car.id, user_id=driver.id, is_active=True))
        db.session.commit()

        with pytest.raises(EvidenceIntakeAccessError, match="purpose"):
            create_image_evidence(
                user_id=driver.id,
                car_id=car.id,
                file_stream=BytesIO(_jpeg_with_metadata()),
                declared_content_type="image/jpeg",
                purpose="assessment_evidence",
                consent_confirmed=True,
                storage_provider=RecordingStorageProvider(),
            )

        accepted = create_image_evidence(
            user_id=driver.id,
            car_id=car.id,
            file_stream=BytesIO(_jpeg_with_metadata()),
            declared_content_type="image/jpeg",
            purpose="driver_observation",
            consent_confirmed=True,
            storage_provider=RecordingStorageProvider(),
        )
        assert accepted.visibility == "client"


def test_owner_and_driver_cannot_write_advisor_only_visibility(app):
    with app.app_context():
        owner = _create_user(suffix=6)
        car = _create_owned_car(owner, suffix=4)

        with pytest.raises(EvidenceIntakeAccessError, match="private advisor"):
            create_image_evidence(
                user_id=owner.id,
                car_id=car.id,
                file_stream=BytesIO(_jpeg_with_metadata()),
                declared_content_type="image/jpeg",
                purpose="concern_support",
                consent_confirmed=True,
                requested_visibility="advisor",
                storage_provider=RecordingStorageProvider(),
            )


def test_explicit_consent_is_required_before_file_decode(app):
    with app.app_context():
        owner = _create_user(suffix=7)
        car = _create_owned_car(owner, suffix=5)

        with pytest.raises(EvidenceIntakeAccessError, match="Confirm"):
            create_image_evidence(
                user_id=owner.id,
                car_id=car.id,
                file_stream=ExplodingStream(),
                declared_content_type="image/jpeg",
                purpose="concern_support",
                consent_confirmed=False,
                storage_provider=RecordingStorageProvider(),
            )


def test_mime_validation_happens_before_any_storage_write(app):
    with app.app_context():
        owner = _create_user(suffix=8)
        car = _create_owned_car(owner, suffix=6)
        provider = RecordingStorageProvider()

        with pytest.raises(EvidenceImageValidationError):
            create_image_evidence(
                user_id=owner.id,
                car_id=car.id,
                file_stream=BytesIO(_jpeg_with_metadata()),
                declared_content_type="image/png",
                purpose="concern_support",
                consent_confirmed=True,
                storage_provider=provider,
            )

        assert provider.put_calls == []
        assert VehicleEvidence.query.count() == 0


def test_storage_failure_is_durable_and_not_reviewable_as_available(app):
    with app.app_context():
        owner = _create_user(suffix=9)
        car = _create_owned_car(owner, suffix=7)

        with pytest.raises(EvidenceIntakeError, match="Nothing was accepted"):
            create_image_evidence(
                user_id=owner.id,
                car_id=car.id,
                file_stream=BytesIO(_jpeg_with_metadata()),
                declared_content_type="image/jpeg",
                purpose="concern_support",
                consent_confirmed=True,
                storage_provider=FailingStorageProvider(),
            )

        record = VehicleEvidence.query.one()
        assert record.storage_state == "failed"
        assert record.storage_failure_reason_code == "write_failed"
        assert record.review_status == "pending_review"


def test_only_authenticated_web_channel_is_enabled_in_first_slice(app):
    with app.app_context():
        owner = _create_user(suffix=10)
        car = _create_owned_car(owner, suffix=8)

        with pytest.raises(EvidenceIntakeAccessError, match="web evidence"):
            create_image_evidence(
                user_id=owner.id,
                car_id=car.id,
                file_stream=ExplodingStream(),
                declared_content_type="image/jpeg",
                purpose="concern_support",
                consent_confirmed=True,
                source_channel="whatsapp",
                storage_provider=RecordingStorageProvider(),
            )
