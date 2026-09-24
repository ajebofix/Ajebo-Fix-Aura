from __future__ import annotations

from datetime import datetime
import json

from evidence.models import EvidenceExtraction, VehicleEvidence
from extensions import db
from historical_ingestion.models import HistoricalServiceEpisode
from historical_ingestion.service import _payload_cipher
from models import Car, CarOwnership, TreatmentPlan, User, VehicleEvent
from profiles.models import ClientProfile, ProfileAuditEvent
from rina.audit_models import RinaAIAuditEvent
from services.rina_advisor_360 import build_rina_advisor_360_context
from services.rina_context_resolver import resolve_rina_vehicle_context
from services.rina_contracts import RinaRequest
from services.rina_memory_service import RinaMemoryBundle
from services.rina_provider_context import build_rina_provider_context
from services.rina_runtime_flags import rina_advisor_360_enabled
from treatment.models import (
    TreatmentAction,
    TreatmentActionAddendum,
    TreatmentActionCompletionDetail,
)


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Advisor 360 User {suffix}",
        email=f"advisor360-{suffix}@example.com",
        phone_number=f"+2348098{suffix:05d}",
        role=role,
        is_active=True,
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _setup_longitudinal_case():
    owner = _user(suffix=1)
    admin = _user(suffix=2, role="admin")
    car = Car(
        brand="Mercedes-Benz",
        model="GL 450",
        year=2014,
        vin="4JG166ADVISOR3600001",
        current_mileage=121000,
        vehicle_identity_source="manual",
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number="R360-001-LA",
        mileage_at_transfer=120000,
        is_active=True,
        care_plan="active_monitoring",
        start_date=datetime(2026, 1, 1, 9, 0, 0),
    )
    db.session.add(ownership)
    db.session.flush()

    profile = ClientProfile(
        user_id=owner.id,
        occupation="Business owner",
        organisation="Example Holdings",
        city="Lagos",
        state_region="Lagos",
        country="Nigeria",
        preferred_communication="whatsapp",
        preferred_communication_time="Evening",
        care_preference="Explain consequence before intervention.",
        preferred_language="English",
        timezone="Africa/Lagos",
    )
    profile.home_address = "Private home address that must not reach provider context"
    profile.emergency_contact_name = "Private Contact"
    profile.emergency_contact_phone = "+2348000000000"
    db.session.add(profile)

    anchor = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=admin.id,
        evidence_type="document",
        purpose="service_document",
        source_channel="web",
        historical_source_type="standalone_document",
        visibility="advisor",
        review_status="accepted",
        storage_provider="test-private",
        storage_state="available",
        object_key="advisor360/anchor.pdf",
        safe_display_name="JOB-2026-002.pdf",
        content_type="application/pdf",
        byte_size=256,
        sha256="1" * 64,
        uploaded_at=datetime(2026, 9, 20, 12, 0, 0),
        consent_basis="advisor_vehicle_care_record",
        lawful_purpose="vehicle_care_recordkeeping",
        reviewed_by_user_id=admin.id,
        reviewed_at=datetime(2026, 9, 20, 12, 30, 0),
        review_reason_code="sufficient_for_record",
    )
    corpus = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=admin.id,
        evidence_type="archive",
        purpose="service_document",
        source_channel="whatsapp",
        historical_source_type="whatsapp_conversation",
        visibility="advisor",
        review_status="pending_review",
        storage_provider="test-private",
        storage_state="available",
        object_key="advisor360/whatsapp.zip",
        safe_display_name="WhatsApp case bundle.zip",
        content_type="application/zip",
        byte_size=512,
        sha256="2" * 64,
        uploaded_at=datetime(2026, 9, 20, 13, 0, 0),
        consent_basis="advisor_whatsapp_case_import",
        lawful_purpose="vehicle_care_recordkeeping",
    )
    db.session.add_all([anchor, corpus])
    db.session.flush()

    anchor_payload = {
        "document": {
            "document_type": "job_record",
            "title": "Electrical and rear AIRMATIC repair",
            "reference": "JOB-2026-002",
            "job_reference": "JOB-2026-002",
            "document_date": "2026-08-07",
        },
        "rina_summary": "Reviewed historical job anchor.",
        "candidates": [],
    }
    cipher, version, digest = _payload_cipher(anchor_payload)
    anchor_extraction = EvidenceExtraction(
        evidence_id=anchor.id,
        extraction_type="structured_fields",
        provider="test",
        provider_model="test-model",
        status="completed",
        result_ciphertext=cipher,
        result_key_version=version,
        result_sha256=digest,
        review_status="corrected",
        reviewed_by_user_id=admin.id,
        reviewed_at=datetime(2026, 9, 20, 12, 30, 0),
        reviewed_result_ciphertext=cipher,
        reviewed_result_key_version=version,
        reviewed_result_sha256=digest,
        completed_at=datetime(2026, 9, 20, 12, 20, 0),
    )
    db.session.add(anchor_extraction)
    db.session.flush()

    episode = HistoricalServiceEpisode(
        car_id=car.id,
        anchor_evidence_id=anchor.id,
        anchor_extraction_id=anchor_extraction.id,
        created_by_user_id=admin.id,
        title="Electrical Power Supply Fault & Rear AIRMATIC Suspension Repair",
        job_reference="JOB-2026-002",
        episode_date=datetime(2026, 8, 7, 0, 0, 0),
        status="active",
    )
    db.session.add(episode)
    db.session.flush()

    attribution_payload = {
        "schema_version": 1,
        "episode_summary": "August 2026 WhatsApp evidence belongs to JOB-2026-002.",
        "match_overview": "Evidence separates this episode from earlier work.",
        "evidence_groups": [
            {
                "classification": "matched",
                "title": "Rear suspension workshop work",
                "summary": "Workshop thread relates to the August episode.",
                "occurred_at": "2026-08-11T12:00:00",
                "evidence_role": "completed_work",
                "source_refs": ["CHAT m000446"],
                "source_excerpt": "The panel beater did a good job.",
                "confidence": 0.99,
                "match_reason": "Same episode and workshop stay.",
            }
        ],
        "advisor_attention": ["Electrical root cause remained unresolved."],
        "source_ref_counts": {
            "matched": 1,
            "uncertain": 0,
            "other_episode": 0,
            "unassigned": 0,
        },
    }
    cipher, version, digest = _payload_cipher(attribution_payload)
    attribution = EvidenceExtraction(
        evidence_id=corpus.id,
        extraction_type="historical_case_attribution",
        provider="test",
        provider_model="test-model",
        status="completed",
        result_ciphertext=cipher,
        result_key_version=version,
        result_sha256=digest,
        review_status="unreviewed",
        provenance={
            "analysis_pipeline": "historical_case_attribution_v1",
            "episode_id": episode.id,
            "semantic_authority": "candidate_only",
        },
        completed_at=datetime(2026, 9, 21, 14, 40, 0),
    )
    db.session.add(attribution)
    db.session.flush()

    reconciliation_payload = {
        "schema_version": 1,
        "summary": "Advisor reconciled missing historical work.",
        "advisor_notice": "Confirm what actually happened.",
        "candidates": [
            {
                "candidate_id": "R001",
                "title": "Diagnostic scan",
                "kind": "service",
                "component_name": None,
                "component_location": None,
                "suggested_occurred_at": "2026-08-10T11:00:00",
                "occurred_at": "2026-08-10T11:00:00",
                "evidence_state": "completion_claim",
                "source_refs": ["CHAT m000308"],
                "evidence_basis": "Scan evidence exists.",
                "confidence": 0.98,
                "reconciliation_reason": "Not preserved in original record.",
                "advisor_decision": "confirmed",
                "component_condition": "not_applicable",
                "advisor_note": "Personally confirmed.",
            }
        ],
        "advisor_review_note": "Confirmed after historical review.",
    }
    cipher, version, digest = _payload_cipher(reconciliation_payload)
    reconciliation = EvidenceExtraction(
        evidence_id=corpus.id,
        extraction_type="historical_reconciliation",
        provider="test",
        provider_model="test-model",
        status="completed",
        result_ciphertext=cipher,
        result_key_version=version,
        result_sha256=digest,
        review_status="corrected",
        reviewed_by_user_id=admin.id,
        reviewed_at=datetime(2026, 9, 24, 8, 0, 0),
        reviewed_result_ciphertext=cipher,
        reviewed_result_key_version=version,
        reviewed_result_sha256=digest,
        provenance={
            "analysis_pipeline": "historical_episode_reconciliation_v1",
            "episode_id": episode.id,
            "attribution_extraction_id": attribution.id,
            "semantic_authority": "candidate_only",
        },
        completed_at=datetime(2026, 9, 21, 15, 0, 0),
    )
    db.session.add(reconciliation)
    db.session.flush()

    plan = TreatmentPlan(
        car_id=car.id,
        advisor_id=admin.id,
        title="Historical reconciliation — JOB-2026-002",
        client_summary="Advisor-confirmed historical work.",
        internal_instructions="Internal detail not needed in provider context.",
        status="completed",
        record_origin="historical_reconciliation",
        source_evidence_id=corpus.id,
        source_extraction_id=reconciliation.id,
        created_at=datetime(2026, 8, 10, 11, 0, 0),
        updated_at=datetime(2026, 8, 13, 16, 0, 0),
    )
    db.session.add(plan)
    db.session.flush()

    action = TreatmentAction(
        treatment_plan_id=plan.id,
        car_id=car.id,
        created_by_user_id=admin.id,
        creation_key="advisor360-diagnostic-scan",
        title="Diagnostic scan",
        client_summary="Electrical and AIRMATIC systems scanned.",
        internal_instructions="Private internal work note.",
        status="completed",
        visibility="advisor",
        completed_at=datetime(2026, 8, 10, 11, 0, 0),
        created_at=datetime(2026, 8, 10, 11, 0, 0),
        updated_at=datetime(2026, 8, 10, 11, 0, 0),
    )
    db.session.add(action)
    db.session.flush()

    detail = TreatmentActionCompletionDetail(
        treatment_action_id=action.id,
        action_kind="service",
        component_name=None,
        component_location=None,
        component_condition="not_applicable",
        source_evidence_id=corpus.id,
        verification_status="advisor_reconciled",
        verified_by_user_id=admin.id,
        verified_at=datetime(2026, 9, 24, 8, 5, 0),
    )
    addendum = TreatmentActionAddendum(
        treatment_action_id=action.id,
        created_by_user_id=admin.id,
        category="additional_information",
        reason="Later historical detail",
        visibility="advisor",
        detail_text="Panel beater detail retained as an immutable addendum.",
        idempotency_key="advisor360-addendum-1",
        created_at=datetime(2026, 9, 24, 8, 10, 0),
    )
    db.session.add_all([detail, addendum])

    event = VehicleEvent(
        car_id=car.id,
        ownership_id=ownership.id,
        event_type="treatment_action_completed",
        severity="low",
        title="Diagnostic scan completed",
        description="Longitudinal event description.",
        source="historical_reconciliation",
        fingerprint="3" * 64,
        schema_version=1,
        occurred_at=datetime(2026, 8, 10, 11, 0, 0),
        recorded_at=datetime(2026, 9, 24, 8, 5, 0),
        subject_type="treatment_action",
        subject_id=action.id,
        actor_type="user",
        actor_user_id=admin.id,
        actor_authority="administrator",
        visibility="advisor",
        new_state="completed",
        evidence_refs=[{"type": "vehicle_evidence", "id": corpus.id}],
        created_by=admin.id,
        created_at=datetime(2026, 9, 24, 8, 5, 0),
    )
    db.session.add(event)

    db.session.add(
        RinaAIAuditEvent(
            request_id="advisor360-audit-request-1",
            user_id=admin.id,
            car_id=car.id,
            authority="administrator",
            state="answered",
            outcome="answered",
            action_family="respond",
            provider="openai",
            provider_model="test-model",
            provider_status="ok",
            evidence_refs=[{"type": "vehicle_event", "id": 1}],
            audit_metadata={
                "channel": "in_app",
                "context_version": 1,
                "memory_policy": "vehicle_scoped_minimized_v1",
            },
            created_at=datetime(2026, 9, 24, 8, 20, 0),
        )
    )
    db.session.add(
        ProfileAuditEvent(
            user_id=owner.id,
            action="update",
            changed_fields=["care_preference", "preferred_communication"],
            request_id="profile-audit-1",
            success=True,
            created_at=datetime(2026, 9, 24, 8, 15, 0),
        )
    )
    db.session.commit()
    return owner, admin, car, action


def _memory(*, user_id: int, car_id: int, authority: str) -> RinaMemoryBundle:
    return RinaMemoryBundle(
        user_id=user_id,
        car_id=car_id,
        authority=authority,
        chat_history=(),
        summaries=(),
        advisor_memory=(),
    )


def _request(context) -> RinaRequest:
    return RinaRequest(
        request_id="advisor360-request",
        user_id=context.user_id,
        car_id=context.car_id,
        authority=context.authority,
        channel="in_app",
        message="Summarise the longitudinal care history.",
        conversation_id="advisor360-conversation",
        context_version=context.context_version,
        memory_policy="vehicle_scoped_minimized_v1",
        allowed_actions=context.allowed_actions,
        denied_actions=context.denied_actions,
    )


def _provider_json(provider_context) -> dict:
    first = provider_context.request.input_messages[0]["content"]
    marker = "instructions:\n"
    return json.loads(first.split(marker, 1)[1])


def test_advisor_360_defaults_off(monkeypatch):
    monkeypatch.delenv("RINA_ADVISOR_360_ENABLED", raising=False)
    assert rina_advisor_360_enabled() is False


def test_advisor_360_builds_compact_longitudinal_graph_for_admin(app):
    with app.app_context():
        owner, admin, car, action = _setup_longitudinal_case()
        context = resolve_rina_vehicle_context(user_id=admin.id, car_id=car.id)

        payload = build_rina_advisor_360_context(context)

        assert payload is not None
        assert payload["scope"]["car_id"] == car.id
        assert payload["client_relationship"]["owner"]["owner_user_id"] == owner.id
        safe_profile = payload["client_relationship"]["owner"]["safe_profile"]
        assert safe_profile["care_preference"] == (
            "Explain consequence before intervention."
        )
        assert "home_address" not in safe_profile
        assert "emergency_contact_phone" not in safe_profile

        episode = payload["historical_episodes"][0]
        assert episode["job_reference"] == "JOB-2026-002"
        assert episode["case_attribution"]["source_ref_counts"]["matched"] == 1
        assert episode["reconciliation"]["decision_counts"]["confirmed"] == 1

        plan = payload["treatment_history"][0]
        assert plan["record_origin"] == "historical_reconciliation"
        assert plan["actions"][0]["treatment_action_id"] == action.id
        assert plan["actions"][0]["completion_detail"]["verification_status"] == (
            "advisor_reconciled"
        )
        assert plan["actions"][0]["addenda"][0]["category"] == (
            "additional_information"
        )

        assert payload["evidence_index"]["active_count"] == 2
        assert payload["canonical_events"][0]["event_type"] == (
            "treatment_action_completed"
        )
        assert payload["audit"]["rina_ai_events"][0]["outcome"] == "answered"
        assert payload["audit"]["client_profile_events"][0]["changed_fields"] == [
            "care_preference",
            "preferred_communication",
        ]


def test_advisor_360_is_not_available_to_owner(app):
    with app.app_context():
        owner, _admin, car, _action = _setup_longitudinal_case()
        context = resolve_rina_vehicle_context(user_id=owner.id, car_id=car.id)
        assert build_rina_advisor_360_context(context) is None


def test_provider_context_includes_advisor_360_only_when_enabled(app, monkeypatch):
    with app.app_context():
        _owner, admin, car, _action = _setup_longitudinal_case()
        context = resolve_rina_vehicle_context(user_id=admin.id, car_id=car.id)

        monkeypatch.setenv("RINA_ADVISOR_360_ENABLED", "true")
        provider = build_rina_provider_context(
            rina_request=_request(context),
            context=context,
            memory=_memory(
                user_id=admin.id,
                car_id=car.id,
                authority=context.authority,
            ),
        )
        payload = _provider_json(provider)
        assert payload["advisor_360"]["historical_episodes"][0][
            "job_reference"
        ] == "JOB-2026-002"
        serialized = json.dumps(payload)
        assert "Private home address" not in serialized
        assert "Private Contact" not in serialized
        assert "Private internal work note" not in serialized

        monkeypatch.setenv("RINA_ADVISOR_360_ENABLED", "false")
        provider_off = build_rina_provider_context(
            rina_request=_request(context),
            context=context,
            memory=_memory(
                user_id=admin.id,
                car_id=car.id,
                authority=context.authority,
            ),
        )
        assert _provider_json(provider_off)["advisor_360"] is None
