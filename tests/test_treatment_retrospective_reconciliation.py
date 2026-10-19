"""Retrospective reconciliation never backfills fictitious consent or payment."""

import secrets

import pytest

from extensions import db
from models import CarFault, TreatmentPlan
from services.treatment_action_lifecycle import TreatmentActionLifecycleService
from services.treatment_retrospective_reconciliation import (
    RetrospectiveReconciliationError,
    record_reconciliation,
    reconciliation_overview,
)
from test_rina_chat_cutover import _car, _csrf_token, _own, _sign_in, _user
from treatment.reconciliation_models import TreatmentReconciliation


def _setup():
    advisor = _user(suffix=993, role="admin")
    owner = _user(suffix=994)
    outsider = _user(suffix=995)
    car = _car(suffix=993, model="GLK 350")
    other_car = _car(suffix=994, model="GL 450")
    _own(owner=owner, car=car, suffix=993)
    _own(owner=outsider, car=other_car, suffix=994)
    plan = TreatmentPlan(
        car_id=car.id, advisor_id=advisor.id,
        title="Collision Repair & Body Restoration — JOB-2026-003",
        record_origin="live", status="proposed",
    )
    db.session.add(plan)
    db.session.flush()
    first = TreatmentActionLifecycleService.create(
        plan_id=plan.id, actor_user_id=advisor.id,
        creation_key="historical-one", title="Panel alignment",
    )
    second = TreatmentActionLifecycleService.create(
        plan_id=plan.id, actor_user_id=advisor.id,
        creation_key="historical-two", title="Cooling system inspection",
    )
    db.session.commit()
    return advisor, owner, car, plan, first, second


def test_save_firsthand_completion_without_advancing_digital_state(app, client):
    advisor, owner, car, plan, first, second = _setup()
    _sign_in(client, advisor)
    path = f"/admin/treatment-plans/{plan.id}/actions"
    page = client.get(path)
    assert page.status_code == 200
    assert b"Retrospective Treatment Reconciliation" in page.data
    assert b"Reconcile vehicle handover" in page.data
    assert b"Original digital plan state: Proposed" in page.data
    result = client.post(
        f"/admin/treatment-plans/{plan.id}/retrospective-reconciliation",
        data={
            "csrf_token": _csrf_token(client),
            "kind": "action", "action_id": str(first.id),
            "finding": "performed", "provenance": "advisor_firsthand",
            "occurred_on": "", "evidence_id": "",
            "description": "I personally inspected the completed panel alignment; the date was not retained.",
            "operation_key": secrets.token_urlsafe(18),
        },
    )
    assert result.status_code == 302
    rows = TreatmentReconciliation.query.filter_by(treatment_plan_id=plan.id).all()
    assert len(rows) == 1
    assert rows[0].finding == "performed"
    assert rows[0].occurred_on is None
    assert db.session.get(TreatmentPlan, plan.id).status == "proposed"
    assert first.status == "planned"
    assert second.status == "planned"
    assert CarFault.query.filter_by(car_id=car.id).count() == 0
    rendered = client.get(path).text
    assert "Retrospective review:" in rendered
    assert "actual date not established" in rendered


def test_handover_is_separate_append_only_attestation_and_correction(app):
    advisor, _owner, car, plan, first, second = _setup()
    base = dict(
        plan_id=plan.id, actor_user_id=advisor.id,
        kind="handover", finding="handed_over",
        provenance="advisor_firsthand",
        recipient="Ryan", location="Lagos",
        occurred_on="", evidence_id="",
        description="I handed the completed collision repair vehicle to Ryan; exact transfer date awaits review.",
        operation_key=secrets.token_urlsafe(18),
    )
    first_record, created = record_reconciliation(**base)
    assert created
    db.session.commit()
    again, created = record_reconciliation(**base)
    assert not created and again.id == first_record.id
    with pytest.raises(RetrospectiveReconciliationError, match="different information"):
        record_reconciliation(**{**base, "description": "Different statement about handover."})
    latest, handover, history = reconciliation_overview(plan.id)
    assert latest == {}
    assert handover.id == first_record.id
    assert len(history) == 1
    with pytest.raises(ValueError, match="immutable"):
        first_record.description = "edited record"
        db.session.commit()
    db.session.rollback()
    correction, created = record_reconciliation(
        **{**base, "operation_key": secrets.token_urlsafe(18),
           "description": "I handed the vehicle to Ryan; exact handover date is still not documented."}
    )
    db.session.commit()
    assert correction.id != first_record.id
    assert TreatmentReconciliation.query.filter_by(kind="handover").count() == 2


def test_reject_wrong_vehicle_action_and_unverified_client_completion(app):
    advisor, _owner, car, plan, first, _second = _setup()
    another_plan = TreatmentPlan(
        car_id=car.id, advisor_id=advisor.id,
        title="Another plan", status="proposed", record_origin="live",
    )
    db.session.add(another_plan)
    db.session.flush()
    other = TreatmentActionLifecycleService.create(
        plan_id=another_plan.id, actor_user_id=advisor.id,
        title="Unrelated work", creation_key="unrelated-work",
    )
    db.session.commit()
    common = dict(
        plan_id=plan.id, actor_user_id=advisor.id,
        kind="action", finding="performed",
        provenance="advisor_firsthand",
        description="I directly witnessed this intervention before delivery.",
        operation_key=secrets.token_urlsafe(18),
    )
    with pytest.raises(RetrospectiveReconciliationError, match="does not belong"):
        record_reconciliation(**common, action_id=other.id)
    with pytest.raises(RetrospectiveReconciliationError, match="Client statements"):
        record_reconciliation(**{**common, "provenance": "client_report"}, action_id=first.id)
    with pytest.raises(RetrospectiveReconciliationError, match="future"):
        record_reconciliation(**common, action_id=first.id, occurred_on="2099-01-01")
    with pytest.raises(RetrospectiveReconciliationError, match="requires an accepted evidence"):
        record_reconciliation(**{**common, "provenance": "reviewed_evidence"}, action_id=first.id)
    assert TreatmentReconciliation.query.count() == 0


def test_owner_cannot_submit_advisor_reconciliation(app, client):
    _advisor, owner, car, plan, first, _second = _setup()
    _sign_in(client, owner)
    r = client.post(
        f"/admin/treatment-plans/{plan.id}/retrospective-reconciliation",
        data={
            "csrf_token": _csrf_token(client),
            "operation_key": secrets.token_urlsafe(18),
            "kind": "action", "action_id": first.id,
            "finding": "performed", "provenance": "advisor_firsthand",
            "description": "The repair was already completed before this plan existed.",
        },
    )
    assert r.status_code == 403
    assert TreatmentReconciliation.query.count() == 0


def test_all_completed_attestations_do_not_claim_outcomes_or_payment(app):
    advisor, _owner, car, plan, first, second = _setup()
    for action, finding in ((first, "performed"), (second, "not_performed")):
        record_reconciliation(
            plan_id=plan.id, actor_user_id=advisor.id,
            kind="action", action_id=action.id,
            finding=finding, provenance="advisor_firsthand",
            description="I personally verified the historical job action against my service notes.",
            operation_key=secrets.token_urlsafe(18),
        )
    db.session.commit()
    latest, handover, history = reconciliation_overview(plan.id)
    assert len(latest) == 2 and len(history) == 2
    assert handover is None
    assert plan.status == "proposed"
    assert first.status == second.status == "planned"
    assert len(plan.outcomes) == 0


def test_owner_sees_historical_summary_and_cannot_reauthorise_old_work(app, client):
    advisor, owner, car, plan, first, second = _setup()
    record_reconciliation(
        plan_id=plan.id, actor_user_id=advisor.id,
        kind="action", action_id=first.id, finding="performed",
        provenance="advisor_firsthand",
        description="I personally verified the panel alignment was completed earlier.",
        operation_key=secrets.token_urlsafe(18),
    )
    db.session.commit()
    _sign_in(client, owner)
    page = client.get("/cars/treatment-plans")
    assert page.status_code == 200
    assert b"Advisor" in page.data
    assert b"Historically performed" in page.data
    assert b"Do not authorize it as a new future repair" in page.data
    assert b"Authorize Treatment Plan" not in page.data

    denied = client.post(
        f"/cars/treatment-plans/{plan.id}/authorize",
        data={"csrf_token": _csrf_token(client)},
    )
    assert denied.status_code == 302
    assert db.session.get(TreatmentPlan, plan.id).status == "proposed"
