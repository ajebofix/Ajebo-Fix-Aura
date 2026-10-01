"""Supervised conversational historical-record review through Ask Rina."""

from types import SimpleNamespace

from test_rina_chat_cutover import _car, _own, _post_json, _sign_in, _user

from evidence.models import EvidenceExtraction, VehicleEvidence
from extensions import db
from historical_ingestion.models import HistoricalServiceEpisode
from historical_ingestion.service import _payload_cipher
from models import TreatmentPlan
from services.rina_historical_review import HistoricalReviewInterpretation
from treatment.models import TreatmentAction


def _historical_reconciliation(*, admin, car):
    evidence = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=admin.id,
        evidence_type="archive",
        purpose="vehicle_history_context",
        source_channel="whatsapp",
        historical_source_type="whatsapp_conversation",
        visibility="advisor",
        review_status="accepted",
        storage_provider="test-private",
        storage_state="available",
        object_key=f"tests/history-{car.id}.zip",
        safe_display_name="Historical WhatsApp case.zip",
        content_type="application/zip",
        byte_size=256,
        sha256=("a" * 63) + str(car.id % 10),
        consent_basis="advisor_whatsapp_case_import",
        lawful_purpose="vehicle_care_recordkeeping",
    )
    db.session.add(evidence)
    db.session.flush()

    anchor = EvidenceExtraction(
        evidence_id=evidence.id,
        extraction_type="structured_fields",
        provider="test",
        provider_model="test-model",
        status="completed",
        review_status="accepted",
    )
    db.session.add(anchor)
    db.session.flush()

    episode = HistoricalServiceEpisode(
        car_id=car.id,
        anchor_evidence_id=evidence.id,
        anchor_extraction_id=anchor.id,
        created_by_user_id=admin.id,
        title="May Mercedes-Benz maintenance",
        job_reference="HIST-2026-05",
    )
    db.session.add(episode)
    db.session.flush()

    payload = {
        "schema_version": 1,
        "summary": "Source evidence suggests a prior maintenance interaction.",
        "advisor_notice": "Human confirmation required.",
        "candidates": [
            {
                "candidate_id": "R001",
                "title": "Engine oil service",
                "kind": "service",
                "component_name": None,
                "component_location": None,
                "suggested_occurred_at": "2026-05-18",
                "evidence_state": "completion_claim",
                "source_refs": ["CHAT m000001"],
                "evidence_basis": "WhatsApp messages discuss the completed oil service.",
                "confidence": 0.86,
                "reconciliation_reason": "Not represented in durable history.",
                "advisor_decision": "unsure",
                "component_condition": "not_applicable",
                "advisor_note": "",
            }
        ],
    }
    cipher, version, digest = _payload_cipher(payload)
    reconciliation = EvidenceExtraction(
        evidence_id=evidence.id,
        extraction_type="historical_reconciliation",
        provider="test",
        provider_model="test-model",
        status="completed",
        review_status="unreviewed",
        result_ciphertext=cipher,
        result_key_version=version,
        result_sha256=digest,
        provenance={
            "analysis_pipeline": "historical_episode_reconciliation_v1",
            "background_stage": "completed",
            "episode_id": episode.id,
            "anchor_evidence_id": evidence.id,
            "anchor_extraction_id": anchor.id,
            "attribution_extraction_id": anchor.id,
            "semantic_authority": "candidate_only",
            "schema_version": 1,
        },
    )
    db.session.add(reconciliation)
    db.session.commit()
    return episode, reconciliation


def _confirmation_interpretation(*, date="2026-05-18"):
    return HistoricalReviewInterpretation(
        payload={
            "intent": "update",
            "changes": [
                {
                    "candidate_id": "R001",
                    "mark_reviewed": True,
                    "advisor_decision": "confirmed",
                    "occurred_at": date,
                    "component_condition": None,
                    "advisor_note": "Advisor confirmed the completed service.",
                    "title": None,
                    "kind": None,
                    "component_name": None,
                    "component_location": None,
                }
            ],
            "additions": [],
            "assistant_note": "",
        },
        provider="test-provider",
        model="test-model",
        provider_request_id="req-history-review",
    )


def test_admin_can_review_and_record_historical_work_entirely_in_chat(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=301, role="admin")
    owner = _user(suffix=302)
    car = _car(suffix=301, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=301)
    _, reconciliation = _historical_reconciliation(admin=admin, car=car)

    _sign_in(client, admin)
    _post_json(client, "/chat/select-vehicle", {"car_id": car.id})

    started = _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": "Rina, let's record the previous jobs you found on this Mercedes.",
        },
    )
    assert started.status_code == 200
    assert started.json["intent"] == "historical_review"
    assert "Item 1 of 1" in started.json["reply"]
    assert "Engine oil service" in started.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0

    monkeypatch.setattr(
        "routes.chat.interpret_historical_review_turn",
        lambda **_kwargs: _confirmation_interpretation(),
    )
    reviewed = _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": "Yes, that service was completed on 18 May 2026.",
        },
    )
    assert reviewed.status_code == 200
    assert "Ready for your final review" in reviewed.json["reply"]
    assert "Confirm and record" in reviewed.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0

    db.session.refresh(reconciliation)
    assert reconciliation.review_status == "corrected"

    applied = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": "Confirm and record"},
    )
    assert applied.status_code == 200
    assert "Recorded." in applied.json["reply"]
    assert applied.json["historical_review"]["applied_plan_id"] is not None

    plan = TreatmentPlan.query.filter_by(
        source_extraction_id=reconciliation.id,
        record_origin="historical_reconciliation",
    ).one()
    assert plan.status == "completed"
    action = TreatmentAction.query.filter_by(treatment_plan_id=plan.id).one()
    assert action.title == "Engine oil service"
    assert action.status == "completed"


def test_chat_never_applies_unreviewed_history_from_record_it(app, client):
    admin = _user(suffix=303, role="admin")
    owner = _user(suffix=304)
    car = _car(suffix=303, model="GL 450")
    _own(owner=owner, car=car, suffix=303)
    _historical_reconciliation(admin=admin, car=car)

    _sign_in(client, admin)
    _post_json(client, "/chat/select-vehicle", {"car_id": car.id})
    _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": "Let's record the previous jobs."},
    )

    blocked = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": "Record it"},
    )
    assert blocked.status_code == 200
    assert "still need your decision" in blocked.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0


def test_owner_cannot_enter_conversational_historical_write_flow(app, client):
    owner = _user(suffix=305)
    car = _car(suffix=305, model="GL 450")
    _own(owner=owner, car=car, suffix=305)
    db.session.commit()
    _sign_in(client, owner)

    response = _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": "Rina, record the previous jobs on this vehicle.",
        },
    )
    assert response.status_code == 403
    assert response.json["state"] == "authority_denied"


def test_confirmed_work_without_date_stays_draft_until_date_is_supplied(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=306, role="admin")
    owner = _user(suffix=307)
    car = _car(suffix=306, model="GL 450")
    _own(owner=owner, car=car, suffix=306)
    _historical_reconciliation(admin=admin, car=car)

    _sign_in(client, admin)
    _post_json(client, "/chat/select-vehicle", {"car_id": car.id})
    _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": "Review the previous jobs with me."},
    )

    no_date = HistoricalReviewInterpretation(
        payload={
            "intent": "update",
            "changes": [
                {
                    "candidate_id": "R001",
                    "mark_reviewed": True,
                    "advisor_decision": "confirmed",
                    "occurred_at": None,
                    "component_condition": None,
                    "advisor_note": None,
                    "title": None,
                    "kind": None,
                    "component_name": None,
                    "component_location": None,
                }
            ],
            "additions": [],
            "assistant_note": "",
        },
        provider="test-provider",
        model="test-model",
        provider_request_id="req-no-date",
    )
    monkeypatch.setattr(
        "routes.chat.interpret_historical_review_turn",
        lambda **_kwargs: no_date,
    )
    response = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": "Yes, it was done."},
    )
    assert response.status_code == 200
    assert "still need the historical date" in response.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0
