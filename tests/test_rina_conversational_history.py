"""Supervised conversational historical-record review through Ask Rina."""

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


def _confirmation_interpretation(*, date="2026-05-18", candidate_id="R001"):
    return HistoricalReviewInterpretation(
        payload={
            "intent": "update",
            "changes": [
                {
                    "candidate_id": candidate_id,
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
            "episode_outcome": None,
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
            "episode_outcome": None,
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


def _historical_intelligence_source(*, admin, car):
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
        object_key=f"tests/intelligence-{car.id}.zip",
        safe_display_name="Historical WhatsApp export.zip",
        content_type="application/zip",
        byte_size=512,
        sha256=("b" * 63) + str(car.id % 10),
        consent_basis="advisor_whatsapp_case_import",
        lawful_purpose="vehicle_care_recordkeeping",
    )
    db.session.add(evidence)
    db.session.flush()

    payload = {
        "historical_intelligence_version": 2,
        "rina_summary": "Longitudinal history reconstructed from WhatsApp evidence.",
        "priority_threads": [],
        "candidates": [],
        "vehicle_candidates": [
            {
                "candidate_id": "V001",
                "identity_state": "selected_vehicle_match",
                "make_model_year": "2014 Mercedes-Benz GL 450",
            }
        ],
        "service_episode_candidates": [
            {
                "episode_candidate_id": "E001",
                "vehicle_candidate_id": "V001",
                "title": "Alternator replacement",
                "date_start": "2026-02-14",
                "date_end": "2026-02-14",
                "episode_state": "completed_work_supported",
                "summary": "Messages support a completed alternator replacement.",
                "reported_concerns": ["Charging concern"],
                "observations": [],
                "recommended_interventions": ["Replace alternator"],
                "authorized_interventions": ["Replace alternator"],
                "completed_interventions": ["Alternator replacement"],
                "outcomes": [],
                "source_refs": ["CHAT m000120", "CHAT m000127"],
                "source_excerpt": (
                    "[CHAT m000127] Alternator replaced and vehicle handed over."
                ),
                "confidence": 0.93,
                "separation_reason": "Distinct dated charging-system service episode.",
            }
        ],
        "canonical_comparisons": [
            {
                "episode_candidate_id": "E001",
                "comparison": "missing_from_durable_history",
                "matched_car_id": car.id,
                "matched_historical_episode_ids": [],
                "matched_treatment_action_ids": [],
                "already_represented_facts": [],
                "missing_facts": ["Alternator replacement"],
                "conflicts": [],
                "reason": "No equivalent completed Treatment Action exists.",
                "advisor_confirmation_required": True,
            }
        ],
    }
    cipher, version, digest = _payload_cipher(payload)
    extraction = EvidenceExtraction(
        evidence_id=evidence.id,
        extraction_type="structured_fields",
        provider="test",
        provider_model="test-model",
        status="completed",
        review_status="accepted",
        result_ciphertext=cipher,
        result_key_version=version,
        result_sha256=digest,
        reviewed_result_ciphertext=cipher,
        reviewed_result_key_version=version,
        reviewed_result_sha256=digest,
        provenance={
            "analysis_pipeline": "whatsapp_bundle_historical_intelligence_v2",
            "background_stage": "completed",
            "historical_intelligence_version": 2,
        },
    )
    db.session.add(extraction)
    db.session.commit()
    return evidence, extraction


def _uncertain_historical_intelligence_source(*, admin, car):
    evidence, extraction = _historical_intelligence_source(admin=admin, car=car)
    payload = {
        "historical_intelligence_version": 2,
        "rina_summary": "May interaction needs advisor clarification.",
        "priority_threads": [],
        "candidates": [],
        "vehicle_candidates": [
            {
                "candidate_id": "V001",
                "identity_state": "selected_vehicle_match",
                "make_model_year": "2014 Mercedes-Benz GL 450",
            }
        ],
        "service_episode_candidates": [
            {
                "episode_candidate_id": "E-MAY",
                "vehicle_candidate_id": "V001",
                "title": "May Mercedes-Benz maintenance payment, tyre incident and resale discussion",
                "date_start": "2026-05-01",
                "date_end": "2026-05-31",
                "episode_state": "uncertain",
                "summary": (
                    "The interaction is Mercedes-labelled, but the imported evidence "
                    "does not establish what work was actually completed."
                ),
                "reported_concerns": ["Tyre incident"],
                "observations": [],
                "recommended_interventions": [],
                "authorized_interventions": [],
                "completed_interventions": [],
                "outcomes": [],
                "source_refs": ["CHAT m000210", "CHAT m000222"],
                "source_excerpt": (
                    "[CHAT m000210] May Mercedes discussion includes maintenance/payment "
                    "context but does not prove completed work."
                ),
                "confidence": 0.71,
                "separation_reason": "Distinct May Mercedes interaction.",
            }
        ],
        "canonical_comparisons": [
            {
                "episode_candidate_id": "E-MAY",
                "comparison": "uncertain",
                "matched_car_id": car.id,
                "matched_historical_episode_ids": [],
                "matched_treatment_action_ids": [],
                "already_represented_facts": [],
                "missing_facts": [],
                "conflicts": [],
                "reason": "Completed work is not established by source evidence.",
                "advisor_confirmation_required": True,
            }
        ],
    }
    cipher, version, digest = _payload_cipher(payload)
    extraction.result_ciphertext = cipher
    extraction.result_key_version = version
    extraction.result_sha256 = digest
    extraction.reviewed_result_ciphertext = cipher
    extraction.reviewed_result_key_version = version
    extraction.reviewed_result_sha256 = digest
    db.session.commit()
    return evidence, extraction


def test_uncertain_may_episode_stays_in_chat_for_advisor_clarification(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=312, role="admin")
    owner = _user(suffix=313)
    car = _car(suffix=312, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=312)
    _uncertain_historical_intelligence_source(admin=admin, car=car)

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
    assert "Historical clarification" in started.json["reply"]
    assert "May Mercedes-Benz" in started.json["reply"]
    assert "does not prove what work was actually completed" in started.json["reply"]
    assert started.json["historical_review"]["phase"] == "reviewing"

    interpretation = HistoricalReviewInterpretation(
        payload={
            "intent": "update",
            "changes": [],
            "additions": [
                {
                    "title": "Engine oil and filter service",
                    "kind": "service",
                    "component_name": None,
                    "component_location": None,
                    "component_condition": "not_applicable",
                    "occurred_at": "2026-05-18",
                    "advisor_note": (
                        "Advisor confirmed this work belongs to the May Mercedes episode."
                    ),
                    "explicitly_completed": True,
                }
            ],
            "episode_outcome": "completed_work_described",
            "assistant_note": "",
        },
        provider="test-provider",
        model="test-model",
        provider_request_id="req-may-intake",
    )
    monkeypatch.setattr(
        "routes.chat.interpret_historical_review_turn",
        lambda **_kwargs: interpretation,
    )
    clarified = _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": (
                "Yes. On 18 May we changed the engine oil and filter on this GL450."
            ),
        },
    )
    assert clarified.status_code == 200
    assert "Ready for your final review" in clarified.json["reply"]
    assert "Engine oil and filter service" in clarified.json["reply"]
    assert "advisor-supplied correction" in clarified.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0

    applied = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": "Confirm and record"},
    )
    assert applied.status_code == 200
    assert "Recorded." in applied.json["reply"]
    plan = TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).one()
    action = TreatmentAction.query.filter_by(treatment_plan_id=plan.id).one()
    assert action.title == "Engine oil and filter service"
    assert action.status == "completed"
    assert action.completion_detail.source_evidence_id is None


def test_uncertain_episode_can_be_closed_without_writing_history(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=314, role="admin")
    owner = _user(suffix=315)
    car = _car(suffix=314, model="GL 450")
    _own(owner=owner, car=car, suffix=314)
    _uncertain_historical_intelligence_source(admin=admin, car=car)

    _sign_in(client, admin)
    _post_json(client, "/chat/select-vehicle", {"car_id": car.id})
    _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": "Let's record the previous jobs on this Mercedes.",
        },
    )

    interpretation = HistoricalReviewInterpretation(
        payload={
            "intent": "update",
            "changes": [],
            "additions": [],
            "episode_outcome": "no_completed_work",
            "assistant_note": "",
        },
        provider="test-provider",
        model="test-model",
        provider_request_id="req-may-no-work",
    )
    monkeypatch.setattr(
        "routes.chat.interpret_historical_review_turn",
        lambda **_kwargs: interpretation,
    )
    response = _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": "No actual work was completed in May.",
        },
    )
    assert response.status_code == 200
    assert "no completed work should be recorded" in response.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0


def test_chat_stages_historical_intelligence_episode_when_no_formal_reconciliation(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=308, role="admin")
    owner = _user(suffix=309)
    car = _car(suffix=308, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=308)
    evidence, source_extraction = _historical_intelligence_source(
        admin=admin,
        car=car,
    )

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
    assert "Alternator replacement" in started.json["reply"]
    assert "Item 1 of 1" in started.json["reply"]

    draft_id = started.json["historical_review"]["extraction_id"]
    draft = db.session.get(EvidenceExtraction, draft_id)
    assert draft is not None
    assert draft.evidence_id == evidence.id
    assert draft.extraction_type == "historical_reconciliation"
    assert draft.status == "completed"
    assert draft.review_status == "unreviewed"
    assert (
        draft.provenance["analysis_pipeline"]
        == "historical_intelligence_candidate_reconciliation_v1"
    )
    assert draft.provenance["source_structured_extraction_id"] == source_extraction.id
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0

    monkeypatch.setattr(
        "routes.chat.interpret_historical_review_turn",
        lambda **_kwargs: _confirmation_interpretation(
            date="2026-02-14",
            candidate_id="I001",
        ),
    )
    reviewed = _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": "Yes, the alternator was replaced on 14 February 2026.",
        },
    )
    assert reviewed.status_code == 200
    assert "Ready for your final review" in reviewed.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0

    applied = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": "Confirm and record"},
    )
    assert applied.status_code == 200
    assert "Recorded." in applied.json["reply"]

    plan = TreatmentPlan.query.filter_by(
        source_extraction_id=draft_id,
        record_origin="historical_reconciliation",
    ).one()
    assert plan.car_id == car.id
    action = TreatmentAction.query.filter_by(treatment_plan_id=plan.id).one()
    assert action.title == "Alternator replacement"
    assert action.status == "completed"


def test_historical_intelligence_bridge_rejects_other_vehicle_candidates(app, client):
    admin = _user(suffix=310, role="admin")
    owner = _user(suffix=311)
    car = _car(suffix=310, model="GL 450")
    _own(owner=owner, car=car, suffix=310)
    evidence, extraction = _historical_intelligence_source(admin=admin, car=car)

    payload = {
        "historical_intelligence_version": 2,
        "rina_summary": "Another vehicle is present in the corpus.",
        "priority_threads": [],
        "candidates": [],
        "vehicle_candidates": [
            {
                "candidate_id": "V999",
                "identity_state": "possible_other_vehicle",
                "make_model_year": "2018 Lexus RX 350",
            }
        ],
        "service_episode_candidates": [
            {
                "episode_candidate_id": "E999",
                "vehicle_candidate_id": "V999",
                "title": "Lexus brake service",
                "date_start": "2026-03-01",
                "date_end": "2026-03-01",
                "episode_state": "completed_work_supported",
                "summary": "Brake work for another vehicle.",
                "reported_concerns": [],
                "observations": [],
                "recommended_interventions": [],
                "authorized_interventions": [],
                "completed_interventions": ["Front brake pad replacement"],
                "outcomes": [],
                "source_refs": ["CHAT m000300"],
                "source_excerpt": "[CHAT m000300] Lexus front pads completed.",
                "confidence": 0.91,
                "separation_reason": "Distinct vehicle identity.",
            }
        ],
        "canonical_comparisons": [
            {
                "episode_candidate_id": "E999",
                "comparison": "belongs_to_other_vehicle",
                "matched_car_id": None,
                "matched_historical_episode_ids": [],
                "matched_treatment_action_ids": [],
                "already_represented_facts": [],
                "missing_facts": ["Front brake pad replacement"],
                "conflicts": [],
                "reason": "Evidence belongs to a different vehicle candidate.",
                "advisor_confirmation_required": True,
            }
        ],
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
    response = _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": "Rina, record the previous jobs you found on this vehicle.",
        },
    )
    assert response.status_code == 200
    assert "won't invent one" in response.json["reply"] or (
        "pull work across from another vehicle" in response.json["reply"]
    )
    direct = (
        EvidenceExtraction.query.filter_by(
            evidence_id=evidence.id,
            extraction_type="historical_reconciliation",
        )
        .all()
    )
    assert direct == []
