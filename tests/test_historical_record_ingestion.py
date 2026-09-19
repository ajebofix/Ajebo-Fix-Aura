from __future__ import annotations

from datetime import datetime
from io import BytesIO
import json

import pytest
from reportlab.pdfgen import canvas

from evidence.models import EvidenceExtraction, EvidenceLink, VehicleEvidence
from evidence.storage import StoredEvidenceObject
from extensions import db
from historical_ingestion.application import apply_reviewed_historical_treatment
from historical_ingestion.service import (
    HistoricalIngestionAccessError,
    decrypt_extraction_payload,
    ingest_pdf_document,
    latest_structured_extraction,
    save_advisor_review,
)
from models import Car, CarOwnership, TreatmentPlan, User
from rina.providers.base import RinaProviderRequest, RinaProviderResult
from services.rina_context_resolver import resolve_rina_vehicle_context
from services.rina_provider_context import _reviewed_historical_records
from treatment.models import (
    TreatmentAction,
    TreatmentActionCompletionDetail,
    TreatmentOutcome,
)


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
            etag="historical-test",
        )

    def delete(self, *, object_key: str) -> None:
        self.objects.pop(object_key, None)

    def exists(self, *, object_key: str) -> bool:
        return object_key in self.objects


class FakeHistoricalProvider:
    provider_name = "fake-history"
    model = "fake-history-model"

    def __init__(self):
        self.calls: list[RinaProviderRequest] = []

    def generate(self, request: RinaProviderRequest) -> RinaProviderResult:
        self.calls.append(request)
        payload = {
            "document": {
                "document_type": "job_record",
                "reference": "JOB-2026-002",
                "document_date": "2026-08-18",
                "job_reference": "JOB-2026-002",
                "sow_reference": "SOW-2026-002",
            },
            "rina_summary": (
                "The source records authorised AIRMATIC and electrical work. "
                "The source alone does not prove every listed item was installed."
            ),
            "advisor_suggestions": [
                "Confirm which authorised components were actually installed.",
                "Keep payment facts separate from vehicle-health history.",
            ],
            "candidates": [
                {
                    "category": "work_item",
                    "state": "authorized",
                    "title": "Alternator",
                    "detail": "A pre-owned alternator was authorised.",
                    "occurred_at": "2026-08-18",
                    "source_excerpt": "A pre-owned alternator will be purchased",
                    "confidence": 0.96,
                    "suggested_destination": "treatment_plan",
                    "outcome_direction": "insufficient_evidence",
                    "action": {
                        "kind": "component_replacement",
                        "component_name": "Alternator",
                        "component_location": None,
                        "component_condition": "preowned_tokunbo",
                        "quantity": 1,
                        "odometer_km": None,
                    },
                },
                {
                    "category": "financial",
                    "state": "observed",
                    "title": "Paid across this job",
                    "detail": "The document records a paid commercial amount.",
                    "occurred_at": "2026-08-18",
                    "source_excerpt": "Paid across this Job",
                    "confidence": 0.99,
                    "suggested_destination": "financial_separate",
                    "outcome_direction": "insufficient_evidence",
                    "action": {
                        "kind": "other_intervention",
                        "component_name": None,
                        "component_location": None,
                        "component_condition": "not_applicable",
                        "quantity": None,
                        "odometer_km": None,
                    },
                },
                {
                    "category": "outcome",
                    "state": "unknown",
                    "title": "Post-work electrical outcome",
                    "detail": "The source does not establish whether the symptom returned.",
                    "occurred_at": None,
                    "source_excerpt": "",
                    "confidence": 0.55,
                    "suggested_destination": "treatment_outcome",
                    "outcome_direction": "insufficient_evidence",
                    "action": {
                        "kind": "other_intervention",
                        "component_name": None,
                        "component_location": None,
                        "component_condition": "not_applicable",
                        "quantity": None,
                        "odometer_km": None,
                    },
                },
            ],
        }
        return RinaProviderResult(
            text=json.dumps(payload),
            provider=self.provider_name,
            model=self.model,
            provider_request_id="req-historical-test",
        )


class ExplodingStream:
    def read(self, *_args, **_kwargs):
        raise AssertionError("file bytes were read before advisor authority was checked")


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Historical User {suffix}",
        email=f"historical-{suffix}@example.com",
        phone_number=f"+23480990{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 9, 19, 12, 0, 0),
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
        vin=f"4JG166HIST{suffix:07d}",
        current_mileage=110000,
        vehicle_identity_source="manual",
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"HIS-{suffix:03d}-LA",
            mileage_at_transfer=110000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _pdf_bytes(text: str) -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output)
    y = 800
    for line in text.splitlines():
        pdf.drawString(50, y, line[:110])
        y -= 20
    pdf.save()
    return output.getvalue()


def test_non_advisor_is_rejected_before_document_bytes_are_read(app):
    with app.app_context():
        owner = _user(suffix=1)
        car = _owned_car(owner, suffix=1)

        with pytest.raises(HistoricalIngestionAccessError):
            ingest_pdf_document(
                user_id=owner.id,
                car_id=car.id,
                file_stream=ExplodingStream(),
                declared_content_type="application/pdf",
                purpose="service_document",
                visibility="advisor",
                retention_days=RETENTION_DAYS,
                storage_provider=RecordingStorageProvider(),
                language_provider=FakeHistoricalProvider(),
            )

        assert VehicleEvidence.query.count() == 0


def test_pdf_extraction_is_candidate_only_until_advisor_review_and_apply(app):
    with app.app_context():
        owner = _user(suffix=2)
        advisor = _user(suffix=3, role="admin")
        car = _owned_car(owner, suffix=2)
        provider = FakeHistoricalProvider()
        storage = RecordingStorageProvider()

        result = ingest_pdf_document(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(
                _pdf_bytes(
                    "JOB-2026-002\n"
                    "A pre-owned alternator will be purchased.\n"
                    "Paid across this Job."
                )
            ),
            declared_content_type="application/pdf",
            purpose="service_document",
            visibility="advisor",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=provider,
        )

        assert result.structured_status == "completed"
        assert TreatmentPlan.query.count() == 0
        assert TreatmentAction.query.count() == 0
        assert TreatmentOutcome.query.count() == 0

        evidence = db.session.get(VehicleEvidence, result.evidence_id)
        extraction = latest_structured_extraction(evidence.id)
        assert evidence.evidence_type == "document"
        assert evidence.storage_state == "available"
        assert evidence.review_status == "pending_review"
        assert evidence.object_key in storage.objects
        assert extraction is not None
        assert extraction.review_status == "unreviewed"
        assert "Alternator" not in (extraction.result_ciphertext or "")

        raw = decrypt_extraction_payload(extraction)
        work = raw["candidates"][0]
        financial = raw["candidates"][1]
        outcome = raw["candidates"][2]

        # Provider extraction correctly remains weaker than completed work.
        assert work["state"] == "authorized"
        assert work["suggested_destination"] == "treatment_plan"
        assert financial["suggested_destination"] == "financial_separate"

        # The advisor supplies the stronger real-world facts. This is the only
        # place the test changes "authorised" into "completed".
        work["review_decision"] = "accepted"
        work["state"] = "completed"
        work["suggested_destination"] = "treatment_action"
        work["completion_confirmed"] = True
        work["occurred_at"] = "2026-08-18"
        work["action"]["component_condition"] = "preowned_tokunbo"

        financial["review_decision"] = "accepted"

        outcome["review_decision"] = "accepted"
        outcome["state"] = "outcome_observed"
        outcome["detail"] = "The intermittent electrical symptom did not return after the work."
        outcome["occurred_at"] = "2026-09-18"
        outcome["outcome_direction"] = "resolved"

        save_advisor_review(
            extraction=extraction,
            actor_user_id=advisor.id,
            reviewed_payload=raw,
        )
        db.session.commit()

        assert evidence.review_status == "accepted"
        assert extraction.review_status == "corrected"

        plan = apply_reviewed_historical_treatment(
            extraction_id=extraction.id,
            actor_user_id=advisor.id,
        )
        db.session.commit()

        assert plan is not None
        assert plan.record_origin == "historical_document"
        assert plan.source_evidence_id == evidence.id
        assert plan.source_extraction_id == extraction.id
        assert plan.status == "completed"

        action = TreatmentAction.query.filter_by(
            treatment_plan_id=plan.id
        ).one()
        assert action.status == "completed"

        detail = TreatmentActionCompletionDetail.query.filter_by(
            treatment_action_id=action.id
        ).one()
        assert detail.action_kind == "component_replacement"
        assert detail.component_name == "Alternator"
        assert detail.component_condition == "preowned_tokunbo"
        assert detail.verification_status == "advisor_confirmed"
        assert detail.source_evidence_id == evidence.id

        recorded_outcome = TreatmentOutcome.query.filter_by(
            treatment_plan_id=plan.id
        ).one()
        assert recorded_outcome.progression_direction == "resolved"
        assert "did not return" in recorded_outcome.summary

        assert EvidenceLink.query.filter_by(
            evidence_id=evidence.id,
            subject_type="treatment_action",
            subject_id=action.id,
        ).count() == 1

        # Financial extraction remains context-only for a future finance layer.
        assert "Paid across this Job" in financial["detail"] or financial["title"]


def test_rina_historical_context_is_advisor_only(app):
    with app.app_context():
        owner = _user(suffix=4)
        advisor = _user(suffix=5, role="admin")
        car = _owned_car(owner, suffix=4)
        provider = FakeHistoricalProvider()
        result = ingest_pdf_document(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(_pdf_bytes("JOB-2026-002\nAlternator authorised")),
            declared_content_type="application/pdf",
            purpose="service_document",
            visibility="advisor",
            retention_days=RETENTION_DAYS,
            storage_provider=RecordingStorageProvider(),
            language_provider=provider,
        )
        extraction = db.session.get(
            EvidenceExtraction,
            result.structured_extraction_id,
        )
        reviewed = decrypt_extraction_payload(extraction)
        reviewed["candidates"][0]["review_decision"] = "accepted"
        save_advisor_review(
            extraction=extraction,
            actor_user_id=advisor.id,
            reviewed_payload=reviewed,
        )
        db.session.commit()

        advisor_context = resolve_rina_vehicle_context(
            user_id=advisor.id,
            car_id=car.id,
        )
        owner_context = resolve_rina_vehicle_context(
            user_id=owner.id,
            car_id=car.id,
        )

        advisor_records = _reviewed_historical_records(advisor_context)
        owner_records = _reviewed_historical_records(owner_context)

        assert len(advisor_records) == 1
        assert advisor_records[0]["job_reference"] == "JOB-2026-002"
        assert advisor_records[0]["accepted_facts"][0]["title"] == "Alternator"
        assert owner_records == []
