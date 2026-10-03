"""Regress exact episode switching and standalone intake semantics."""

from test_rina_chat_cutover import _car, _csrf_token, _own, _post_json, _sign_in, _user
from test_rina_conversational_history import (
    _historical_intelligence_source,
    _standalone_document_analysis,
)

from evidence.models import EvidenceExtraction
from extensions import db
from historical_ingestion.reconciliation import reconciliation_payload
from historical_ingestion.service import _payload_cipher
from models import ChatMessage, TreatmentPlan


def test_explicit_whatsapp_episode_request_switches_active_draft(app, client):
    admin = _user(suffix=360, role="admin")
    owner = _user(suffix=361)
    car = _car(suffix=360, model="GLK 350")
    car.year = 2013
    _own(owner=owner, car=car, suffix=360)

    standalone_evidence, _ = _standalone_document_analysis(admin=admin, car=car)
    whatsapp_evidence, extraction = _historical_intelligence_source(
        admin=admin,
        car=car,
    )
    payload = {
        "historical_intelligence_version": 2,
        "rina_summary": "July steering history.",
        "priority_threads": [],
        "candidates": [],
        "vehicle_candidates": [{
            "candidate_id": "V001",
            "identity_state": "selected_vehicle_match",
            "make_model_year": "2013 Mercedes-Benz GLK 350",
        }],
        "service_episode_candidates": [{
            "episode_candidate_id": "E-JULY",
            "vehicle_candidate_id": "V001",
            "title": "25 July 2026 power-steering episode",
            "date_start": "2026-07-25",
            "date_end": "2026-07-25",
            "episode_state": "uncertain",
            "summary": "The July steering episode needs advisor clarification.",
            "reported_concerns": ["Power Steering Malfunction"],
            "observations": [],
            "recommended_interventions": [],
            "authorized_interventions": [],
            "completed_interventions": [],
            "outcomes": [],
            "source_refs": ["CHAT m000010"],
            "source_excerpt": "[CHAT m000010] Power Steering Malfunction reported.",
            "confidence": 0.82,
            "separation_reason": "Distinct July steering episode.",
        }],
        "canonical_comparisons": [{
            "episode_candidate_id": "E-JULY",
            "comparison": "uncertain",
            "matched_car_id": car.id,
            "matched_historical_episode_ids": [],
            "matched_treatment_action_ids": [],
            "already_represented_facts": [],
            "missing_facts": [],
            "conflicts": [],
            "reason": "Completion is not established.",
            "advisor_confirmation_required": True,
        }],
    }
    cipher, version, digest = _payload_cipher(payload)
    extraction.result_ciphertext = cipher
    extraction.result_key_version = version
    extraction.result_sha256 = digest
    extraction.reviewed_result_ciphertext = cipher
    extraction.reviewed_result_key_version = version
    extraction.reviewed_result_sha256 = digest
    db.session.commit()

    _sign_in(client, admin)
    response = client.post(
        "/chat/historical-review/from-source",
        data={
            "csrf_token": _csrf_token(client),
            "car_id": car.id,
            "evidence_id": standalone_evidence.id,
        },
    )
    assert response.status_code == 302

    switched = _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": (
                "Review the 25 July 2026 power-steering episode from "
                f"WhatsApp Evidence #{whatsapp_evidence.id}."
            ),
        },
    )
    assert switched.status_code == 200
    assert "Historical clarification" in switched.json["reply"]
    assert "25 July 2026 power-steering episode" in switched.json["reply"]
    assert "June 2026 GLK workshop record" not in switched.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0


def test_unproven_standalone_scope_enters_intake(app, client):
    admin = _user(suffix=362, role="admin")
    owner = _user(suffix=363)
    car = _car(suffix=362, model="GLK 350")
    car.year = 2013
    _own(owner=owner, car=car, suffix=362)
    evidence, structured = _standalone_document_analysis(admin=admin, car=car)

    payload = {
        "document": {
            "document_type": "receipt",
            "title": "25 July 2026 receipt",
            "reference": "RCPT-0725",
            "document_date": "2026-07-25",
            "job_reference": None,
            "sow_reference": None,
            "client_name": "Historical Client",
            "vehicle_description": "2013 Mercedes-Benz GLK 350",
            "vin": car.vin,
            "plate_number": None,
        },
        "rina_summary": (
            "The receipt proves payment for intervention scope but not completion."
        ),
        "advisor_suggestions": ["Ask what work was actually completed."],
        "candidates": [{
            "category": "work_item",
            "state": "authorized",
            "title": "Paid intervention scope without completion evidence",
            "detail": "Payment is established; completed work is not.",
            "occurred_at": "2026-07-25T15:37:00",
            "source_pages": [1],
            "source_fact_ids": ["FACT-PAYMENT-001"],
            "source_excerpt": "Receipt shows payment for listed scope.",
            "confidence": 0.91,
            "confidence_reason": "Payment is explicit; completion is not.",
            "suggested_destination": "treatment_action",
            "outcome_direction": "insufficient_evidence",
            "advisor_attention": "Professional confirmation required.",
            "action": {
                "kind": "other_intervention",
                "component_name": None,
                "component_location": None,
                "component_condition": "not_applicable",
                "quantity": None,
                "odometer_km": None,
            },
        }],
    }
    cipher, version, digest = _payload_cipher(payload)
    structured.result_ciphertext = cipher
    structured.result_key_version = version
    structured.result_sha256 = digest
    db.session.commit()

    _sign_in(client, admin)
    response = client.post(
        "/chat/historical-review/from-source",
        data={
            "csrf_token": _csrf_token(client),
            "car_id": car.id,
            "evidence_id": evidence.id,
        },
    )
    assert response.status_code == 302

    draft = (
        EvidenceExtraction.query.filter_by(
            evidence_id=evidence.id,
            extraction_type="historical_reconciliation",
        )
        .order_by(EvidenceExtraction.id.desc())
        .first()
    )
    staged = reconciliation_payload(draft)
    assert staged["candidates"] == []
    assert staged["advisor_intake_required"] is True

    prompt = (
        ChatMessage.query.filter_by(
            user_id=admin.id,
            car_id=car.id,
            role="assistant",
        )
        .order_by(ChatMessage.id.desc())
        .first()
    )
    assert "Historical clarification" in prompt.message
    assert "Paid intervention scope without completion evidence" not in prompt.message
