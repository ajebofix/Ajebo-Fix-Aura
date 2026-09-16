from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from extensions import db
from models import Car, CarOwnership, Consultation, User, VehicleEvent, VehicleHealthAlert
from services.alert_history import AlertHistoryService, alert_time
from services.alert_service import AlertService
from services.care_signal_lifecycle import CareSignalLifecycleService


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Alert Center {role} {suffix}",
        email=f"alert-center-{role}-{suffix}@example.com",
        phone_number=f"+2348972{suffix:06d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _fixture(*, suffix: int, consultation_status: str = "requested",
             alert_type: str = "low_health_status"):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 1000, role="admin")
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin=f"W1N24AC{suffix:010d}",
        current_mileage=31000,
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"ACA-{suffix:03d}-LA",
        mileage_at_transfer=30000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.flush()

    signal = CareSignalLifecycleService.raise_signal(
        car_id=car.id,
        alert_type=alert_type,
        severity="high",
        message="Durable care signal requires advisor review.",
        source_classification="deterministic_rule:test",
        actor_type="system",
        actor_user_id=None,
        occurred_at=datetime(2026, 9, 6, 8, 0, 0),
    )

    consultation = Consultation(
        car_id=car.id,
        ownership_id=ownership.id,
        advisor_id=advisor.id,
        client_id=owner.id,
        status=consultation_status,
        scheduled_for=datetime.utcnow() + timedelta(days=1),
        created_at=datetime.utcnow() - timedelta(days=6),
    )
    db.session.add(consultation)
    db.session.commit()
    return owner, advisor, car, signal


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


def test_alert_service_marks_only_durable_care_signals_actionable(app):
    with app.app_context():
        _owner, _advisor, _car, signal = _fixture(suffix=1)
        alerts = AlertService.build_alert_center()

        care_signal = next(item for item in alerts if item["id"] == signal.id)
        consultation_delay = next(
            item for item in alerts if item["type"] == "consultation_delay"
        )

        assert care_signal["record_kind"] == "care_signal"
        assert care_signal["projection_source"] is None
        assert care_signal["actionable"] is True
        assert care_signal["vehicle"].id == signal.car_id

        assert consultation_delay["id"] is None
        assert consultation_delay["record_kind"] == "projection"
        assert consultation_delay["projection_source"] == "consultation"
        assert consultation_delay["actionable"] is False


def test_current_consultation_states_drive_delay_projection(app):
    with app.app_context():
        _fixture(suffix=20, consultation_status="scheduled")
        _fixture(suffix=21, consultation_status="deferred")
        alerts = AlertService.build_alert_center()
        projected_car_ids = {
            item["vehicle"].id
            for item in alerts
            if item["type"] == "consultation_delay"
        }

        scheduled = Car.query.filter_by(vin=f"W1N24AC{20:010d}").one()
        deferred = Car.query.filter_by(vin=f"W1N24AC{21:010d}").one()
        assert scheduled.id in projected_car_ids
        assert deferred.id in projected_car_ids


def test_alert_center_renders_actions_only_for_durable_care_signals(app):
    client = app.test_client()
    with app.app_context():
        _owner, advisor, _car, signal = _fixture(suffix=2)
        advisor_email = advisor.email
        signal_id = signal.id

    _login(client, advisor_email)
    response = client.get("/admin/alerts")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert "Durable care signal" in body
    assert "Consultation remains unresolved" in body
    assert "Computed operational projection · read-only" in body
    assert "Priority Requests" in body

    acknowledge_path = f"/admin/alerts/{signal_id}/acknowledge"
    resolve_path = f"/admin/alerts/{signal_id}/resolve"
    assert acknowledge_path in body
    assert resolve_path in body

    assert "/admin/alerts/None/acknowledge" not in body
    assert "/admin/alerts/None/resolve" not in body


def _resolve(signal, *, advisor=None):
    CareSignalLifecycleService.resolve(
        signal_id=signal.id,
        actor_type="user" if advisor else "system",
        actor_user_id=advisor.id if advisor else None,
        source_classification=(
            "advisor_resolution" if advisor
            else "maintenance_state:service_classification_changed"
        ),
        occurred_at=datetime(2026, 9, 14, 23, 30, 0),
    )
    db.session.commit()


def test_resolved_queue_and_history_preserve_system_resolution_without_writes(app):
    client = app.test_client()
    with app.app_context():
        _owner, advisor, _car, signal = _fixture(
            suffix=30, alert_type="maintenance_monitoring"
        )
        _resolve(signal)
        email, signal_id = advisor.email, signal.id
        event_count = VehicleEvent.query.count()
        resolution_time = signal.resolved_at

    _login(client, email)
    active = client.get("/admin/alerts").get_data(as_text=True)
    assert f"/admin/alerts/{signal_id}/history" not in active
    assert "Consultation remains unresolved" in active

    resolved = client.get("/admin/alerts?view=resolved")
    assert resolved.status_code == 200
    body = resolved.get_data(as_text=True)
    assert f"/admin/alerts/{signal_id}/history" in body
    assert "15 Sep 2026, 12:30:00 AM WAT" in body
    assert "Consultation remains unresolved" not in body
    assert f"/admin/alerts/{signal_id}/resolve" not in body
    assert f"/admin/alerts/{signal_id}/acknowledge" not in body

    for _ in range(2):
        response = client.get(f"/admin/alerts/{signal_id}/history")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Resolved by</dt><dd>System" in body
        assert "Maintenance reevaluation found no overdue items." in body
        assert "care_signal.raised" in body and "care_signal.resolved" in body
        assert "maintenance_state:service_classification_changed" in body

    with app.app_context():
        signal = db.session.get(VehicleHealthAlert, signal_id)
        assert signal.status == "resolved" and signal.is_active is False
        assert signal.resolved_at == resolution_time
        assert VehicleEvent.query.count() == event_count


def test_history_identifies_manual_resolution_and_does_not_claim_automatic_clear(app):
    client = app.test_client()
    with app.app_context():
        _owner, advisor, _car, signal = _fixture(suffix=31)
        _resolve(signal, advisor=advisor)
        email, signal_id, name = advisor.email, signal.id, advisor.name
    _login(client, email)
    body = client.get(f"/admin/alerts/{signal_id}/history").get_data(as_text=True)
    assert f"Advisor · {name}" in body
    assert "An advisor resolved this alert." in body
    assert "Maintenance reevaluation found no overdue items." not in body


def test_recurrence_has_its_own_history_and_preserves_resolved_occurrence(app):
    with app.app_context():
        _owner, _advisor, car, old = _fixture(suffix=32)
        _resolve(old)
        new = CareSignalLifecycleService.raise_signal(
            car_id=car.id, alert_type=old.alert_type, severity=old.severity,
            message=old.message, source_classification="deterministic_rule:test",
            occurred_at=datetime(2026, 9, 15, 8, 0, 0),
        )
        db.session.commit()
        assert new.id != old.id
        active = AlertService.build_alert_center()
        assert {row["id"] for row in active if row["record_kind"] == "care_signal"} == {new.id}
        resolved, _page = AlertHistoryService.resolved_page()
        assert [row["id"] for row in resolved] == [old.id]
        old_history = AlertHistoryService.detail(old)
        new_history = AlertHistoryService.detail(new)
        assert len(old_history["entries"]) == 2
        assert len(new_history["entries"]) == 1
        assert new_history["resolution"] is None
        assert {item["id"] for item in old_history["entries"]}.isdisjoint(
            {item["id"] for item in new_history["entries"]}
        )


@pytest.mark.parametrize("role", ["user", "driver"])
def test_non_advisors_cannot_read_alert_history_or_resolved_queue(app, role):
    client = app.test_client()
    with app.app_context():
        owner, _advisor, _car, signal = _fixture(suffix=33)
        owner.role = role
        db.session.commit()
        email, signal_id = owner.email, signal.id
    for path in ("/admin/alerts?view=resolved", f"/admin/alerts/{signal_id}/history"):
        response = client.get(path)
        assert response.status_code in {302, 401}
    _login(client, email)
    for path in ("/admin/alerts?view=resolved", f"/admin/alerts/{signal_id}/history"):
        assert client.get(path).status_code == 403


def test_legacy_history_is_not_fabricated_and_missing_ids_return_404(app):
    client = app.test_client()
    with app.app_context():
        _owner, advisor, car, active = _fixture(suffix=34)
        legacy = VehicleHealthAlert(
            car_id=car.id, ownership_id=active.ownership_id,
            alert_type="maintenance_monitoring", severity="low",
            message="Preserved legacy monitoring alert.", status="resolved",
            is_active=False, created_at=datetime(2025, 1, 1),
            resolved_at=datetime(2025, 2, 1),
        )
        db.session.add(legacy)
        db.session.commit()
        email, legacy_id = advisor.email, legacy.id
    _login(client, email)
    response = client.get(f"/admin/alerts/{legacy_id}/history")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "No lifecycle events were recorded" in body
    assert "No resolution audit event is available" in body
    assert "Resolved by</dt><dd>System" not in body
    assert client.get("/admin/alerts/999999/history").status_code == 404
    assert client.get("/admin/alerts?view=invalid").status_code == 400


def test_history_excludes_events_from_another_vehicle_or_stewardship(app):
    with app.app_context():
        _owner, _advisor, _car, first = _fixture(suffix=35)
        _owner2, _advisor2, _car2, second = _fixture(suffix=36)
        other_event = VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert", subject_id=second.id
        ).one()
        # A misbound legacy subject ID must not expose the other vehicle's event.
        other_event.subject_id = first.id
        db.session.commit()
        history = AlertHistoryService.detail(first)
        assert len(history["entries"]) == 1
        assert history["entries"][0]["id"] != other_event.id
        # Same vehicle but wrong stewardship is also excluded.
        other_event.car_id = first.car_id
        db.session.commit()
        assert len(AlertHistoryService.detail(first)["entries"]) == 1


def test_resolved_pagination_does_not_recalculate_current_maintenance(app, monkeypatch):
    client = app.test_client()
    with app.app_context():
        _owner, advisor, car, active = _fixture(suffix=37)
        for index in range(26):
            db.session.add(VehicleHealthAlert(
                car_id=car.id, ownership_id=active.ownership_id,
                alert_type="maintenance_monitoring", severity="low",
                message=f"Legacy occurrence {index}", status="resolved",
                is_active=False, created_at=datetime(2025, 1, 1),
                resolved_at=datetime(2025, 2, 1) + timedelta(days=index),
            ))
        db.session.commit()
        email = advisor.email
        rows, page = AlertHistoryService.resolved_page()
        assert len(rows) == 25 and page.total == 26 and page.has_next
        assert rows[0]["title"] == "Legacy occurrence 25"
        rows, page = AlertHistoryService.resolved_page(page=2)
        assert len(rows) == 1 and rows[0]["title"] == "Legacy occurrence 0"

    def unexpected_evaluation(*_args, **_kwargs):
        raise AssertionError("Resolved history must not evaluate current maintenance")

    monkeypatch.setattr(
        "services.alert_service.MaintenancePresentationService.advisor_view",
        unexpected_evaluation,
    )
    _login(client, email)
    body = client.get("/admin/alerts?view=resolved").get_data(as_text=True)
    assert "Page 1 of 2" in body and "page=2" in body
    response = client.get("/admin/alerts?view=resolved&page=2")
    assert response.status_code == 200
    assert "Page 2 of 2" in response.get_data(as_text=True)


def test_alert_time_converts_utc_and_labels_missing_time():
    assert alert_time(datetime(2026, 9, 14, 23, 30)) == "15 Sep 2026, 12:30:00 AM WAT"
    assert alert_time(None) == "Not recorded"
