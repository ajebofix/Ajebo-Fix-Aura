"""Regression tests for advisor/Rina parity on historical context corrections.

All actors, notes, vehicles and evidence are synthetic. Production history is
never rewritten by these draft-only operations.
"""

from test_rina_chat_cutover import (
    _car,
    _csrf_token,
    _own,
    _post_json,
    _sign_in,
    _user,
)
from test_rina_conversational_history import _historical_reconciliation

import pytest

from extensions import db
from historical_ingestion.reconciliation import (
    reconciliation_payload,
    save_reconciliation_review,
    apply_reconciliation,
)
from models import TreatmentPlan
from services.historical_context_editing import (
    HistoricalContextEditError,
    context_editor_snapshot,
    revise_context_note,
)
from services.rina_historical_review import review_preview, summarize_state


def _setup_notes(*, suffix: int):
    admin = _user(suffix=suffix, role="admin")
    owner = _user(suffix=suffix + 1)
    car = _car(suffix=suffix, model="C 300")
    _own(owner=owner, car=car, suffix=suffix)
    _episode, extraction = _historical_reconciliation(admin=admin, car=car)
    payload = reconciliation_payload(extraction)
    payload["advisor_context_notes"] = [
        {
            "category": "reported_concern",
            "occurred_at": "2026-08-09",
            "note": "A vibration was reported before the vehicle was returned.",
        },
        {
            "category": "clarification",
            "occurred_at": "2026-08-09",
            "note": "No vibration was reported before the vehicle was returned.",
        },
        {
            "category": "other",
            "occurred_at": None,
            "note": "s and must not be marked resolved.",
        },
    ]
    save_reconciliation_review(
        extraction_id=extraction.id,
        actor_user_id=admin.id,
        reviewed_payload=payload,
    )
    db.session.commit()
    return admin, owner, car, extraction


def test_manual_advisor_correction_keeps_source_and_history_unchanged(app, client):
    admin, _owner, car, extraction = _setup_notes(suffix=551)
    original = reconciliation_payload(extraction)
    _sign_in(client, admin)
    path = f"/admin/cars/{car.id}/historical-reviews/{extraction.id}/context"

    response = client.get(path)
    assert response.status_code == 200
    assert b"Context note 3" in response.data
    assert b"Save draft correction" in response.data

    snapshot = context_editor_snapshot(
        extraction_id=extraction.id, car_id=car.id, actor_user_id=admin.id
    )
    note = snapshot["notes"][2]
    result = client.post(
        path,
        data={
            "context_note_id": note["context_note_id"],
            "revision": snapshot["revision"],
            "operation": "supersede",
            "reason": "Malformed fragment from earlier interpretation.",
            "category": note["category"],
            "occurred_at": "",
            "note": note["note"],
            "csrf_token": _csrf_token(client),
        },
    )
    assert result.status_code == 302
    updated = reconciliation_payload(extraction, reviewed=True)
    assert updated["advisor_context_notes"][2]["context_status"] == "superseded"
    assert updated["advisor_context_notes"][2]["note"] == original["advisor_context_notes"][2]["note"]
    assert updated["context_correction_audit"][-1]["actor_user_id"] == admin.id
    assert updated["context_correction_audit"][-1]["before"]["note"] == note["note"]
    assert reconciliation_payload(extraction) == original
    assert TreatmentPlan.query.count() == 0
    preview = review_preview(summarize_state(
        extraction_id=extraction.id, car_id=car.id, actor_user_id=admin.id,
    ))
    assert "s and must not be marked resolved" not in preview
    assert "vibration was reported before" in preview


def test_advisor_can_correct_and_supersede_conflicting_context(app):
    admin, _owner, car, extraction = _setup_notes(suffix=553)
    snapshot = context_editor_snapshot(
        extraction_id=extraction.id, car_id=car.id, actor_user_id=admin.id
    )
    revise_context_note(
        extraction_id=extraction.id, car_id=car.id, actor_user_id=admin.id,
        position=1, operation="correct",
        reason="Verified subsequent statement clarifies chronology.",
        note="A vibration was noticed at handover and reported again later.",
        category="reported_concern", occurred_at="2026-08-09",
        revision=snapshot["revision"],
    )
    db.session.commit()

    revised = context_editor_snapshot(
        extraction_id=extraction.id, car_id=car.id, actor_user_id=admin.id
    )
    with pytest.raises(HistoricalContextEditError, match="draft changed"):
        revise_context_note(
            extraction_id=extraction.id, car_id=car.id, actor_user_id=admin.id,
            note_id=revised["notes"][1]["context_note_id"],
            operation="supersede",
            reason="Contradicted by the verified chronology.",
            revision=snapshot["revision"],
        )
    db.session.rollback()

    revise_context_note(
        extraction_id=extraction.id, car_id=car.id, actor_user_id=admin.id,
        position=2, operation="supersede",
        reason="Contradicted by the verified chronology.",
        revision=revised["revision"],
    )
    db.session.commit()
    preview = review_preview(summarize_state(
        extraction_id=extraction.id, car_id=car.id, actor_user_id=admin.id,
    ))
    assert "noticed at handover" in preview
    assert "No vibration was reported" not in preview
    assert "Correct or supersede context notes" in preview
    assert len(reconciliation_payload(extraction, reviewed=True)["context_correction_audit"]) == 2


def test_non_advisor_cannot_open_or_edit_even_with_known_ids(app, client):
    admin, owner, car, extraction = _setup_notes(suffix=555)
    snapshot = context_editor_snapshot(
        extraction_id=extraction.id, car_id=car.id, actor_user_id=admin.id
    )
    _sign_in(client, owner)
    path = f"/admin/cars/{car.id}/historical-reviews/{extraction.id}/context"
    assert client.get(path).status_code in {302, 403}
    assert client.post(path, data={
        "context_note_id": snapshot["notes"][0]["context_note_id"],
        "revision": snapshot["revision"],
        "operation": "supersede",
        "reason": "Unauthorised change attempt.",
        "csrf_token": _csrf_token(client),
    }).status_code in {302, 403}
    with pytest.raises(HistoricalContextEditError):
        revise_context_note(
            extraction_id=extraction.id, car_id=car.id,
            actor_user_id=owner.id, position=1, operation="supersede",
            reason="Attempted override",
        )


def test_rina_explicit_numbered_command_edits_only_pending_draft(app, client):
    admin, _owner, car, extraction = _setup_notes(suffix=557)
    _sign_in(client, admin)
    _post_json(client, "/chat/select-vehicle", {"car_id": car.id})
    started = _post_json(client, "/chat", {
        "car_id": car.id, "message": "Review previous jobs with me."
    })
    assert started.status_code == 200
    reply = _post_json(client, "/chat", {
        "car_id": car.id,
        "message": "Supersede historical context #3: Malformed duplicate fragment.",
    })
    assert reply.status_code == 200
    assert "No permanent vehicle history changed" in reply.json["reply"]
    assert "s and must not be marked resolved" not in reply.json["reply"]
    reviewed = reconciliation_payload(extraction, reviewed=True)
    assert reviewed["advisor_context_notes"][2]["context_status"] == "superseded"
    assert reviewed["context_correction_audit"][-1]["source"] == "rina_explicit_command"
    assert TreatmentPlan.query.count() == 0

    correction = _post_json(client, "/chat", {
        "car_id": car.id,
        "message": (
            "Correct historical context #1: A vibration was observed "
            "upon return to the workshop."
        ),
    })
    assert correction.status_code == 200
    reviewed = reconciliation_payload(extraction, reviewed=True)
    assert reviewed["advisor_context_notes"][0]["note"].startswith("A vibration was observed")


def test_superseded_notes_do_not_enter_permanent_plan_instructions(app):
    admin, _owner, car, extraction = _setup_notes(suffix=559)
    draft = reconciliation_payload(extraction, reviewed=True)
    row = draft["candidates"][0]
    row.update({
        "advisor_decision": "confirmed",
        "reviewed_by_advisor": True,
        "occurred_at": "2026-08-09",
        "component_condition": "not_applicable",
    })
    save_reconciliation_review(
        extraction_id=extraction.id,
        actor_user_id=admin.id,
        reviewed_payload=draft,
    )
    db.session.commit()
    revise_context_note(
        extraction_id=extraction.id, car_id=car.id, actor_user_id=admin.id,
        position=3, operation="supersede",
        reason="Fragment from duplicate advisor prose.",
    )
    db.session.commit()

    plan = apply_reconciliation(
        extraction_id=extraction.id, actor_user_id=admin.id,
    )
    db.session.commit()
    assert plan is not None
    assert "s and must not be marked resolved" not in plan.internal_instructions
    assert "vibration was reported" in plan.internal_instructions
    with pytest.raises(HistoricalContextEditError, match="already applied"):
        revise_context_note(
            extraction_id=extraction.id, car_id=car.id, actor_user_id=admin.id,
            position=1, operation="supersede", reason="Too late to rewrite.",
        )
