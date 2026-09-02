from __future__ import annotations

from datetime import datetime

from evidence.models import EvidenceLink, VehicleEvidence
from extensions import db
from models import Car, CarOwnership, TreatmentPlan, User, VehicleEvent
from treatment.models import TreatmentAction, TreatmentOutcome


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"2.3C Route {role} {suffix}",
        email=f"wave23c-route-{role}-{suffix}@example.com",
        phone_number=f"+2348954{suffix:06d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _fixture(*, suffix: int, plan_status: str = "in_progress"):
    owner = _user(suffix=suffix)
    unrelated = _user(suffix=suffix + 100)
    driver = _user(suffix=suffix + 200, role="driver")
    advisor = _user(suffix=suffix + 300, role="admin")

    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1N23CRT{suffix:09d}",
        current_mileage=27000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"23C-{suffix:03d}-LA",
            mileage_at_transfer=26000,
            is_active=True,
        )
    )
    plan = TreatmentPlan(
        car_id=car.id,
        advisor_id=advisor.id,
        title="Wave 2.3C treatment pathway",
        client_summary="Client-safe care pathway summary.",
        internal_instructions="PLAN PRIVATE — OWNER MUST NOT SEE THIS",
        status=plan_status,
    )
    db.session.add(plan)
    db.session.commit()
    return owner, unrelated, driver, advisor, car, plan


def _evidence(*, car: Car, uploader: User, suffix: int) -> VehicleEvidence:
    evidence = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=uploader.id,
        evidence_type="image",
        purpose="treatment_evidence",
        source_channel="web",
        visibility="client",
        review_status="accepted",
        storage_provider="r2",
        storage_state="available",
        object_key=f"route/treatment/{car.id}/{suffix}.jpg",
        safe_display_name=f"treatment-evidence-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=1024,
        sha256=f"{suffix:064x}"[-64:],
        consent_basis="client_submission",
        lawful_purpose="vehicle care evidence",
        reviewed_by_user_id=uploader.id,
        reviewed_at=datetime(2026, 9, 1, 13, 0, 0),
        review_reason_code="advisor_verified",
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def _csrf(client) -> str:
    with client.session_transaction() as flask_session:
        return str(flask_session["_csrf_token"])


def _login(client, email: str) -> None:
    client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "email": email,
            "password": PASSWORD,
            "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    client.get("/")
    _csrf(client)


def _post(client, path: str, data: dict | None = None):
    payload = dict(data or {})
    payload["csrf_token"] = _csrf(client)
    return client.post(path, data=payload, follow_redirects=False)


def test_only_advisor_can_open_treatment_action_console_and_plan_detail(app):
    with app.app_context():
        owner, unrelated, driver, advisor, _car, plan = _fixture(suffix=1)
        emails = {
            "owner": owner.email,
            "unrelated": unrelated.email,
            "driver": driver.email,
            "advisor": advisor.email,
        }
        plan_id = plan.id

    for role in ("owner", "unrelated", "driver"):
        client = app.test_client()
        _login(client, emails[role])

        console = client.get("/admin/treatment-actions", follow_redirects=False)
        detail = client.get(
            f"/admin/treatment-plans/{plan_id}/actions",
            follow_redirects=False,
        )

        assert console.status_code in {302, 403}
        assert detail.status_code in {302, 403}
        assert "Treatment Action Console" not in console.get_data(as_text=True)
        assert "PLAN PRIVATE — OWNER MUST NOT SEE THIS" not in detail.get_data(as_text=True)

    advisor_client = app.test_client()
    _login(advisor_client, emails["advisor"])
    console = advisor_client.get("/admin/treatment-actions")
    detail = advisor_client.get(f"/admin/treatment-plans/{plan_id}/actions")
    assert console.status_code == 200
    assert detail.status_code == 200
    assert "Treatment Action Console" in console.get_data(as_text=True)
    assert "PLAN PRIVATE — OWNER MUST NOT SEE THIS" in detail.get_data(as_text=True)


def test_advisor_create_action_route_emits_canonical_event(app):
    client = app.test_client()
    with app.app_context():
        _owner, _unrelated, _driver, advisor, _car, plan = _fixture(suffix=2)
        advisor_email = advisor.email
        advisor_id = advisor.id
        plan_id = plan.id

    _login(client, advisor_email)
    response = _post(
        client,
        f"/admin/treatment-plans/{plan_id}/actions",
        {
            "creation_key": "route-action-create-2",
            "title": "Front braking-system intervention",
            "client_summary": "Carry out the authorized braking-system intervention.",
            "internal_instructions": "ACTION PRIVATE — OWNER MUST NOT SEE THIS",
            "visibility": "client",
        },
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        action = TreatmentAction.query.filter_by(
            treatment_plan_id=plan_id,
            creation_key="route-action-create-2",
        ).one()
        event = VehicleEvent.query.filter_by(
            subject_type="treatment_action",
            subject_id=action.id,
            event_type="treatment_action.created",
        ).one()
        assert action.status == "planned"
        assert event.actor_user_id == advisor_id
        assert event.actor_authority in {"advisor", "administrator"}
        assert event.previous_state is None
        assert event.new_state == "planned"
        assert "ACTION PRIVATE" not in str(event.data)


def test_advisor_action_schedule_start_complete_routes_do_not_complete_parent_plan(app):
    client = app.test_client()
    with app.app_context():
        _owner, _unrelated, _driver, advisor, _car, plan = _fixture(suffix=3)
        action = TreatmentAction(
            treatment_plan_id=plan.id,
            car_id=plan.car_id,
            created_by_user_id=advisor.id,
            creation_key="route-flow-3",
            title="Treatment flow route action",
            client_summary="Client-safe action summary.",
            status="planned",
            visibility="client",
        )
        db.session.add(action)
        db.session.commit()
        advisor_email = advisor.email
        plan_id = plan.id
        action_id = action.id

    _login(client, advisor_email)
    scheduled = _post(
        client,
        f"/admin/treatment-actions/{action_id}/schedule",
        {
            "scheduled_for": "2026-09-02T09:00",
            "operation_key": "route-schedule-3",
        },
    )
    assert scheduled.status_code in {302, 303}

    started = _post(
        client,
        f"/admin/treatment-actions/{action_id}/start",
        {"operation_key": "route-start-3"},
    )
    assert started.status_code in {302, 303}

    completed = _post(
        client,
        f"/admin/treatment-actions/{action_id}/complete",
        {"operation_key": "route-complete-3"},
    )
    assert completed.status_code in {302, 303}

    with app.app_context():
        persisted_action = db.session.get(TreatmentAction, action_id)
        persisted_plan = db.session.get(TreatmentPlan, plan_id)
        assert persisted_action is not None
        assert persisted_action.status == "completed"
        assert persisted_plan is not None
        assert persisted_plan.status == "in_progress"
        assert [
            event.event_type
            for event in VehicleEvent.query.filter_by(
                subject_type="treatment_action",
                subject_id=action_id,
            ).order_by(VehicleEvent.id.asc()).all()
        ] == [
            "treatment_action.scheduled",
            "treatment_action.started",
            "treatment_action.completed",
        ]


def test_owner_page_shows_only_client_safe_actions_and_outcomes(app):
    client = app.test_client()
    with app.app_context():
        owner, _unrelated, _driver, advisor, _car, plan = _fixture(suffix=4)
        client_action = TreatmentAction(
            treatment_plan_id=plan.id,
            car_id=plan.car_id,
            created_by_user_id=advisor.id,
            creation_key="client-action-4",
            title="Client-visible intervention",
            client_summary="Client-visible intervention summary.",
            internal_instructions="ACTION PRIVATE — OWNER MUST NOT SEE THIS",
            status="completed",
            visibility="client",
            completed_at=datetime(2026, 9, 2, 10, 0, 0),
        )
        advisor_action = TreatmentAction(
            treatment_plan_id=plan.id,
            car_id=plan.car_id,
            created_by_user_id=advisor.id,
            creation_key="advisor-action-4",
            title="ADVISOR ACTION — OWNER MUST NOT SEE THIS",
            internal_instructions="ADVISOR ACTION DETAIL — OWNER MUST NOT SEE THIS",
            status="planned",
            visibility="advisor",
        )
        db.session.add_all([client_action, advisor_action])
        db.session.flush()
        client_outcome = TreatmentOutcome(
            treatment_plan_id=plan.id,
            treatment_action_id=client_action.id,
            car_id=plan.car_id,
            recorded_by_user_id=advisor.id,
            recording_key="client-outcome-4",
            progression_direction="improving",
            summary="Client-visible outcome summary.",
            advisor_note="OUTCOME PRIVATE — OWNER MUST NOT SEE THIS",
            visibility="client",
            provenance_kind="professional_observation",
            provenance_data={
                "observation_source": "road_test",
                "reference": "PRIVATE REFERENCE",
            },
            observed_at=datetime(2026, 9, 2, 12, 0, 0),
        )
        advisor_outcome = TreatmentOutcome(
            treatment_plan_id=plan.id,
            car_id=plan.car_id,
            recorded_by_user_id=advisor.id,
            recording_key="advisor-outcome-4",
            progression_direction="stable",
            summary="ADVISOR OUTCOME — OWNER MUST NOT SEE THIS",
            advisor_note="ADVISOR NOTE — OWNER MUST NOT SEE THIS",
            visibility="advisor",
            provenance_kind="professional_observation",
            provenance_data={"observation_source": "advisor_inspection"},
            observed_at=datetime(2026, 9, 2, 12, 5, 0),
        )
        db.session.add_all([client_outcome, advisor_outcome])
        db.session.commit()
        owner_email = owner.email

    _login(client, owner_email)
    response = client.get("/cars/treatment-plans")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Client-visible intervention" in html
    assert "Client-visible intervention summary." in html
    assert "Client-visible outcome summary." in html
    assert "Improving" in html
    assert "PLAN PRIVATE — OWNER MUST NOT SEE THIS" not in html
    assert "ACTION PRIVATE — OWNER MUST NOT SEE THIS" not in html
    assert "ADVISOR ACTION — OWNER MUST NOT SEE THIS" not in html
    assert "OUTCOME PRIVATE — OWNER MUST NOT SEE THIS" not in html
    assert "ADVISOR OUTCOME — OWNER MUST NOT SEE THIS" not in html
    assert "PRIVATE REFERENCE" not in html


def test_advisor_outcome_route_links_accepted_evidence_and_emits_outcome_event(app):
    client = app.test_client()
    with app.app_context():
        owner, _unrelated, _driver, advisor, car, plan = _fixture(suffix=5)
        evidence = _evidence(car=car, uploader=owner, suffix=5)
        advisor_email = advisor.email
        plan_id = plan.id
        evidence_id = evidence.id

    _login(client, advisor_email)
    response = _post(
        client,
        f"/admin/treatment-plans/{plan_id}/outcomes",
        {
            "recording_key": "route-outcome-5",
            "progression_direction": "improving",
            "summary": "Evidence-backed post-treatment behavior is improving.",
            "provenance_kind": "reviewed_evidence",
            "visibility": "client",
            "observed_at": "2026-09-02T12:00",
            "evidence_ids": [str(evidence_id)],
        },
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        outcome = TreatmentOutcome.query.filter_by(
            treatment_plan_id=plan_id,
            recording_key="route-outcome-5",
        ).one()
        assert EvidenceLink.query.filter_by(
            evidence_id=evidence_id,
            subject_type="treatment_outcome",
            subject_id=outcome.id,
            relationship_type="supports",
        ).count() == 1
        event = VehicleEvent.query.filter_by(
            subject_type="treatment_plan",
            subject_id=plan_id,
            event_type="treatment.outcome_recorded",
        ).one()
        assert event.progression_direction == "improving"
        assert event.evidence_refs == [
            {"type": "vehicle_evidence", "id": evidence_id}
        ]
        assert db.session.get(TreatmentPlan, plan_id).status == "in_progress"


def test_owner_cannot_post_treatment_action_mutations(app):
    client = app.test_client()
    with app.app_context():
        owner, _unrelated, _driver, advisor, _car, plan = _fixture(suffix=6)
        action = TreatmentAction(
            treatment_plan_id=plan.id,
            car_id=plan.car_id,
            created_by_user_id=advisor.id,
            creation_key="blocked-owner-action-6",
            title="Professional action",
            status="planned",
            visibility="client",
        )
        db.session.add(action)
        db.session.commit()
        owner_email = owner.email
        plan_id = plan.id
        action_id = action.id

    _login(client, owner_email)

    create_response = _post(
        client,
        f"/admin/treatment-plans/{plan_id}/actions",
        {"creation_key": "blocked", "title": "Blocked"},
    )
    schedule_response = _post(
        client,
        f"/admin/treatment-actions/{action_id}/schedule",
        {"scheduled_for": "2026-09-02T09:00"},
    )
    outcome_response = _post(
        client,
        f"/admin/treatment-plans/{plan_id}/outcomes",
        {
            "recording_key": "blocked-outcome",
            "progression_direction": "stable",
            "summary": "Blocked",
            "provenance_kind": "professional_observation",
            "observation_source": "client_follow_up",
        },
    )

    assert create_response.status_code in {302, 403}
    assert schedule_response.status_code in {302, 403}
    assert outcome_response.status_code in {302, 403}

    with app.app_context():
        persisted = db.session.get(TreatmentAction, action_id)
        assert persisted is not None
        assert persisted.status == "planned"
        assert TreatmentAction.query.filter_by(
            treatment_plan_id=plan_id,
            creation_key="blocked",
        ).count() == 0
        assert TreatmentOutcome.query.filter_by(
            treatment_plan_id=plan_id,
            recording_key="blocked-outcome",
        ).count() == 0
        assert VehicleEvent.query.filter_by(
            subject_type="treatment_action",
            subject_id=action_id,
        ).count() == 0
