"""Supervised conversational historical-record review through Ask Rina."""

from test_rina_chat_cutover import _car, _own, _post_json, _sign_in, _user

from evidence.models import EvidenceExtraction, VehicleEvidence
from extensions import db
from historical_ingestion.models import HistoricalServiceEpisode
from historical_ingestion.service import _payload_cipher
from models import TreatmentPlan
from rina.providers.base import RinaProviderError
from services.rina_historical_review import (
    HistoricalReviewInterpretation,
    HistoricalReviewInterpreter,
)
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


def _demola_may_correction() -> str:
    return (
        "Please correct the May 2026 workshop history for Mr Demola’s Mercedes. "
        "On 12 May 2026, Mr Demola reported that after driving the vehicle for a "
        "while, if the engine was switched off while hot, the vehicle would not "
        "restart until it had cooled for a few hours. I asked him to bring the "
        "vehicle to the workshop for assessment, and he came on 13 May 2026. "
        "During the workshop assessment, we found the starter motor was burnt/faulty "
        "and was the cause identified for the hard-start condition. The work completed "
        "by Ajebo Fix was: * starter motor replacement * engine oil change * engine "
        "oil filter replacement Please show me the structured historical draft first. "
        "Do not write this to durable history until I confirm and record it."
    )


def _start_uncertain_may_review(client, *, admin, car) -> None:
    _sign_in(client, admin)
    _post_json(client, "/chat/select-vehicle", {"car_id": car.id})
    started = _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": "Let's record the previous jobs on this Mercedes.",
        },
    )
    assert started.status_code == 200
    assert started.json["historical_review"]["phase"] == "reviewing"


def test_explicit_may_correction_survives_provider_under_extraction(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=320, role="admin")
    owner = _user(suffix=321)
    car = _car(suffix=320, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=320)
    _uncertain_historical_intelligence_source(admin=admin, car=car)
    _start_uncertain_may_review(client, admin=admin, car=car)

    def under_extract(_self, **_kwargs):
        return HistoricalReviewInterpretation(
            payload={
                "intent": "no_change",
                "changes": [],
                "additions": [],
                "episode_outcome": None,
                "assistant_note": "",
            },
            provider="test-provider",
            model="test-model",
            provider_request_id="req-under-extracted",
        )

    monkeypatch.setattr(HistoricalReviewInterpreter, "interpret", under_extract)

    corrected = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": _demola_may_correction()},
    )
    assert corrected.status_code == 200
    assert "Ready for your final review" in corrected.json["reply"]
    assert "May 2026 workshop history" in corrected.json["reply"]
    assert "tyre incident and resale discussion" not in corrected.json["reply"]
    assert "starter motor replacement" in corrected.json["reply"].lower()
    assert "engine oil change" in corrected.json["reply"].lower()
    assert "engine oil filter replacement" in corrected.json["reply"].lower()
    assert "2026-05-13" in corrected.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0

    applied = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": "Confirm and record"},
    )
    assert applied.status_code == 200
    plan = TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).one()
    actions = TreatmentAction.query.filter_by(treatment_plan_id=plan.id).all()
    assert {action.title.lower() for action in actions} == {
        "starter motor replacement",
        "engine oil change",
        "engine oil filter replacement",
    }


def test_explicit_completed_work_has_safe_fallback_when_provider_fails(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=322, role="admin")
    owner = _user(suffix=323)
    car = _car(suffix=322, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=322)
    _uncertain_historical_intelligence_source(admin=admin, car=car)
    _start_uncertain_may_review(client, admin=admin, car=car)

    def provider_failure(_self, **_kwargs):
        raise RinaProviderError("test provider unavailable")

    monkeypatch.setattr(HistoricalReviewInterpreter, "interpret", provider_failure)

    corrected = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": _demola_may_correction()},
    )
    assert corrected.status_code == 200
    assert corrected.json["state"] == "answered"
    assert "starter motor replacement" in corrected.json["reply"].lower()
    assert "engine oil change" in corrected.json["reply"].lower()
    assert "engine oil filter replacement" in corrected.json["reply"].lower()
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0


def test_historical_correction_does_not_cross_month_scopes(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=324, role="admin")
    owner = _user(suffix=325)
    car = _car(suffix=324, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=324)
    _uncertain_historical_intelligence_source(admin=admin, car=car)
    _start_uncertain_may_review(client, admin=admin, car=car)

    def should_not_call_provider(_self, **_kwargs):
        raise AssertionError("scope mismatch should stop before provider interpretation")

    monkeypatch.setattr(
        HistoricalReviewInterpreter,
        "interpret",
        should_not_call_provider,
    )

    response = _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": (
                "Please correct the June 2026 workshop history. "
                "The work completed was: * brake fluid service"
            ),
        },
    )
    assert response.status_code == 200
    assert "refers to June 2026" in response.json["reply"]
    assert "active historical draft is scoped to May 2026" in response.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0


def test_scope_acknowledgement_stays_in_may_review_without_provider(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=326, role="admin")
    owner = _user(suffix=327)
    car = _car(suffix=326, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=326)
    _uncertain_historical_intelligence_source(admin=admin, car=car)
    _start_uncertain_may_review(client, admin=admin, car=car)

    def should_not_call_provider(_self, **_kwargs):
        raise AssertionError("scope acknowledgement must not call the provider")

    monkeypatch.setattr(
        HistoricalReviewInterpreter,
        "interpret",
        should_not_call_provider,
    )

    response = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": "We are reviewing the May history"},
    )
    assert response.status_code == 200
    assert response.json["historical_review"]["phase"] == "reviewing"
    assert "This historical review is scoped to May 2026" in response.json["reply"]
    assert "Historical clarification" in response.json["reply"]
    assert "Ready for your final review" not in response.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0


def test_provider_no_change_does_not_show_final_review_while_intake_unresolved(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=328, role="admin")
    owner = _user(suffix=329)
    car = _car(suffix=328, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=328)
    _uncertain_historical_intelligence_source(admin=admin, car=car)
    _start_uncertain_may_review(client, admin=admin, car=car)

    interpretation = HistoricalReviewInterpretation(
        payload={
            "intent": "no_change",
            "changes": [],
            "additions": [],
            "episode_outcome": None,
            "assistant_note": "",
        },
        provider="test-provider",
        model="test-model",
        provider_request_id="req-no-change-unresolved",
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
            "message": "Okay, I understand the source context.",
        },
    )
    assert response.status_code == 200
    assert response.json["historical_review"]["phase"] == "reviewing"
    assert "Historical clarification" in response.json["reply"]
    assert "Ready for your final review" not in response.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0


def test_multiline_scope_acknowledgement_ignores_benign_trailing_line(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=330, role="admin")
    owner = _user(suffix=331)
    car = _car(suffix=330, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=330)
    _uncertain_historical_intelligence_source(admin=admin, car=car)
    _start_uncertain_may_review(client, admin=admin, car=car)

    def should_not_call_provider(_self, **_kwargs):
        raise AssertionError("benign multiline acknowledgement must stay deterministic")

    monkeypatch.setattr(
        HistoricalReviewInterpreter,
        "interpret",
        should_not_call_provider,
    )

    response = _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": "We are reviewing the May history\nOkay",
        },
    )
    assert response.status_code == 200
    assert response.json["historical_review"]["phase"] == "reviewing"
    assert "This historical review is scoped to May 2026" in response.json["reply"]
    assert "Historical clarification" in response.json["reply"]
    assert "Ready for your final review" not in response.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0


def _demola_grille_context_correction() -> str:
    return (
        "I need to add one more correction to the May 2026 workshop history before "
        "I confirm it.\n\n"
        "Later that night on 13 May 2026, we remembered that one of the chrome trim "
        "pieces on the front grille had fallen off in the past. A replacement chrome "
        "trim piece purchased.\n\n"
        "Because it was already late and the panel beaters had closed, the replacement "
        "chrome was not installed by Ajebo Fix that night. I advised Mr Demola to keep "
        "the chrome trim in the vehicle and take it to a panel beater close to his house "
        "the following day to have it properly bonded in place.\n\n"
        "The 'windscreen adhesive' mentioned in the WhatsApp history referred to the "
        "black adhesive commonly used by panel beaters for bonding windscreens, which I "
        "recommended for securing the grille chrome trim because other type of glues "
        "and adhesives didn't keep the chrome trim and that's why it fell off. It did "
        "not mean that the vehicle required windscreen repair or that windscreen work "
        "was performed.\n\n"
        "Please update the historical draft to reflect this distinction and show me the "
        "revised draft. Do not record anything yet."
    )


def test_noncompleted_grille_context_survives_provider_failure_without_extra_action(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=332, role="admin")
    owner = _user(suffix=333)
    car = _car(suffix=332, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=332)
    _uncertain_historical_intelligence_source(admin=admin, car=car)
    _start_uncertain_may_review(client, admin=admin, car=car)

    def provider_failure(_self, **_kwargs):
        raise RinaProviderError("test provider unavailable")

    monkeypatch.setattr(HistoricalReviewInterpreter, "interpret", provider_failure)

    initial = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": _demola_may_correction()},
    )
    assert initial.status_code == 200
    assert initial.json["historical_review"]["phase"] == "awaiting_apply_confirmation"
    assert "starter motor replacement" in initial.json["reply"].lower()

    corrected = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": _demola_grille_context_correction()},
    )
    assert corrected.status_code == 200
    assert corrected.json["historical_review"]["phase"] == "awaiting_apply_confirmation"
    reply = corrected.json["reply"].lower()
    assert "historical context — not completed work" in reply
    assert "replacement chrome trim piece purchased" in reply
    assert "not installed by ajebo fix" in reply
    assert "windscreen adhesive" in reply
    assert "windscreen repair" in reply
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0

    applied = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": "Confirm and record"},
    )
    assert applied.status_code == 200

    plan = TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).one()
    actions = TreatmentAction.query.filter_by(treatment_plan_id=plan.id).all()
    assert len(actions) == 3
    assert {action.title.lower() for action in actions} == {
        "starter motor replacement",
        "engine oil change",
        "engine oil filter replacement",
    }
    assert "not completed treatment work" in (plan.internal_instructions or "").lower()
    assert "replacement chrome trim piece purchased" in (
        plan.internal_instructions or ""
    ).lower()
    assert "windscreen adhesive" in (plan.internal_instructions or "").lower()
