from __future__ import annotations

from datetime import datetime
from io import BytesIO

import pytest
from reportlab.pdfgen import canvas

from evidence.models import EvidenceExtraction, EvidenceLink, VehicleEvidence
from evidence.storage import RetrievedEvidenceObject, StoredEvidenceObject
from extensions import db
from historical_ingestion.application import apply_reviewed_historical_treatment
from historical_ingestion.routes import _validate_historical_source_upload
from historical_ingestion.service import (
    HistoricalIngestionAccessError,
    HistoricalSourceSupersessionError,
    advance_historical_background_analysis,
    decrypt_extraction_payload,
    ingest_pdf_document,
    ingest_pdf_document_background,
    latest_background_extraction,
    latest_structured_extraction,
    reanalyze_stored_document,
    reanalyze_stored_document_background,
    save_advisor_review,
    historical_source_summaries,
    supersede_historical_source,
)
from models import Car, CarOwnership, TreatmentPlan, User
from historical_ingestion.advisor_analyzer import (
    HistoricalAdvisorAnalysis,
    HistoricalBackgroundResponse,
)
from rina.providers.base import RinaProviderRejectedError
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

    def get_bytes(self, *, object_key: str, max_bytes: int):
        payload = self.objects[object_key]
        if len(payload) > max_bytes:
            raise AssertionError("test object exceeded retrieval bound")
        return RetrievedEvidenceObject(
            provider=self.provider_name,
            object_key=object_key,
            payload=payload,
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
        self.calls = 0

    def analyze_pdf(
        self,
        *,
        pdf_payload: bytes,
        extracted_text: str,
        trusted_vehicle_context: dict,
    ) -> HistoricalAdvisorAnalysis:
        self.calls += 1
        understanding = {
            "document": {
                "document_type": "job_record",
                "title": "Ajebo Fix job record",
                "reference": "JOB-2026-002",
                "document_date": "2026-08-18",
                "job_reference": "JOB-2026-002",
                "sow_reference": "SOW-2026-002",
                "client_name": "Historical Owner",
                "vehicle_description": "2014 Mercedes-Benz GL 450",
                "vin": trusted_vehicle_context["vin"],
                "plate_number": None,
            },
            "advisor_narrative": (
                "The source documents an electrical concern and authorised work. "
                "It does not by itself prove every authorised item was installed."
            ),
            "chronology": [],
            "facts": [],
            "ambiguities": [
                "Installation of the alternator requires advisor confirmation."
            ],
            "advisor_suggestions": [
                "Confirm which authorised components were actually installed."
            ],
        }
        structured = {
            "document": understanding["document"],
            "rina_summary": understanding["advisor_narrative"],
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
                    "source_pages": [1],
                    "source_fact_ids": ["f1"],
                    "source_excerpt": "A pre-owned alternator will be purchased.",
                    "confidence": 0.96,
                    "confidence_reason": "The source states the plan directly.",
                    "suggested_destination": "treatment_plan",
                    "outcome_direction": "insufficient_evidence",
                    "advisor_attention": "Confirm actual installation separately.",
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
                    "state": "completed",
                    "title": "Paid across this job",
                    "detail": "The document records a paid commercial amount.",
                    "occurred_at": "2026-08-18",
                    "source_pages": [1],
                    "source_fact_ids": ["f2"],
                    "source_excerpt": "Paid across this Job.",
                    "confidence": 0.99,
                    "confidence_reason": "The commercial line is explicit.",
                    "suggested_destination": "financial_separate",
                    "outcome_direction": "insufficient_evidence",
                    "advisor_attention": "",
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
                    "source_pages": [],
                    "source_fact_ids": [],
                    "source_excerpt": "",
                    "confidence": 0.55,
                    "confidence_reason": "No post-work observation is present.",
                    "suggested_destination": "treatment_outcome",
                    "outcome_direction": "insufficient_evidence",
                    "advisor_attention": "Do not convert this into a resolved outcome.",
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
        return HistoricalAdvisorAnalysis(
            understanding=understanding,
            structured=structured,
            provider=self.provider_name,
            model=self.model,
            understanding_request_id="req-understanding",
            structured_request_id="req-structured",
        )


class FakeBackgroundHistoricalProvider:
    provider_name = "fake-background-history"
    model = "fake-background-model"

    def __init__(self):
        self.started_understanding = 0
        self.started_structuring = 0
        self.retrieve_calls = []

    def start_understanding_background(
        self,
        *,
        pdf_payload: bytes,
        extracted_text: str,
        trusted_vehicle_context: dict,
    ):
        self.started_understanding += 1
        assert pdf_payload.startswith(b"%PDF-")
        assert "--- PAGE 1 ---" in extracted_text
        assert trusted_vehicle_context["audience"] == "Ajebo Fix professional advisor"
        return (
            HistoricalBackgroundResponse(
                response_id="resp-understanding",
                status="queued",
                model=self.model,
            ),
            True,
        )

    def retrieve_background(self, response_id: str):
        self.retrieve_calls.append(response_id)
        if response_id == "resp-understanding":
            return HistoricalBackgroundResponse(
                response_id=response_id,
                status="completed",
                model=self.model,
                payload={
                    "document": {
                        "document_type": "job_record",
                        "title": "Ajebo Fix job record",
                        "reference": "JOB-2026-002",
                        "document_date": "2026-08-18",
                        "job_reference": "JOB-2026-002",
                        "sow_reference": "SOW-2026-002",
                        "client_name": "Historical Owner",
                        "vehicle_description": "2014 Mercedes-Benz GL 450",
                        "vin": "4JG166HIST0000012",
                        "plate_number": None,
                    },
                    "advisor_narrative": (
                        "The source records an electrical concern, an observed "
                        "AIRMATIC leak and recommended work."
                    ),
                    "chronology": [],
                    "facts": [],
                    "ambiguities": [],
                    "advisor_suggestions": [
                        "Confirm completed work from completion evidence."
                    ],
                },
            )
        if response_id == "resp-structuring":
            return HistoricalBackgroundResponse(
                response_id=response_id,
                status="completed",
                model=self.model,
                payload={
                    "document": {
                        "document_type": "job_record",
                        "title": "Ajebo Fix job record",
                        "reference": "JOB-2026-002",
                        "document_date": "2026-08-18",
                        "job_reference": "JOB-2026-002",
                        "sow_reference": "SOW-2026-002",
                        "client_name": "Historical Owner",
                        "vehicle_description": "2014 Mercedes-Benz GL 450",
                        "vin": "4JG166HIST0000012",
                        "plate_number": None,
                    },
                    "rina_summary": "Advisor-grade historical summary.",
                    "advisor_suggestions": [
                        "Confirm completed work from completion evidence."
                    ],
                    "candidates": [
                        {
                            "category": "reported_concern",
                            "state": "reported",
                            "title": "Intermittent electrical concern",
                            "detail": "Owner reported intermittent electrical loss.",
                            "occurred_at": None,
                            "source_pages": [1],
                            "source_fact_ids": ["f1"],
                            "source_excerpt": "Intermittent electrical concern",
                            "confidence": 0.93,
                            "confidence_reason": "Explicitly reported in the source.",
                            "suggested_destination": "reported_concern",
                            "outcome_direction": "insufficient_evidence",
                            "advisor_attention": "",
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

    def start_structuring_background(
        self,
        *,
        understanding: dict,
        trusted_vehicle_context: dict,
    ):
        self.started_structuring += 1
        assert understanding["document"]["job_reference"] == "JOB-2026-002"
        assert trusted_vehicle_context["audience"] == "Ajebo Fix professional advisor"
        return HistoricalBackgroundResponse(
            response_id="resp-structuring",
            status="queued",
            model=self.model,
        )


class FailingHistoricalProvider:
    provider_name = "failing-history"

    def analyze_pdf(self, **_kwargs):
        raise RinaProviderRejectedError("safe provider rejection")


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
        assert evidence.historical_source_type == "standalone_document"
        assert evidence.storage_state == "available"
        assert evidence.review_status == "pending_review"
        assert evidence.object_key in storage.objects
        assert extraction is not None
        assert extraction.review_status == "unreviewed"
        understanding = EvidenceExtraction.query.filter_by(
            evidence_id=evidence.id,
            extraction_type="document_understanding",
        ).one()
        assert understanding.provider_model == "fake-history-model"
        assert "Alternator" not in (extraction.result_ciphertext or "")

        raw = decrypt_extraction_payload(extraction)
        work = raw["candidates"][0]
        financial = raw["candidates"][1]
        outcome = raw["candidates"][2]

        # Provider extraction correctly remains weaker than completed work.
        assert work["state"] == "authorized"
        assert work["suggested_destination"] == "treatment_plan"
        assert financial["suggested_destination"] == "financial_separate"
        assert financial["state"] == "observed"
        assert work["source_verified"] is True

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



def test_identical_pdf_reopens_existing_analysis_instead_of_reinterpreting(app):
    with app.app_context():
        owner = _user(suffix=6)
        advisor = _user(suffix=7, role="admin")
        car = _owned_car(owner, suffix=6)
        analyzer = FakeHistoricalProvider()
        storage = RecordingStorageProvider()
        payload = _pdf_bytes(
            "JOB-2026-002\n"
            "A pre-owned alternator will be purchased.\n"
            "Paid across this Job."
        )

        first = ingest_pdf_document(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(payload),
            declared_content_type="application/pdf",
            purpose="service_document",
            visibility="advisor",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=analyzer,
        )
        second = ingest_pdf_document(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(payload),
            declared_content_type="application/pdf",
            purpose="service_document",
            visibility="advisor",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=analyzer,
        )

        assert first.reused_existing is False
        assert second.reused_existing is True
        assert second.evidence_id == first.evidence_id
        assert second.structured_extraction_id == first.structured_extraction_id
        assert analyzer.calls == 1
        assert VehicleEvidence.query.filter_by(car_id=car.id).count() == 1



def test_advisor_can_reanalyze_private_source_without_reupload(app):
    with app.app_context():
        owner = _user(suffix=8)
        advisor = _user(suffix=9, role="admin")
        car = _owned_car(owner, suffix=8)
        analyzer = FakeHistoricalProvider()
        storage = RecordingStorageProvider()
        payload = _pdf_bytes(
            "JOB-2026-002\n"
            "A pre-owned alternator will be purchased.\n"
            "Paid across this Job."
        )

        first = ingest_pdf_document(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(payload),
            declared_content_type="application/pdf",
            purpose="service_document",
            visibility="advisor",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=analyzer,
        )
        first_structured_id = first.structured_extraction_id

        second = reanalyze_stored_document(
            evidence_id=first.evidence_id,
            actor_user_id=advisor.id,
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=analyzer,
        )

        assert second.evidence_id == first.evidence_id
        assert second.structured_extraction_id != first_structured_id
        assert analyzer.calls == 2
        assert VehicleEvidence.query.filter_by(car_id=car.id).count() == 1
        assert EvidenceExtraction.query.filter_by(
            evidence_id=first.evidence_id,
            extraction_type="structured_fields",
        ).count() == 2


def test_background_import_returns_immediately_and_advances_in_two_poll_steps(app):
    with app.app_context():
        owner = _user(suffix=12)
        advisor = _user(suffix=13, role="admin")
        car = _owned_car(owner, suffix=12)
        analyzer = FakeBackgroundHistoricalProvider()
        storage = RecordingStorageProvider()
        payload = _pdf_bytes(
            "JOB-2026-002\n"
            "Intermittent electrical concern\n"
            "AIRMATIC leak observed"
        )

        started = ingest_pdf_document_background(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(payload),
            declared_content_type="application/pdf",
            purpose="service_document",
            visibility="advisor",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=analyzer,
        )

        assert started.status == "processing"
        assert started.phase == "understanding"
        assert analyzer.started_understanding == 1
        background = latest_background_extraction(started.evidence_id)
        assert background is not None
        assert background.status == "processing"
        assert background.provenance["background_stage"] == "understanding"

        first_poll = advance_historical_background_analysis(
            extraction_id=background.id,
            actor_user_id=advisor.id,
            language_provider=analyzer,
        )
        assert first_poll.status == "processing"
        assert first_poll.phase == "structuring"
        assert analyzer.started_structuring == 1

        second_poll = advance_historical_background_analysis(
            extraction_id=background.id,
            actor_user_id=advisor.id,
            language_provider=analyzer,
        )
        assert second_poll.status == "completed"
        assert second_poll.review_ready is True

        usable = latest_structured_extraction(started.evidence_id)
        assert usable is not None
        assert usable.id == background.id
        assert usable.status == "completed"
        payload = decrypt_extraction_payload(usable)
        assert payload["rina_summary"] == "Advisor-grade historical summary."
        assert payload["candidates"][0]["suggested_destination"] == "reported_concern"


def test_background_reanalysis_is_idempotent_while_processing(app):
    with app.app_context():
        owner = _user(suffix=14)
        advisor = _user(suffix=15, role="admin")
        car = _owned_car(owner, suffix=14)
        storage = RecordingStorageProvider()
        source_provider = FakeHistoricalProvider()
        background_provider = FakeBackgroundHistoricalProvider()
        payload = _pdf_bytes(
            "JOB-2026-002\n"
            "Intermittent electrical concern\n"
            "AIRMATIC leak observed"
        )

        source = ingest_pdf_document(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(payload),
            declared_content_type="application/pdf",
            purpose="service_document",
            visibility="advisor",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=source_provider,
        )

        first = reanalyze_stored_document_background(
            evidence_id=source.evidence_id,
            actor_user_id=advisor.id,
            storage_provider=storage,
            language_provider=background_provider,
        )
        second = reanalyze_stored_document_background(
            evidence_id=source.evidence_id,
            actor_user_id=advisor.id,
            storage_provider=storage,
            language_provider=background_provider,
        )

        assert first.status == "processing"
        assert second.status == "processing"
        assert second.reused_analysis is True
        assert second.extraction_id == first.extraction_id
        assert background_provider.started_understanding == 1


def test_failed_reanalysis_does_not_hide_previous_completed_candidates(app):
    with app.app_context():
        owner = _user(suffix=10)
        advisor = _user(suffix=11, role="admin")
        car = _owned_car(owner, suffix=10)
        good = FakeHistoricalProvider()
        storage = RecordingStorageProvider()
        payload = _pdf_bytes(
            "JOB-2026-002\n"
            "A pre-owned alternator will be purchased.\n"
            "Paid across this Job."
        )

        first = ingest_pdf_document(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(payload),
            declared_content_type="application/pdf",
            purpose="service_document",
            visibility="advisor",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=good,
        )
        first_completed = db.session.get(
            EvidenceExtraction,
            first.structured_extraction_id,
        )

        failed = reanalyze_stored_document(
            evidence_id=first.evidence_id,
            actor_user_id=advisor.id,
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=FailingHistoricalProvider(),
        )

        assert failed.structured_status == "failed"
        usable = latest_structured_extraction(first.evidence_id)
        assert usable is not None
        assert usable.id == first_completed.id
        assert usable.status == "completed"

        failed_row = (
            EvidenceExtraction.query.filter_by(
                evidence_id=first.evidence_id,
                extraction_type="structured_fields",
                status="failed",
            )
            .order_by(EvidenceExtraction.id.desc())
            .first()
        )
        assert failed_row is not None
        assert failed_row.provenance["failure_class"] == "RinaProviderRejectedError"
        assert "safe provider rejection" in failed_row.provenance["failure_detail"]


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


def test_historical_source_type_rejects_mismatched_file_formats():
    assert _validate_historical_source_upload(
        source_type="whatsapp_conversation",
        filename="job.pdf",
        content_type="application/pdf",
    ) == (
        "WhatsApp conversation sources must be uploaded as the original "
        "WhatsApp ZIP export."
    )

    assert _validate_historical_source_upload(
        source_type="standalone_document",
        filename="chat.zip",
        content_type="application/zip",
    ) == "Standalone document sources accept PDF files, not ZIP archives."

    assert _validate_historical_source_upload(
        source_type="whatsapp_conversation",
        filename="WhatsApp Chat.zip",
        content_type="application/octet-stream",
    ) is None

    assert _validate_historical_source_upload(
        source_type="standalone_document",
        filename="service-record.pdf",
        content_type="application/pdf",
    ) is None

    assert _validate_historical_source_upload(
        source_type="instagram_conversation",
        filename="instagram.zip",
        content_type="application/zip",
    ) == "Select a supported historical source type."


def test_superseded_historical_source_is_preserved_but_hidden_from_working_list(app):
    with app.app_context():
        owner = _user(suffix=40)
        advisor = _user(suffix=41, role="admin")
        car = _owned_car(owner, suffix=40)
        provider = FakeHistoricalProvider()
        storage = RecordingStorageProvider()

        obsolete = ingest_pdf_document(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(_pdf_bytes("OLD JOB VERSION\nAlternator authorised")),
            declared_content_type="application/pdf",
            purpose="service_document",
            visibility="advisor",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=provider,
        )
        replacement = ingest_pdf_document(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(_pdf_bytes("FINAL JOB VERSION\nAlternator authorised")),
            declared_content_type="application/pdf",
            purpose="service_document",
            visibility="advisor",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=provider,
        )

        replacement_extraction = db.session.get(
            EvidenceExtraction,
            replacement.structured_extraction_id,
        )
        replacement_payload = decrypt_extraction_payload(replacement_extraction)
        save_advisor_review(
            extraction=replacement_extraction,
            actor_user_id=advisor.id,
            reviewed_payload=replacement_payload,
        )
        db.session.commit()

        supersede_historical_source(
            evidence_id=obsolete.evidence_id,
            replacement_evidence_id=replacement.evidence_id,
            actor_user_id=advisor.id,
        )
        db.session.commit()

        obsolete_row = db.session.get(VehicleEvidence, obsolete.evidence_id)
        assert obsolete_row.review_status == "superseded"
        assert obsolete_row.review_reason_code == (
            f"superseded_by_evidence:{replacement.evidence_id}"
        )
        assert obsolete_row.deleted_at is None
        assert obsolete_row.storage_state == "available"
        assert EvidenceExtraction.query.filter_by(
            evidence_id=obsolete.evidence_id
        ).count() > 0

        active = historical_source_summaries(car.id)
        archived = historical_source_summaries(car.id, include_superseded=True)

        assert [item.evidence.id for item in active] == [replacement.evidence_id]
        assert {item.evidence.id for item in archived} == {
            obsolete.evidence_id,
            replacement.evidence_id,
        }
        superseded = next(
            item for item in archived if item.evidence.id == obsolete.evidence_id
        )
        assert superseded.state == "superseded"
        assert superseded.state_label == "Superseded"


def test_supersession_requires_same_vehicle_finalised_replacement(app):
    with app.app_context():
        owner = _user(suffix=42)
        other_owner = _user(suffix=43)
        advisor = _user(suffix=44, role="admin")
        car = _owned_car(owner, suffix=42)
        other_car = _owned_car(other_owner, suffix=43)
        provider = FakeHistoricalProvider()
        storage = RecordingStorageProvider()

        obsolete = ingest_pdf_document(
            user_id=advisor.id,
            car_id=car.id,
            file_stream=BytesIO(_pdf_bytes("OLD SOURCE\nAlternator authorised")),
            declared_content_type="application/pdf",
            purpose="service_document",
            visibility="advisor",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=provider,
        )
        other = ingest_pdf_document(
            user_id=advisor.id,
            car_id=other_car.id,
            file_stream=BytesIO(_pdf_bytes("OTHER VEHICLE FINAL\nAlternator authorised")),
            declared_content_type="application/pdf",
            purpose="service_document",
            visibility="advisor",
            retention_days=RETENTION_DAYS,
            storage_provider=storage,
            language_provider=provider,
        )

        other_extraction = db.session.get(
            EvidenceExtraction,
            other.structured_extraction_id,
        )
        save_advisor_review(
            extraction=other_extraction,
            actor_user_id=advisor.id,
            reviewed_payload=decrypt_extraction_payload(other_extraction),
        )
        db.session.commit()

        with pytest.raises(
            HistoricalSourceSupersessionError,
            match="same vehicle",
        ):
            supersede_historical_source(
                evidence_id=obsolete.evidence_id,
                replacement_evidence_id=other.evidence_id,
                actor_user_id=advisor.id,
            )
