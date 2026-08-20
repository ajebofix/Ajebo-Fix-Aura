from __future__ import annotations

from datetime import datetime

from extensions import db
from models import (
    BookingIntent,
    Car,
    CarOwnership,
    Consultation,
    User,
    VehicleAssessment,
    VehicleEvent,
)


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Consultation Cutover {role} {suffix}",
        email=f"consultation-cutover-{role}-{suffix}@example.com",
        phone_number=f"+234896100{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 20, 8, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _owned_car(owner: User, *, suffix: int) -> tuple[Car, CarOwnership]:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin=f"W1NCUTOVER{suffix:08d}",
        current_mileage=42000,
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"CU-{suffix:03d}-LA",
        mileage_at_transfer=42000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return car, ownership


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


def _finalized_assessment(
    *,
    consultation: Consultation,
    advisor: User,
) -> VehicleAssessment:
    assessment = VehicleAssessment(
        consultation_id=consultation.id,
        car_id=consultation.car_id,
        advisor_id=advisor.id,
        finalized_by=advisor.id,
        status="finalized",
        is_finalized=True,
        finalized_at=datetime(2026, 8, 20, 10, 30, 0),
        vin=consultation.car.vin,
        mileage_at_assessment=consultation.car.current_mileage or 0,
    )
    db.session.add(assessment)
    db.session.commit()
    return assessment


def test_cutover_replaces_legacy_consultation_view_functions(app):
    expected_module = "services.consultation_route_cutover"
    endpoints = (
        "cars.book_consultation",
        "cars.request_priority_scheduling",
        "admin.admin_schedule_consultation",
        "admin.admin_start_consultation",
        "admin.admin_complete_consultation",
        "admin.admin_consultations",
    )

    for endpoint in endpoints:
        assert app.view_functions[endpoint].__module__ == expected_module

    assert "admin.admin_schedule_requested_consultation" in app.view_functions


def test_owner_booking_creates_requested_consultation_event_and_completes_intent(
    app,
    monkeypatch,
):
    owner_client = app.test_client()

    with app.app_context():
        owner = _user(suffix=1)
        car, _ownership = _owned_car(owner, suffix=1)
        owner_email = owner.email
        owner_id = owner.id
        car_id = car.id

    monkeypatch.setattr(
        "services.consultation_route_cutover.send_booking_confirmation",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "services.consultation_route_cutover.notify_admin_new_booking",
        lambda **_kwargs: None,
    )

    _login(owner_client, owner_email)
    get_response = owner_client.get(f"/cars/{car_id}/consultations/book")
    assert get_response.status_code == 200

    response = owner_client.post(
        f"/cars/{car_id}/consultations/book",
        data={
            "preferred_time": "2026-08-21T14:30",
            "description": "Private review requested.",
            "csrf_token": _csrf(owner_client),
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        consultation = Consultation.query.filter_by(
            car_id=car_id,
            client_id=owner_id,
        ).one()
        assert consultation.status == "requested"
        assert consultation.advisor_id is None
        assert consultation.scheduled_for == datetime(2026, 8, 21, 14, 30)

        event = VehicleEvent.query.filter_by(
            car_id=car_id,
            event_type="consultation.requested",
            subject_type="consultation",
            subject_id=consultation.id,
        ).one()
        assert event.new_state == "requested"
        assert event.previous_state is None
        assert event.actor_authority == "owner"
        assert event.visibility == "client"

        intent = BookingIntent.query.filter_by(
            car_id=car_id,
            user_id=owner_id,
        ).one()
        assert intent.completed is True


def test_notification_failure_does_not_rollback_durable_owner_request(app, monkeypatch):
    owner_client = app.test_client()

    with app.app_context():
        owner = _user(suffix=2)
        car, _ownership = _owned_car(owner, suffix=2)
        owner_email = owner.email
        car_id = car.id

    def _notification_failure(**_kwargs):
        raise RuntimeError("simulated provider outage")

    monkeypatch.setattr(
        "services.consultation_route_cutover.send_booking_confirmation",
        _notification_failure,
    )

    _login(owner_client, owner_email)
    owner_client.get(f"/cars/{car_id}/consultations/book")
    response = owner_client.post(
        f"/cars/{car_id}/consultations/book",
        data={
            "preferred_time": "2026-08-22T09:00",
            "description": "",
            "csrf_token": _csrf(owner_client),
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        consultation = Consultation.query.filter_by(car_id=car_id).one()
        assert consultation.status == "requested"
        assert VehicleEvent.query.filter_by(
            subject_type="consultation",
            subject_id=consultation.id,
            event_type="consultation.requested",
        ).count() == 1


def test_advisor_queue_confirms_starts_and_completes_requested_consultation(app):
    owner_client = app.test_client()
    advisor_client = app.test_client()

    with app.app_context():
        owner = _user(suffix=3)
        advisor = _user(suffix=4, role="admin")
        car, _ownership = _owned_car(owner, suffix=3)
        owner_email = owner.email
        advisor_email = advisor.email
        advisor_id = advisor.id
        car_id = car.id

    _login(owner_client, owner_email)
    owner_client.get(f"/cars/{car_id}/consultations/book")
    owner_client.post(
        f"/cars/{car_id}/consultations/book",
        data={
            "preferred_time": "2026-08-23T15:00",
            "description": "Owner preference.",
            "csrf_token": _csrf(owner_client),
        },
        follow_redirects=False,
    )

    with app.app_context():
        consultation_id = Consultation.query.filter_by(car_id=car_id).one().id

    # tests/conftest.py intentionally keeps an outer app context alive for each
    # test. Flask-Login caches current_user on that context's ``g`` object, so
    # two browser clients can otherwise inherit the first actor in the harness
    # even though their cookies are isolated. Sign the owner out before the
    # advisor login so this route test reflects real request isolation.
    owner_logout = owner_client.post(
        "/auth/logout",
        data={"csrf_token": _csrf(owner_client)},
        follow_redirects=False,
    )
    assert owner_logout.status_code in {302, 303}

    _login(advisor_client, advisor_email)

    queue = advisor_client.get("/admin/consultations")
    assert queue.status_code == 200
    queue_html = queue.get_data(as_text=True)
    assert "Owner requests awaiting advisor confirmation" in queue_html
    assert "Preferred time" in queue_html
    assert "Confirm Schedule" in queue_html
    assert f"/admin/consultations/{consultation_id}/schedule-request" in queue_html

    schedule_page = advisor_client.get(
        f"/admin/consultations/{consultation_id}/schedule-request"
    )
    assert schedule_page.status_code == 200
    schedule_html = schedule_page.get_data(as_text=True)
    assert "Owner request awaiting confirmation" in schedule_html
    assert "not a confirmed appointment" in schedule_html

    schedule_response = advisor_client.post(
        f"/admin/consultations/{consultation_id}/schedule-request",
        data={
            "scheduled_for": "2026-08-23T16:30",
            "csrf_token": _csrf(advisor_client),
        },
        follow_redirects=False,
    )
    assert schedule_response.status_code in {302, 303}

    with app.app_context():
        consultation = db.session.get(Consultation, consultation_id)
        assert consultation.status == "scheduled"
        assert consultation.advisor_id == advisor_id
        assert consultation.scheduled_for == datetime(2026, 8, 23, 16, 30)
        assert VehicleEvent.query.filter_by(
            subject_type="consultation",
            subject_id=consultation_id,
            event_type="consultation.scheduled",
        ).count() == 1

    start_response = advisor_client.post(
        f"/admin/consultations/{consultation_id}/start",
        data={"csrf_token": _csrf(advisor_client)},
        follow_redirects=False,
    )
    assert start_response.status_code in {302, 303}

    with app.app_context():
        consultation = db.session.get(Consultation, consultation_id)
        assert consultation.status == "in_progress"
        assert consultation.started_at is not None
        assert VehicleEvent.query.filter_by(
            subject_type="consultation",
            subject_id=consultation_id,
            event_type="consultation.started",
        ).count() == 1

        advisor = db.session.get(User, advisor_id)
        _finalized_assessment(consultation=consultation, advisor=advisor)

    complete_page = advisor_client.get(
        f"/admin/consultations/{consultation_id}/complete"
    )
    assert complete_page.status_code == 200

    complete_response = advisor_client.post(
        f"/admin/consultations/{consultation_id}/complete",
        data={
            "summary": "Internal professional summary.",
            "client_visible_summary": "Consultation review completed.",
            "csrf_token": _csrf(advisor_client),
        },
        follow_redirects=False,
    )
    assert complete_response.status_code in {302, 303}

    with app.app_context():
        consultation = db.session.get(Consultation, consultation_id)
        assert consultation.status == "completed"
        assert consultation.completed_at is not None
        assert consultation.summary == "Internal professional summary."
        assert consultation.client_visible_summary == "Consultation review completed."

        completion_event = VehicleEvent.query.filter_by(
            subject_type="consultation",
            subject_id=consultation_id,
            event_type="consultation.completed",
        ).one()
        assert completion_event.new_state == "completed"
        assert completion_event.data.get("assessment_id") is not None
        assert "Internal professional summary" not in str(completion_event.data)
        assert "Internal professional summary" not in (
            completion_event.description or ""
        )


def test_direct_advisor_schedule_uses_canonical_lifecycle(app):
    advisor_client = app.test_client()

    with app.app_context():
        owner = _user(suffix=5)
        advisor = _user(suffix=6, role="admin")
        car, _ownership = _owned_car(owner, suffix=4)
        advisor_email = advisor.email
        advisor_id = advisor.id
        car_id = car.id

    _login(advisor_client, advisor_email)
    response = advisor_client.post(
        f"/admin/cars/{car_id}/consultations/schedule",
        data={
            "scheduled_for": "2026-08-24T11:15",
            "csrf_token": _csrf(advisor_client),
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        consultation = Consultation.query.filter_by(car_id=car_id).one()
        assert consultation.status == "scheduled"
        assert consultation.advisor_id == advisor_id
        assert VehicleEvent.query.filter_by(
            event_type="consultation.scheduled",
            subject_type="consultation",
            subject_id=consultation.id,
        ).count() == 1


def test_priority_scheduling_records_request_not_confirmed_schedule(app, monkeypatch):
    owner_client = app.test_client()

    with app.app_context():
        owner = _user(suffix=7)
        car, _ownership = _owned_car(owner, suffix=5)
        owner_email = owner.email
        car_id = car.id

    monkeypatch.setattr(
        "services.consultation_route_cutover.has_feature",
        lambda _ownership, _feature: True,
    )

    _login(owner_client, owner_email)
    response = owner_client.post(
        f"/cars/{car_id}/priority-request",
        data={"csrf_token": _csrf(owner_client)},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        consultation = Consultation.query.filter_by(car_id=car_id).one()
        assert consultation.status == "requested"
        assert consultation.advisor_id is None
        assert consultation.notes == "Priority scheduling request by client."
        assert VehicleEvent.query.filter_by(
            event_type="consultation.requested",
            subject_type="consultation",
            subject_id=consultation.id,
        ).count() == 1
