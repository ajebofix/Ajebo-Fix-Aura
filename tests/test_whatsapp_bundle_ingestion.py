from __future__ import annotations

from datetime import datetime
from io import BytesIO
import zipfile

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from evidence.models import EvidenceBundleItem, EvidenceExtraction, VehicleEvidence
from evidence.storage import RetrievedEvidenceObject, StoredEvidenceObject
from extensions import db
from historical_ingestion.advisor_analyzer import HistoricalBackgroundResponse
from historical_ingestion.service import (
    HistoricalIngestionAccessError,
    decrypt_extraction_payload,
)
from historical_ingestion.whatsapp_bundle import (
    WhatsAppBundleValidationError,
    advance_whatsapp_bundle_analysis,
    ingest_whatsapp_bundle,
    latest_whatsapp_bundle_extraction,
)
from historical_ingestion.whatsapp_bundle_analyzer import (
    BUNDLE_CANDIDATE_SCHEMA,
    BUNDLE_UNDERSTANDING_INSTRUCTIONS,
    BUNDLE_UNDERSTANDING_SCHEMA,
)
from models import Car, CarOwnership, User


PASSWORD = "Password123"
RETENTION_DAYS = 365


class RecordingStorageProvider:
    provider_name = "test-private"

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, *, object_key: str, payload: bytes, content_type: str):
        self.objects[object_key] = payload
        return StoredEvidenceObject(
            provider=self.provider_name,
            object_key=object_key,
            byte_size=len(payload),
            etag="bundle-test",
        )

    def get_bytes(self, *, object_key: str, max_bytes: int):
        payload = self.objects[object_key]
        assert len(payload) <= max_bytes
        return RetrievedEvidenceObject(
            provider=self.provider_name,
            object_key=object_key,
            payload=payload,
            byte_size=len(payload),
            etag="bundle-test",
        )

    def delete(self, *, object_key: str) -> None:
        self.objects.pop(object_key, None)

    def exists(self, *, object_key: str) -> bool:
        return object_key in self.objects


class ExplodingStream:
    def read(self, *_args, **_kwargs):
        raise AssertionError("archive bytes were read before advisor authority")


class FakeBundleAnalyzer:
    provider_name = "fake-bundle"
    media_model = "fake-media"
    transcription_model = "fake-transcribe"

    def __init__(self):
        self.images = 0
        self.audio = 0
        self.videos = 0
        self.started_understanding = 0
        self.started_structuring = 0

    def observe_image(self, *, payload: bytes, content_type: str, source_ref: str):
        self.images += 1
        assert content_type.startswith("image/")
        assert source_ref.startswith("[IMAGE evidence:")
        return {
            "summary": "Photo shows the GL 450 at the workshop.",
            "observations": ["Vehicle exterior is visible."],
            "visible_text": "",
            "uncertainties": ["The photo alone does not establish repair completion."],
        }

    def transcribe_audio(self, *, payload: bytes, filename: str, content_type: str):
        self.audio += 1
        assert payload
        assert filename
        assert content_type.startswith("audio/")
        return "Client says the electrical interruption has not returned."

    def observe_video_frames(self, *, frames: list[bytes], transcript: str, source_ref: str):
        self.videos += 1
        assert frames
        assert source_ref.startswith("[VIDEO evidence:")
        return {
            "summary": "Video shows the vehicle sitting level after the work.",
            "observations": ["Rear suspension appears level in the sampled frames."],
            "visible_text": "",
            "uncertainties": [
                "Representative frames do not prove every replaced component."
            ],
        }

    def start_bundle_understanding_background(
        self,
        *,
        corpus: str,
        trusted_vehicle_context: dict,
    ):
        self.started_understanding += 1
        assert "Electrical symptom did not return after work." in corpus
        assert "[AUDIO evidence:" in corpus
        assert "[VIDEO evidence:" in corpus
        assert trusted_vehicle_context["audience"] == "Ajebo Fix professional advisor"
        return HistoricalBackgroundResponse(
            response_id="bundle-understanding",
            status="queued",
            model="fake-bundle-model",
        )

    def retrieve_background(self, response_id: str):
        if response_id == "bundle-understanding":
            return HistoricalBackgroundResponse(
                response_id=response_id,
                status="completed",
                model="fake-bundle-model",
                payload={
                    "document": {
                        "document_type": "whatsapp_case_bundle",
                        "title": "Client WhatsApp vehicle-care case",
                        "reference": None,
                        "job_reference": "JOB-2026-002",
                        "sow_reference": "SOW-2026-002",
                        "document_date": None,
                        "client_name": "Oluwasanmi Ademola Samuel",
                        "vehicle_description": "2014 Mercedes-Benz GL 450",
                        "vin": None,
                        "plate_number": "JJJ926HX",
                    },
                    "advisor_narrative": (
                        "The chat and media document the concern, work discussion "
                        "and post-work observations."
                    ),
                    "chronology": [],
                    "facts": [],
                    "ambiguities": [],
                    "advisor_suggestions": [
                        "Confirm installed parts against advisor completion evidence."
                    ],
                    "case_focus": (
                        "Intermittent electrical interruption, completed work discussion "
                        "and whether the symptom recurred afterward."
                    ),
                    "priority_threads": [
                        {
                            "title": "Post-work electrical outcome",
                            "priority": "high",
                            "reason": (
                                "The post-work client update establishes whether the "
                                "original symptom recurred."
                            ),
                            "status": "outcome_followup",
                            "source_refs": ["CHAT m000002"],
                        }
                    ],
                    "supporting_context": [
                        {
                            "summary": "Workshop media supports chronology.",
                            "why_it_matters": (
                                "It places the vehicle at the workshop around the care episode."
                            ),
                            "source_refs": ["IMAGE evidence:2"],
                        }
                    ],
                    "low_relevance_context": [
                        {
                            "summary": "Greetings and acknowledgements.",
                            "reason": "They do not alter the vehicle-care chronology.",
                        }
                    ],
                },
            )
        if response_id == "bundle-structuring":
            return HistoricalBackgroundResponse(
                response_id=response_id,
                status="completed",
                model="fake-bundle-model",
                payload={
                    "document": {
                        "document_type": "whatsapp_case_bundle",
                        "title": "Client WhatsApp vehicle-care case",
                        "reference": None,
                        "job_reference": "JOB-2026-002",
                        "sow_reference": "SOW-2026-002",
                        "document_date": None,
                        "client_name": "Oluwasanmi Ademola Samuel",
                        "vehicle_description": "2014 Mercedes-Benz GL 450",
                        "vin": None,
                        "plate_number": "JJJ926HX",
                    },
                    "rina_summary": (
                        "The bundle provides longitudinal evidence across chat and media."
                    ),
                    "advisor_suggestions": [
                        "Review completion facts before applying durable history."
                    ],
                    "case_focus": (
                        "Intermittent electrical interruption, completed work discussion "
                        "and whether the symptom recurred afterward."
                    ),
                    "priority_threads": [
                        {
                            "title": "Post-work electrical outcome",
                            "priority": "high",
                            "reason": (
                                "The post-work client update establishes whether the "
                                "original symptom recurred."
                            ),
                            "status": "outcome_followup",
                            "source_refs": ["CHAT m000002"],
                        }
                    ],
                    "supporting_context": [
                        {
                            "summary": "Workshop media supports chronology.",
                            "why_it_matters": (
                                "It places the vehicle at the workshop around the care episode."
                            ),
                            "source_refs": ["IMAGE evidence:2"],
                        }
                    ],
                    "low_relevance_context": [
                        {
                            "summary": "Greetings and acknowledgements.",
                            "reason": "They do not alter the vehicle-care chronology.",
                        }
                    ],
                    "candidates": [
                        {
                            "category": "outcome",
                            "state": "outcome_observed",
                            "title": "Electrical symptom did not return",
                            "detail": (
                                "The client reported that the electrical interruption "
                                "did not return after the work."
                            ),
                            "occurred_at": None,
                            "source_pages": [],
                            "source_fact_ids": ["CHAT m000002"],
                            "source_excerpt": (
                                "Electrical symptom did not return after work."
                            ),
                            "confidence": 0.98,
                            "confidence_reason": (
                                "The post-work chat statement is explicit."
                            ),
                            "suggested_destination": "treatment_outcome",
                            "outcome_direction": "resolved",
                            "advisor_attention": (
                                "Retain this as a reported post-work outcome."
                            ),
                            "action": {
                                "kind": "other_intervention",
                                "component_name": None,
                                "component_location": None,
                                "component_condition": "not_applicable",
                                "quantity": None,
                                "odometer_km": None,
                            },
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected response id {response_id}")

    def start_bundle_structuring_background(
        self,
        *,
        understanding: dict,
        trusted_vehicle_context: dict,
    ):
        self.started_structuring += 1
        assert understanding["document"]["document_type"] == "whatsapp_case_bundle"
        assert trusted_vehicle_context["audience"] == "Ajebo Fix professional advisor"
        return HistoricalBackgroundResponse(
            response_id="bundle-structuring",
            status="queued",
            model="fake-bundle-model",
        )


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Bundle User {suffix}",
        email=f"bundle-{suffix}@example.com",
        phone_number=f"+23480770{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 9, 20, 12, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _owned_car(owner: User, *, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GL 450",
        year=2014,
        vin=f"4JG166BUND{suffix:07d}",
        current_mileage=None,
        vehicle_identity_source="manual",
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"BND-{suffix:03d}-LA",
            mileage_at_transfer=None,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _image_bytes() -> bytes:
    output = BytesIO()
    image = Image.new("RGB", (320, 180), "white")
    image.save(output, format="JPEG", quality=85)
    image.close()
    return output.getvalue()


def _pdf_bytes() -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(50, 800, "JOB-2026-002")
    pdf.drawString(50, 780, "Rear AIRMATIC work discussed.")
    pdf.save()
    return output.getvalue()


def _zip_bytes(*, unsafe_path: bool = False, corrupt_image: bool = False) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        chat_name = "../_chat.txt" if unsafe_path else "_chat.txt"
        archive.writestr(
            chat_name,
            (
                "[08/08/2026, 09:15] Client: The dashboard and screen went off while driving.\n"
                "[14/08/2026, 18:30] Client: Electrical symptom did not return after work.\n"
            ).encode(),
        )
        archive.writestr(
            "IMG-20260814-WA0001.jpg",
            b"not-an-image" if corrupt_image else _image_bytes(),
        )
        archive.writestr("JOB-2026-002.pdf", _pdf_bytes())
        archive.writestr("PTT-20260814-WA0001.opus", b"OggS" + b"\x00" * 64)
        archive.writestr(
            "VID-20260814-WA0001.mp4",
            b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 80,
        )
    return output.getvalue()


def test_non_advisor_is_rejected_before_archive_bytes_are_read(app):
    with app.app_context():
        owner = _user(suffix=1)
        car = _owned_car(owner, suffix=1)

        with pytest.raises(HistoricalIngestionAccessError):
            ingest_whatsapp_bundle(
                user_id=owner.id,
                car_id=car.id,
                file_stream=ExplodingStream(),
                purpose="service_document",
                retention_days=RETENTION_DAYS,
                storage_provider=RecordingStorageProvider(),
            )


def test_whatsapp_bundle_is_safe_private_lineage_and_multimodal_case(
    app,
    monkeypatch,
):
    from historical_ingestion import whatsapp_bundle as bundle_module

    with app.app_context():
        owner = _user(suffix=2)
        advisor = _user(suffix=3, role="admin")
        car = _owned_car(owner, suffix=2)
        storage = RecordingStorageProvider()
        analyzer = FakeBundleAnalyzer()

        monkeypatch.setattr(
            bundle_module,
            "_video_derivatives",
            lambda *_args, **_kwargs: (
                b"ID3" + b"\x00" * 80,
                [_image_bytes()],
            ),
        )

        started = ingest_whatsapp_bundle(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(_zip_bytes()),
            purpose="service_document",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
        )

        assert started.status == "processing"
        assert started.phase == "unpacking"
        archive = db.session.get(VehicleEvidence, started.evidence_id)
        assert archive.evidence_type == "archive"
        assert archive.source_channel == "whatsapp"
        assert archive.visibility == "advisor"
        assert archive.content_type == "application/zip"

        state = advance_whatsapp_bundle_analysis(
            extraction_id=started.extraction_id,
            actor_user_id=advisor.id,
            storage_provider=storage,
            analyzer=analyzer,
        )
        assert state.phase == "preprocessing"

        items = EvidenceBundleItem.query.filter_by(
            bundle_evidence_id=archive.id
        ).order_by(EvidenceBundleItem.member_index.asc()).all()
        assert len(items) == 5
        assert {item.member_kind for item in items} == {
            "transcript",
            "image",
            "document",
            "audio",
            "video",
        }
        assert all(item.child.visibility == "advisor" for item in items)
        assert all(item.child.source_channel == "whatsapp" for item in items)

        manifest = EvidenceExtraction.query.filter_by(
            evidence_id=archive.id,
            extraction_type="archive_manifest",
            status="completed",
        ).one()
        assert "IMG-20260814" not in (manifest.result_ciphertext or "")
        manifest_payload = decrypt_extraction_payload(manifest)
        assert manifest_payload["bundle_type"] == "whatsapp_export"
        assert manifest_payload["supported_member_count"] == 5

        for _ in range(12):
            state = advance_whatsapp_bundle_analysis(
                extraction_id=started.extraction_id,
                actor_user_id=advisor.id,
                storage_provider=storage,
                analyzer=analyzer,
            )
            if state.status == "completed":
                break

        assert state.status == "completed"
        assert state.review_ready is True
        assert state.completed_items == 5
        assert state.total_items == 5
        assert analyzer.images == 1
        assert analyzer.videos == 1
        assert analyzer.audio >= 2  # voice note + extracted video audio
        assert analyzer.started_understanding == 1
        assert analyzer.started_structuring == 1

        final = latest_whatsapp_bundle_extraction(archive.id)
        assert final is not None
        assert final.status == "completed"
        payload = decrypt_extraction_payload(final)
        assert payload["document"]["document_type"] == "whatsapp_case_bundle"
        assert "Intermittent electrical interruption" in payload["case_focus"]
        assert payload["priority_threads"][0]["priority"] == "high"
        assert payload["priority_threads"][0]["status"] == "outcome_followup"
        assert payload["supporting_context"][0]["source_refs"] == ["IMAGE evidence:2"]
        assert payload["low_relevance_context"][0]["reason"]
        candidate = payload["candidates"][0]
        assert candidate["source_verified"] is True
        assert candidate["source_fact_ids"] == ["CHAT m000002"]
        assert candidate["suggested_destination"] == "treatment_outcome"

        assert EvidenceExtraction.query.filter_by(
            evidence_id=next(
                item.child_evidence_id
                for item in items
                if item.member_kind == "audio"
            ),
            extraction_type="transcription",
            status="completed",
        ).count() == 1


def test_identical_whatsapp_zip_reuses_existing_processing_analysis(app):
    with app.app_context():
        owner = _user(suffix=4)
        advisor = _user(suffix=5, role="admin")
        car = _owned_car(owner, suffix=4)
        storage = RecordingStorageProvider()
        payload = _zip_bytes()

        first = ingest_whatsapp_bundle(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(payload),
            purpose="service_document",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
        )
        second = ingest_whatsapp_bundle(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(payload),
            purpose="service_document",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
        )

        assert second.reused_existing is True
        assert second.evidence_id == first.evidence_id
        assert second.extraction_id == first.extraction_id
        assert VehicleEvidence.query.filter_by(
            car_id=car.id,
            evidence_type="archive",
        ).count() == 1


def test_whatsapp_zip_rejects_path_traversal(app):
    with app.app_context():
        owner = _user(suffix=6)
        advisor = _user(suffix=7, role="admin")
        car = _owned_car(owner, suffix=6)

        with pytest.raises(WhatsAppBundleValidationError):
            ingest_whatsapp_bundle(
                user_id=advisor.id,
                car_id=car.id,
                file_stream=BytesIO(_zip_bytes(unsafe_path=True)),
                purpose="service_document",
                retention_days=RETENTION_DAYS,
                storage_provider=RecordingStorageProvider(),
            )


def test_corrupt_non_transcript_media_is_recorded_and_does_not_kill_bundle(app):
    with app.app_context():
        owner = _user(suffix=8)
        advisor = _user(suffix=9, role="admin")
        car = _owned_car(owner, suffix=8)
        storage = RecordingStorageProvider()

        started = ingest_whatsapp_bundle(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(_zip_bytes(corrupt_image=True)),
            purpose="service_document",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
        )
        state = advance_whatsapp_bundle_analysis(
            extraction_id=started.extraction_id,
            actor_user_id=advisor.id,
            storage_provider=storage,
            analyzer=FakeBundleAnalyzer(),
        )

        assert state.status == "processing"
        manifest = EvidenceExtraction.query.filter_by(
            evidence_id=started.evidence_id,
            extraction_type="archive_manifest",
            status="completed",
        ).one()
        payload = decrypt_extraction_payload(manifest)
        rejected = [
            item
            for item in payload["members"]
            if item["original_name"].endswith(".jpg")
        ]
        assert rejected[0]["status"] == "rejected_unsafe"
        assert rejected[0]["child_evidence_id"] is None
        assert EvidenceBundleItem.query.filter_by(
            bundle_evidence_id=started.evidence_id
        ).count() == 4


def test_bundle_schema_requires_contextual_relevance():
    required_understanding = set(BUNDLE_UNDERSTANDING_SCHEMA["required"])
    required_candidates = set(BUNDLE_CANDIDATE_SCHEMA["required"])
    relevance_fields = {
        "case_focus",
        "priority_threads",
        "supporting_context",
        "low_relevance_context",
    }
    assert relevance_fields <= required_understanding
    assert relevance_fields <= required_candidates
    assert "relevance is contextual, never a keyword filter" in BUNDLE_UNDERSTANDING_INSTRUCTIONS
    assert "priority for advisor attention" in BUNDLE_UNDERSTANDING_INSTRUCTIONS
