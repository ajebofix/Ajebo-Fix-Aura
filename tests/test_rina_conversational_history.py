"""Supervised conversational historical-record review through Ask Rina."""

from test_rina_chat_cutover import (
    _car,
    _csrf_token,
    _own,
    _post_json,
    _sign_in,
    _user,
)

from evidence.models import EvidenceExtraction, VehicleEvidence
from extensions import db
from historical_ingestion.models import HistoricalServiceEpisode
from historical_ingestion.reconciliation import (
    reconciliation_payload,
    save_reconciliation_review,
)
from historical_ingestion.service import _payload_cipher
from models import ChatMessage, TreatmentPlan
from rina.providers.base import RinaProviderError
from services.rina_context_resolver import resolve_rina_vehicle_context
from services.rina_historical_review import (
    HistoricalReviewInterpretation,
    HistoricalReviewInterpreter,
    discover_review_choices,
)
from treatment.models import TreatmentAction, TreatmentActionCompletionDetail


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
    assert "now talking about" in response.json["reply"]
    assert "Should I switch to that episode?" in response.json["reply"]
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0

    switched = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": "Yes"},
    )
    assert switched.status_code == 200
    assert "June" in switched.json["reply"]
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


def _demola_remaining_context_correction() -> str:
    return (
        "I want to add the remaining historical context to this May 2026 episode before "
        "I confirm and record it.\n\n"
        "Reported concern — 12 May 2026: Mr Demola reported that after driving the "
        "vehicle for a while, if the engine was switched off while hot, the vehicle "
        "would not restart until it had cooled for a few hours.\n\n"
        "Workshop assessment — 13 May 2026: During the assessment, the starter motor "
        "was found to be burnt/faulty and was identified as the cause of the hard-start "
        "condition. This finding led to the starter motor replacement already shown in "
        "the completed-work section, and the starter motor replaced was a brand new "
        "Bosch starter motor.\n\n"
        "Tyre incident — 13 May 2026: While Mr Demola was driving to the workshop, one "
        "of the vehicle's tyres blew out. He replaced the blown tyre with a used tyre "
        "from a vulcaniser before arriving at the workshop. Ajebo Fix did not perform "
        "this tyre replacement.\n\n"
        "Client/advisor context: Mr Demola said he intended to sell this Mercedes-Benz "
        "and purchase another Mercedes-Benz, so we discussed the vehicle's resale "
        "valuation. This was a discussion, not a mechanical intervention.\n\n"
        "Please add these as historical context and show me the revised final draft. "
        "Do not record anything yet."
    )


def test_labeled_context_wins_over_keyword_heuristics_and_preserves_bosch_detail(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=334, role="admin")
    owner = _user(suffix=335)
    car = _car(suffix=334, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=334)
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

    grille = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": _demola_grille_context_correction()},
    )
    assert grille.status_code == 200

    context = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": _demola_remaining_context_correction()},
    )
    assert context.status_code == 200
    assert context.json["historical_review"]["phase"] == "awaiting_apply_confirmation"
    reply = context.json["reply"].lower()
    assert "reported concern" in reply
    assert "assessment finding" in reply
    assert "external event" in reply
    assert "client advisor context" in reply
    assert "hot" in reply and "would not restart" in reply
    assert "burnt/faulty" in reply
    assert "tyres blew out" in reply
    assert "used tyre from a vulcaniser" in reply
    assert "ajebo fix did not perform this tyre replacement" in reply
    assert "resale valuation" in reply
    assert "procurement" in reply  # grille procurement still remains
    assert "client/advisor context" not in (
        "\n".join(
            line
            for line in reply.splitlines()
            if line.strip().startswith("- **procurement**")
        )
    )
    assert "bosch starter motor" in reply
    assert "· new" in reply

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

    starter = next(
        action for action in actions if action.title.lower() == "starter motor replacement"
    )
    detail = TreatmentActionCompletionDetail.query.filter_by(
        treatment_action_id=starter.id
    ).one()
    assert detail.component_name == "Bosch starter motor"
    assert detail.component_condition == "new"

    instructions = (plan.internal_instructions or "").lower()
    assert "reported_concern" in instructions
    assert "assessment_finding" in instructions
    assert "external_event" in instructions
    assert "client_advisor_context" in instructions
    assert "resale valuation" in instructions


def test_explicit_context_reclassifies_existing_persisted_wrong_category(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=336, role="admin")
    owner = _user(suffix=337)
    car = _car(suffix=336, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=336)
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

    grille = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": _demola_grille_context_correction()},
    )
    assert grille.status_code == 200

    extraction_id = grille.json["historical_review"]["extraction_id"]
    extraction = db.session.get(EvidenceExtraction, extraction_id)
    reviewed = reconciliation_payload(extraction, reviewed=True)
    bad_note = (
        "Client/advisor context: Mr Demola said he intended to sell this Mercedes-Benz "
        "and purchase another Mercedes-Benz, so we discussed the vehicle's resale "
        "valuation. This was a discussion, not a mechanical intervention."
    )
    reviewed.setdefault("advisor_context_notes", []).append(
        {
            "category": "procurement",
            "occurred_at": "2026-05-13",
            "note": bad_note,
            "advisor_supplied": True,
        }
    )
    save_reconciliation_review(
        extraction_id=extraction_id,
        actor_user_id=admin.id,
        reviewed_payload=reviewed,
    )
    db.session.commit()

    corrected = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": _demola_remaining_context_correction()},
    )
    assert corrected.status_code == 200
    reply = corrected.json["reply"].lower()

    client_context_lines = [
        line
        for line in reply.splitlines()
        if "client advisor context" in line or "client/advisor context" in line
    ]
    assert client_context_lines
    assert any("client advisor context" in line for line in client_context_lines)

    procurement_lines = [
        line
        for line in reply.splitlines()
        if line.strip().startswith("- **procurement**")
    ]
    assert not any("resale valuation" in line for line in procurement_lines)

    extraction = db.session.get(EvidenceExtraction, extraction_id)
    repaired = reconciliation_payload(extraction, reviewed=True)
    matching = [
        row
        for row in repaired.get("advisor_context_notes") or []
        if "resale valuation" in str(row.get("note") or "").lower()
    ]
    assert len(matching) == 1
    assert matching[0]["category"] == "client_advisor_context"
    assert matching[0]["occurred_at"] is None
    assert not str(matching[0]["note"]).lower().startswith("client/advisor context")


def test_inline_mobile_labeled_context_is_extracted_and_repairs_shorter_old_row(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=338, role="admin")
    owner = _user(suffix=339)
    car = _car(suffix=338, model="GL 450")
    car.year = 2014
    _own(owner=owner, car=car, suffix=338)
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

    grille = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": _demola_grille_context_correction()},
    )
    assert grille.status_code == 200

    extraction_id = grille.json["historical_review"]["extraction_id"]
    extraction = db.session.get(EvidenceExtraction, extraction_id)
    reviewed = reconciliation_payload(extraction, reviewed=True)
    shorter_bad_note = (
        "Client/advisor context: Mr Demola said he intended to sell this Mercedes-Benz "
        "and purchase another Mercedes-Benz, so we discussed the vehicle's resale "
        "valuation."
    )
    reviewed.setdefault("advisor_context_notes", []).append(
        {
            "category": "procurement",
            "occurred_at": "2026-05-13",
            "note": shorter_bad_note,
            "advisor_supplied": True,
        }
    )
    save_reconciliation_review(
        extraction_id=extraction_id,
        actor_user_id=admin.id,
        reviewed_payload=reviewed,
    )
    db.session.commit()

    inline_message = _demola_remaining_context_correction().replace("\n\n", " ")
    corrected = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": inline_message},
    )
    assert corrected.status_code == 200
    assert corrected.json["historical_review"]["phase"] == "awaiting_apply_confirmation"

    reply = corrected.json["reply"].lower()
    assert "reported concern" in reply
    assert "assessment finding" in reply
    assert "external event" in reply
    assert "client advisor context" in reply
    assert "would not restart until it had cooled for a few hours" in reply
    assert "burnt/faulty" in reply
    assert "used tyre from a vulcaniser" in reply
    assert "resale valuation" in reply
    assert "bosch starter motor" in reply
    assert "· new" in reply

    procurement_lines = [
        line
        for line in reply.splitlines()
        if line.strip().startswith("- **procurement**")
    ]
    assert not any("resale valuation" in line for line in procurement_lines)

    extraction = db.session.get(EvidenceExtraction, extraction_id)
    repaired = reconciliation_payload(extraction, reviewed=True)
    resale_rows = [
        row
        for row in repaired.get("advisor_context_notes") or []
        if "resale valuation" in str(row.get("note") or "").lower()
    ]
    assert len(resale_rows) == 1
    assert resale_rows[0]["category"] == "client_advisor_context"
    assert resale_rows[0]["occurred_at"] is None

    categories = {
        row.get("category")
        for row in repaired.get("advisor_context_notes") or []
        if isinstance(row, dict)
    }
    assert {
        "reported_concern",
        "assessment_finding",
        "external_event",
        "client_advisor_context",
    }.issubset(categories)

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


def _standalone_document_analysis(*, admin, car):
    evidence = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=admin.id,
        evidence_type="document",
        purpose="treatment_evidence",
        source_channel="web",
        historical_source_type="standalone_document",
        visibility="advisor",
        review_status="pending_review",
        storage_provider="test-private",
        storage_state="available",
        object_key=f"tests/standalone-history-{car.id}.pdf",
        safe_display_name="Historical workshop record.pdf",
        content_type="application/pdf",
        byte_size=512,
        sha256=("c" * 63) + str(car.id % 10),
        consent_basis="advisor_historical_document_import",
        lawful_purpose="vehicle_care_recordkeeping",
    )
    db.session.add(evidence)
    db.session.flush()

    payload = {
        "document": {
            "document_type": "job_record",
            "title": "June 2026 GLK workshop record",
            "reference": "JOB-GLK-0626",
            "document_date": "2026-06-18",
            "job_reference": "JOB-GLK-0626",
            "sow_reference": None,
            "client_name": "Historical Client",
            "vehicle_description": "2013 Mercedes-Benz GLK 350",
            "vin": car.vin,
            "plate_number": None,
        },
        "rina_summary": (
            "The source describes a hot-start complaint and a claimed starter-motor "
            "replacement. Advisor confirmation is still required."
        ),
        "advisor_suggestions": [
            "Confirm what work was actually completed and the historical date."
        ],
        "candidates": [
            {
                "category": "reported_concern",
                "state": "reported",
                "title": "Hot-start concern",
                "detail": "Vehicle would not restart while hot.",
                "occurred_at": "2026-06-17",
                "source_pages": [1],
                "source_fact_ids": ["FACT-001"],
                "source_excerpt": "Vehicle would not restart while hot.",
                "confidence": 0.97,
                "confidence_reason": "Explicitly stated in the source.",
                "suggested_destination": "reported_concern",
                "outcome_direction": "insufficient_evidence",
                "advisor_attention": "Context only.",
                "action": None,
            },
            {
                "category": "work_item",
                "state": "completed",
                "title": "Starter motor replacement",
                "detail": "The document states that the starter motor was replaced.",
                "occurred_at": "2026-06-18",
                "source_pages": [2],
                "source_fact_ids": ["FACT-002"],
                "source_excerpt": "Starter motor replaced.",
                "confidence": 0.94,
                "confidence_reason": "Completion wording is explicit in the source.",
                "suggested_destination": "treatment_action",
                "outcome_direction": "insufficient_evidence",
                "advisor_attention": "Confirm completion before recording.",
                "action": {
                    "kind": "component_replacement",
                    "component_name": "Bosch starter motor",
                    "component_location": None,
                    "component_condition": "new",
                    "quantity": 1,
                    "odometer_km": None,
                },
            },
        ],
    }
    cipher, version, digest = _payload_cipher(payload)
    structured = EvidenceExtraction(
        evidence_id=evidence.id,
        extraction_type="structured_fields",
        provider="test",
        provider_model="test-model",
        status="completed",
        review_status="unreviewed",
        result_ciphertext=cipher,
        result_key_version=version,
        result_sha256=digest,
    )
    db.session.add(structured)
    db.session.commit()
    return evidence, structured


def test_ready_standalone_analysis_hands_off_into_supervised_rina_chat(
    app,
    client,
    monkeypatch,
):
    admin = _user(suffix=340, role="admin")
    owner = _user(suffix=341)
    car = _car(suffix=340, model="GLK 350")
    car.year = 2013
    _own(owner=owner, car=car, suffix=340)
    evidence, structured = _standalone_document_analysis(admin=admin, car=car)

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
    assert f"/chat/workspace?car_id={car.id}" in response.headers["Location"]

    draft = (
        EvidenceExtraction.query.filter_by(
            evidence_id=evidence.id,
            extraction_type="historical_reconciliation",
        )
        .order_by(EvidenceExtraction.id.desc())
        .first()
    )
    assert draft is not None
    assert draft.status == "completed"
    assert draft.review_status == "unreviewed"
    assert draft.provenance["analysis_pipeline"] == (
        "standalone_document_conversational_review_v1"
    )
    assert draft.provenance["source_structured_extraction_id"] == structured.id

    staged = reconciliation_payload(draft)
    assert staged["episode_title"] == "June 2026 GLK workshop record"
    assert len(staged["candidates"]) == 1
    candidate = staged["candidates"][0]
    assert candidate["candidate_id"] == "D001"
    assert candidate["title"] == "Starter motor replacement"
    assert candidate["advisor_decision"] == "unsure"
    assert candidate["reviewed_by_advisor"] is False
    assert candidate["component_name"] == "Bosch starter motor"
    assert candidate["component_condition"] == "new"

    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0
    assert TreatmentAction.query.count() == 0

    prompt = (
        ChatMessage.query.filter_by(
            user_id=admin.id,
            car_id=car.id,
            role="assistant",
        )
        .order_by(ChatMessage.id.desc())
        .first()
    )
    assert prompt is not None
    assert "Starter motor replacement" in prompt.message
    assert "Nothing becomes durable vehicle history" in prompt.message

    context = resolve_rina_vehicle_context(user_id=admin.id, car_id=car.id)
    choices = discover_review_choices(context)
    assert any(item["extraction_id"] == draft.id for item in choices)

    monkeypatch.setattr(
        "routes.chat.interpret_historical_review_turn",
        lambda **_kwargs: _confirmation_interpretation(
            date="2026-06-18",
            candidate_id="D001",
        ),
    )
    reviewed = _post_json(
        client,
        "/chat",
        {
            "car_id": car.id,
            "message": (
                "Yes, the starter motor replacement was completed on 18 June 2026."
            ),
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json["historical_review"]["phase"] == "awaiting_apply_confirmation"
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
    assert len(actions) == 1
    assert actions[0].title == "Starter motor replacement"

    detail = TreatmentActionCompletionDetail.query.filter_by(
        treatment_action_id=actions[0].id
    ).one()
    assert detail.component_name == "Bosch starter motor"
    assert detail.component_condition == "new"


def test_whatsapp_ready_review_handoff_lists_only_selected_vehicle_episodes(
    app,
    client,
):
    admin = _user(suffix=350, role="admin")
    owner = _user(suffix=351)
    car = _car(suffix=350, model="GLK 350")
    car.year = 2013
    _own(owner=owner, car=car, suffix=350)
    evidence, extraction = _historical_intelligence_source(admin=admin, car=car)

    payload = {
        "historical_intelligence_version": 2,
        "rina_summary": "Longitudinal WhatsApp history across two vehicles.",
        "priority_threads": [],
        "candidates": [],
        "vehicle_candidates": [
            {
                "candidate_id": "V001",
                "identity_state": "selected_vehicle_match",
                "make_model_year": "2013 Mercedes-Benz GLK 350",
            },
            {
                "candidate_id": "V002",
                "identity_state": "possible_other_vehicle",
                "make_model_year": "Geely Azkarra",
            },
        ],
        "service_episode_candidates": [
            {
                "episode_candidate_id": "E001",
                "vehicle_candidate_id": "V001",
                "title": "Damaged belt replacement",
                "date_start": "2026-07-25",
                "date_end": "2026-07-25",
                "episode_state": "completed_work",
                "summary": "The bundle supports completed belt replacement.",
                "reported_concerns": ["Damaged belt"],
                "observations": [],
                "recommended_interventions": ["Replace belt"],
                "authorized_interventions": ["Replace belt"],
                "completed_interventions": ["Damaged belt replacement"],
                "outcomes": [],
                "source_refs": ["CHAT m000120", "IMAGE evidence:102"],
                "source_excerpt": "[CHAT m000120] Belt changed and vehicle handed over.",
                "confidence": 0.95,
                "separation_reason": "Distinct July GLK service episode.",
            },
            {
                "episode_candidate_id": "E002",
                "vehicle_candidate_id": "V001",
                "title": "Suspension work",
                "date_start": "2026-08-10",
                "date_end": "2026-08-10",
                "episode_state": "uncertain",
                "summary": "Suspension work needs advisor clarification.",
                "reported_concerns": ["Suspension noise"],
                "observations": [],
                "recommended_interventions": ["Suspension repair"],
                "authorized_interventions": [],
                "completed_interventions": [],
                "outcomes": [],
                "source_refs": ["CHAT m000240"],
                "source_excerpt": "[CHAT m000240] Suspension discussion continued.",
                "confidence": 0.76,
                "separation_reason": "Distinct August GLK episode.",
            },
            {
                "episode_candidate_id": "E999",
                "vehicle_candidate_id": "V002",
                "title": "Geely collision repair",
                "date_start": "2026-08-17",
                "date_end": "2026-08-30",
                "episode_state": "mixed",
                "summary": "Repair/payment discussion for the other vehicle.",
                "reported_concerns": [],
                "observations": [],
                "recommended_interventions": ["Collision repair"],
                "authorized_interventions": [],
                "completed_interventions": [],
                "outcomes": [],
                "source_refs": ["CHAT m000500"],
                "source_excerpt": "[CHAT m000500] Geely repair discussion.",
                "confidence": 0.92,
                "separation_reason": "Different vehicle identity.",
            },
        ],
        "canonical_comparisons": [
            {
                "episode_candidate_id": "E001",
                "comparison": "missing_from_durable_history",
                "matched_car_id": car.id,
                "matched_historical_episode_ids": [],
                "matched_treatment_action_ids": [],
                "already_represented_facts": [],
                "missing_facts": ["Damaged belt replacement"],
                "conflicts": [],
                "reason": "No equivalent durable action exists.",
                "advisor_confirmation_required": True,
            },
            {
                "episode_candidate_id": "E002",
                "comparison": "uncertain",
                "matched_car_id": car.id,
                "matched_historical_episode_ids": [],
                "matched_treatment_action_ids": [],
                "already_represented_facts": [],
                "missing_facts": [],
                "conflicts": [],
                "reason": "Completion is not established.",
                "advisor_confirmation_required": True,
            },
            {
                "episode_candidate_id": "E999",
                "comparison": "belongs_to_other_vehicle",
                "matched_car_id": None,
                "matched_historical_episode_ids": [],
                "matched_treatment_action_ids": [],
                "already_represented_facts": [],
                "missing_facts": [],
                "conflicts": [],
                "reason": "This episode belongs to the Geely, not the selected GLK.",
                "advisor_confirmation_required": True,
            },
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
    response = client.post(
        "/chat/historical-review/from-whatsapp-source",
        data={
            "csrf_token": _csrf_token(client),
            "car_id": car.id,
            "evidence_id": evidence.id,
        },
    )
    assert response.status_code == 302
    assert f"/chat/workspace?car_id={car.id}" in response.headers["Location"]

    prompt = (
        ChatMessage.query.filter_by(
            user_id=admin.id,
            car_id=car.id,
            role="assistant",
        )
        .order_by(ChatMessage.id.desc())
        .first()
    )
    assert prompt is not None
    assert "Damaged belt replacement" in prompt.message
    assert "Suspension work" in prompt.message
    assert "Geely collision repair" not in prompt.message
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0

    chosen = _post_json(
        client,
        "/chat",
        {"car_id": car.id, "message": "2"},
    )
    assert chosen.status_code == 200
    assert chosen.json["intent"] == "historical_review"
    assert "Suspension work" in chosen.json["reply"]
    assert chosen.json["historical_review"]["phase"] == "reviewing"
    assert TreatmentPlan.query.filter_by(
        record_origin="historical_reconciliation"
    ).count() == 0
