from __future__ import annotations

from datetime import datetime

from evidence.models import (
    EvidenceBundleItem,
    EvidenceExtraction,
    VehicleEvidence,
)
from extensions import db
from historical_ingestion.advisor_analyzer import HistoricalBackgroundResponse
from historical_ingestion.case_attribution import (
    attribution_payload,
    advance_case_attribution,
    create_episode_from_finalized_source,
    start_case_attribution,
)
from historical_ingestion.service import _payload_cipher
from models import Car, CarOwnership, User


PASSWORD = "Password123"


class FakeCaseAttributionAnalyzer:
    provider_name = "fake-case-attribution"
    model = "fake-case-model"

    def __init__(self):
        self.starts: list[str] = []
        self.retrieves: list[str] = []

    def start_case_attribution_background(
        self,
        *,
        corpus: str,
        episode_anchor: dict,
        trusted_vehicle_context: dict,
    ):
        assert "CHAT m000001" in corpus
        assert "CHAT m000003" in corpus
        assert trusted_vehicle_context["audience"] == "Ajebo Fix professional advisor"
        job = episode_anchor["job_reference"]
        self.starts.append(job)
        return HistoricalBackgroundResponse(
            response_id=f"attr-{job}",
            status="queued",
            model=self.model,
        )

    def retrieve_background(self, response_id: str):
        self.retrieves.append(response_id)
        if response_id == "attr-JOB-2026-A":
            groups = [
                {
                    "classification": "matched",
                    "title": "AIRMATIC work discussion",
                    "summary": "The chat discusses the rear air spring job.",
                    "occurred_at": "2026-08-18T09:00:00",
                    "evidence_role": "completed_work",
                    "source_refs": ["CHAT m000001"],
                    "source_excerpt": "Rear air spring fitted today.",
                    "confidence": 0.97,
                    "match_reason": "Same job subject and date as the anchor.",
                },
                {
                    "classification": "other_episode",
                    "title": "Earlier battery visit",
                    "summary": "This message is about an earlier battery problem.",
                    "occurred_at": "2026-02-10T10:00:00",
                    "evidence_role": "observation",
                    "source_refs": ["CHAT m000003"],
                    "source_excerpt": "Battery warning came back.",
                    "confidence": 0.95,
                    "match_reason": "Different time period and different job subject.",
                },
            ]
        elif response_id == "attr-JOB-2026-B":
            groups = [
                {
                    "classification": "other_episode",
                    "title": "Later AIRMATIC job",
                    "summary": "This message belongs to the later suspension intervention.",
                    "occurred_at": "2026-08-18T09:00:00",
                    "evidence_role": "completed_work",
                    "source_refs": ["CHAT m000001"],
                    "source_excerpt": "Rear air spring fitted today.",
                    "confidence": 0.94,
                    "match_reason": "The anchor is the earlier electrical episode.",
                },
                {
                    "classification": "matched",
                    "title": "Battery warning",
                    "summary": "The chat describes the electrical episode.",
                    "occurred_at": "2026-02-10T10:00:00",
                    "evidence_role": "reported_concern",
                    "source_refs": ["CHAT m000003"],
                    "source_excerpt": "Battery warning came back.",
                    "confidence": 0.96,
                    "match_reason": "Same date range and electrical subject as the anchor.",
                },
            ]
        else:
            raise AssertionError(f"unexpected response id {response_id}")

        return HistoricalBackgroundResponse(
            response_id=response_id,
            status="completed",
            model=self.model,
            payload={
                "episode_summary": "Episode-specific WhatsApp attribution.",
                "match_overview": "The corpus contains evidence from more than one job.",
                "evidence_groups": groups,
                "advisor_attention": [
                    "Keep the two service episodes separate in durable history."
                ],
            },
        )


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Attribution User {suffix}",
        email=f"attribution-{suffix}@example.com",
        phone_number=f"+23480660{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 9, 21, 12, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _car(owner: User, *, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GL 450",
        year=2014,
        vin=f"4JG166ATTR{suffix:07d}",
        current_mileage=120000,
        vehicle_identity_source="manual",
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"ATR-{suffix:03d}-LA",
            mileage_at_transfer=120000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _evidence(
    *,
    car: Car,
    advisor: User,
    evidence_type: str,
    historical_source_type: str,
    key: str,
    review_status: str = "pending_review",
) -> VehicleEvidence:
    now = datetime(2026, 9, 21, 12, 0, 0)
    row = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=advisor.id,
        evidence_type=evidence_type,
        purpose="service_document",
        source_channel=(
            "whatsapp" if historical_source_type == "whatsapp_conversation" else "web"
        ),
        historical_source_type=historical_source_type,
        visibility="advisor",
        review_status=review_status,
        storage_provider="test-private",
        storage_state="available",
        object_key=f"evidence/{key}",
        safe_display_name=f"{key}.bin",
        content_type=(
            "application/zip" if evidence_type == "archive" else "application/pdf"
        ),
        byte_size=128,
        sha256=(key.encode().hex() + "0" * 64)[:64],
        uploaded_at=now,
        consent_basis="advisor_vehicle_care_record",
        lawful_purpose="vehicle_care_recordkeeping",
        reviewed_by_user_id=(advisor.id if review_status == "accepted" else None),
        reviewed_at=(now if review_status == "accepted" else None),
        review_reason_code=(
            "sufficient_for_record" if review_status == "accepted" else None
        ),
    )
    db.session.add(row)
    db.session.flush()
    return row


def _finalised_anchor(
    *,
    car: Car,
    advisor: User,
    key: str,
    job_reference: str,
    document_date: str,
    title: str,
) -> VehicleEvidence:
    evidence = _evidence(
        car=car,
        advisor=advisor,
        evidence_type="document",
        historical_source_type="standalone_document",
        key=key,
        review_status="accepted",
    )
    payload = {
        "document": {
            "document_type": "job_record",
            "title": title,
            "reference": job_reference,
            "job_reference": job_reference,
            "sow_reference": None,
            "document_date": document_date,
        },
        "rina_summary": f"Reviewed anchor for {job_reference}.",
        "advisor_review_note": "Confirmed historical job anchor.",
        "candidates": [
            {
                "review_decision": "accepted",
                "category": "work_item",
                "state": "completed",
                "title": title,
                "detail": f"Completed work for {job_reference}.",
                "occurred_at": document_date,
                "source_fact_ids": ["F-001"],
            }
        ],
    }
    cipher, version, digest = _payload_cipher(payload)
    extraction = EvidenceExtraction(
        evidence_id=evidence.id,
        extraction_type="structured_fields",
        provider="fake-history",
        provider_model="fake-history-model",
        status="completed",
        result_ciphertext=cipher,
        result_key_version=version,
        result_sha256=digest,
        review_status="corrected",
        reviewed_by_user_id=advisor.id,
        reviewed_at=datetime(2026, 9, 21, 12, 0, 0),
        review_reason_code="historical_record_advisor_review",
        reviewed_result_ciphertext=cipher,
        reviewed_result_key_version=version,
        reviewed_result_sha256=digest,
        completed_at=datetime(2026, 9, 21, 12, 0, 0),
    )
    db.session.add(extraction)
    db.session.commit()
    return evidence


def _whatsapp_corpus(*, car: Car, advisor: User) -> tuple[VehicleEvidence, VehicleEvidence]:
    root = _evidence(
        car=car,
        advisor=advisor,
        evidence_type="archive",
        historical_source_type="whatsapp_conversation",
        key="whatsapp-root",
    )
    child = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=advisor.id,
        evidence_type="document",
        purpose="service_document",
        source_channel="whatsapp",
        historical_source_type="whatsapp_conversation",
        visibility="advisor",
        review_status="pending_review",
        storage_provider="test-private",
        storage_state="available",
        object_key="evidence/whatsapp-transcript",
        safe_display_name="chat.txt",
        content_type="text/plain",
        byte_size=256,
        sha256="a" * 64,
        uploaded_at=datetime(2026, 9, 21, 12, 0, 0),
        consent_basis="advisor_whatsapp_case_import",
        lawful_purpose="vehicle_care_recordkeeping",
    )
    db.session.add(child)
    db.session.flush()
    db.session.add(
        EvidenceBundleItem(
            bundle_evidence_id=root.id,
            child_evidence_id=child.id,
            member_index=0,
            member_kind="transcript",
            member_sha256="a" * 64,
        )
    )

    text_payload = {
        "schema_version": 1,
        "format": "whatsapp_export",
        "message_count": 3,
        "text": (
            "[CHAT m000001 | 18/08/2026, 09:00 | Advisor] Rear air spring fitted today.\n"
            "[CHAT m000002 | 19/08/2026, 12:00 | Client] Car is sitting level.\n"
            "[CHAT m000003 | 10/02/2026, 10:00 | Client] Battery warning came back."
        ),
    }
    cipher, version, digest = _payload_cipher(text_payload)
    db.session.add(
        EvidenceExtraction(
            evidence_id=child.id,
            extraction_type="document_text",
            provider="aura_whatsapp_parser",
            status="completed",
            result_ciphertext=cipher,
            result_key_version=version,
            result_sha256=digest,
            provenance={
                "analysis_pipeline": "whatsapp_bundle_v1",
                "semantic_authority": "source_text",
            },
            completed_at=datetime(2026, 9, 21, 12, 0, 0),
        )
    )
    db.session.add(
        EvidenceExtraction(
            evidence_id=root.id,
            extraction_type="structured_fields",
            provider="fake-bundle",
            provider_model="fake-bundle-model",
            status="completed",
            review_status="unreviewed",
            provenance={
                "analysis_pipeline": "whatsapp_bundle_v1",
                "background_stage": "completed",
                "semantic_authority": "candidate_only",
            },
            completed_at=datetime(2026, 9, 21, 12, 0, 0),
        )
    )
    db.session.commit()
    return root, child


def test_same_whatsapp_corpus_can_be_separated_into_multiple_historical_episodes(app):
    with app.app_context():
        owner = _user(suffix=1)
        advisor = _user(suffix=2, role="admin")
        car = _car(owner, suffix=1)

        anchor_a = _finalised_anchor(
            car=car,
            advisor=advisor,
            key="anchor-a",
            job_reference="JOB-2026-A",
            document_date="2026-08-18",
            title="Rear AIRMATIC intervention",
        )
        anchor_b = _finalised_anchor(
            car=car,
            advisor=advisor,
            key="anchor-b",
            job_reference="JOB-2026-B",
            document_date="2026-02-10",
            title="Electrical and battery visit",
        )
        corpus, transcript = _whatsapp_corpus(car=car, advisor=advisor)

        episode_a = create_episode_from_finalized_source(
            car_id=car.id,
            evidence_id=anchor_a.id,
            actor_user_id=advisor.id,
        )
        episode_b = create_episode_from_finalized_source(
            car_id=car.id,
            evidence_id=anchor_b.id,
            actor_user_id=advisor.id,
        )
        db.session.commit()

        before_child_extractions = EvidenceExtraction.query.filter_by(
            evidence_id=transcript.id
        ).count()
        analyzer = FakeCaseAttributionAnalyzer()

        start_a = start_case_attribution(
            episode_id=episode_a.episode_id,
            corpus_evidence_id=corpus.id,
            actor_user_id=advisor.id,
            analyzer=analyzer,
        )
        status_a = advance_case_attribution(
            extraction_id=start_a.extraction_id,
            actor_user_id=advisor.id,
            analyzer=analyzer,
        )
        assert status_a.status == "completed"

        start_b = start_case_attribution(
            episode_id=episode_b.episode_id,
            corpus_evidence_id=corpus.id,
            actor_user_id=advisor.id,
            analyzer=analyzer,
        )
        status_b = advance_case_attribution(
            extraction_id=start_b.extraction_id,
            actor_user_id=advisor.id,
            analyzer=analyzer,
        )
        assert status_b.status == "completed"

        assert start_a.extraction_id != start_b.extraction_id
        assert VehicleEvidence.query.filter_by(
            car_id=car.id,
            evidence_type="archive",
        ).count() == 1
        assert EvidenceExtraction.query.filter_by(
            evidence_id=transcript.id
        ).count() == before_child_extractions

        extraction_a = db.session.get(EvidenceExtraction, start_a.extraction_id)
        extraction_b = db.session.get(EvidenceExtraction, start_b.extraction_id)
        payload_a = attribution_payload(extraction_a)
        payload_b = attribution_payload(extraction_b)

        groups_a = {
            (group["classification"], tuple(group["source_refs"]))
            for group in payload_a["evidence_groups"]
        }
        groups_b = {
            (group["classification"], tuple(group["source_refs"]))
            for group in payload_b["evidence_groups"]
        }

        assert ("matched", ("CHAT m000001",)) in groups_a
        assert ("other_episode", ("CHAT m000003",)) in groups_a
        assert ("matched", ("CHAT m000003",)) in groups_b
        assert ("other_episode", ("CHAT m000001",)) in groups_b

        runs = EvidenceExtraction.query.filter_by(
            evidence_id=corpus.id,
            extraction_type="historical_case_attribution",
        ).all()
        assert len(runs) == 2
        assert {
            int((run.provenance or {})["episode_id"])
            for run in runs
        } == {episode_a.episode_id, episode_b.episode_id}


def test_episode_creation_reuses_same_finalised_anchor(app):
    with app.app_context():
        owner = _user(suffix=10)
        advisor = _user(suffix=11, role="admin")
        car = _car(owner, suffix=10)
        anchor = _finalised_anchor(
            car=car,
            advisor=advisor,
            key="anchor-idempotent",
            job_reference="JOB-2026-IDEMP",
            document_date="2026-06-01",
            title="Historical service",
        )

        first = create_episode_from_finalized_source(
            car_id=car.id,
            evidence_id=anchor.id,
            actor_user_id=advisor.id,
        )
        db.session.commit()
        second = create_episode_from_finalized_source(
            car_id=car.id,
            evidence_id=anchor.id,
            actor_user_id=advisor.id,
        )

        assert first.created is True
        assert second.created is False
        assert second.episode_id == first.episode_id
