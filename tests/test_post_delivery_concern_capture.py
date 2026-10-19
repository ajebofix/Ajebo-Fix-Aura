"""Regression coverage for post-delivery advisor concern intake and Rina commands."""

from __future__ import annotations

from datetime import datetime

from extensions import db
from models import CarFault, TreatmentPlan, VehicleEvent
from test_rina_chat_cutover import (
    _car, _csrf_token, _fake_provider, _own, _post_json, _sign_in, _user,
)


def _setup():
    advisor = _user(suffix=971, role="admin")
    owner = _user(suffix=972, role="user")
    car = _car(suffix=971, model="GLK 350")
    _own(owner=owner, car=car, suffix=971)
    plan = TreatmentPlan(
        car_id=car.id,
        advisor_id=advisor.id,
        title="Collision Repair & Body Restoration — JOB-2026-003",
        status="proposed",
    )
    db.session.add(plan)
    db.session.commit()
    return advisor, owner, car, plan


def _events(concern_id):
    return VehicleEvent.query.filter_by(
        subject_type="reported_concern",
        subject_id=concern_id,
        event_type="concern.reported",
    ).all()


def test_advisor_can_record_post_delivery_concern_without_active_consultation(app, client):
    advisor, _owner, car, plan = _setup()
    _sign_in(client, advisor)
    page = client.get(f"/admin/cars/{car.id}/concerns/add")
    assert page.status_code == 200
    assert b"Save Reported Concern" in page.data
    assert b"18 Oct" in page.data

    recorded = client.post(
        f"/admin/cars/{car.id}/concerns/add",
        data={
            "csrf_token": _csrf_token(client),
            "category": "suspension_steering",
            "description": (
                "18 Oct 2026, 17:54: client reports electric steering malfunction "
                "warning and hard steering. 20:17: client reports exposed pump wire. "
                "Both remain unverified."
            ),
            "observed_at": "2026-10-18",
        },
    )
    assert recorded.status_code == 302
    fault = CarFault.query.filter_by(car_id=car.id).one()
    assert fault.status == "reported"
    assert fault.source == "admin"
    assert fault.observed_at == datetime(2026, 10, 18)
    assert len(_events(fault.id)) == 1
    assert db.session.get(TreatmentPlan, plan.id).status == "proposed"


def test_rina_explicit_report_command_creates_durable_idempotent_report(app, client, monkeypatch):
    advisor, _owner, car, plan = _setup()
    _sign_in(client, advisor)
    provider = _fake_provider(monkeypatch, text="No provider call expected")
    message = (
        "Record this client’s reported concern:\n\n"
        "At 17:54 on 18 October 2026, client reported 'electric steering malfunction' "
        "and hard steering. At 20:17, he reported an exposed electric pump wire."
    )
    response = _post_json(client, "/chat", {"car_id": car.id, "message": message})
    assert response.status_code == 200
    assert response.json["intent"] == "reported_concern"
    assert response.json["reported_concern"]["created"] is True
    fault = CarFault.query.filter_by(car_id=car.id).one()
    assert fault.status == "reported"
    assert fault.source == "rina"
    assert "exposed electric pump wire" in fault.description
    assert fault.category == "suspension_steering"
    assert len(_events(fault.id)) == 1
    assert db.session.get(TreatmentPlan, plan.id).status == "proposed"
    assert provider.calls == []

    retry = _post_json(client, "/chat", {"car_id": car.id, "message": message})
    assert retry.status_code == 200
    assert retry.json["reported_concern"]["id"] == fault.id
    assert retry.json["reported_concern"]["created"] is False
    assert CarFault.query.filter_by(car_id=car.id).count() == 1
    assert len(_events(fault.id)) == 1


def test_owner_cannot_use_advisor_only_report_command_or_form(app, client):
    _advisor, owner, car, _plan = _setup()
    _sign_in(client, owner)
    message = "Record reported concern: Steering assistance failed after delivery."
    attempt = _post_json(client, "/chat", {"car_id": car.id, "message": message})
    assert attempt.status_code == 403
    assert CarFault.query.filter_by(car_id=car.id).count() == 0
    form = client.post(
        f"/admin/cars/{car.id}/concerns/add",
        data={"csrf_token": _csrf_token(client), "category": "other", "description": "x"},
    )
    assert form.status_code == 403
    assert CarFault.query.filter_by(car_id=car.id).count() == 0


def test_invalid_advisor_report_does_not_create_unverified_row(app, client):
    advisor, _owner, car, _plan = _setup()
    _sign_in(client, advisor)
    result = client.post(
        f"/admin/cars/{car.id}/concerns/add",
        data={
            "csrf_token": _csrf_token(client),
            "category": "suspension_steering",
            "description": "Steering felt heavy.",
            "observed_at": "this is not a date",
        },
    )
    assert result.status_code == 302
    assert CarFault.query.filter_by(car_id=car.id).count() == 0


def test_treatment_record_shows_real_vehicle_and_no_invalid_action_schedule(app, client):
    advisor, _owner, car, plan = _setup()
    _sign_in(client, advisor)
    from services.treatment_action_lifecycle import TreatmentActionLifecycleService
    TreatmentActionLifecycleService.create(
        plan_id=plan.id, actor_user_id=advisor.id,
        creation_key="post-delivery-planned-only",
        title="Inspect steering warning",
    )
    db.session.commit()
    response = client.get(f"/admin/treatment-plans/{plan.id}/actions")
    assert response.status_code == 200
    assert b"GLK 350" in response.data
    assert b"Record Reported Concern" in response.data
    assert b"Scheduling unavailable" in response.data
    assert b"Schedule Action" not in response.data
    assert b"<details" in response.data
