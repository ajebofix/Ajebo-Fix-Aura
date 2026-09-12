from __future__ import annotations

from datetime import datetime

from extensions import db
from maintenance.presentation import MaintenancePresentationService
from maintenance.state_engine import MaintenanceStateResult, MaintenanceVehicleEvaluation
from models import Car, CarOwnership, User, VehicleEvent, VehicleHealthAlert
from services.health_alert_service import CareSignalService


def _context(*, suffix: int):
    owner = User(
        name=f"M5 Owner {suffix}",
        email=f"m5-owner-{suffix}@example.com",
        phone_number=f"+2348119{suffix:06d}",
        role="user",
        is_active=True,
    )
    owner.set_password("Password123")
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2021,
        vin=f"W1NM5{suffix:012d}",
        current_mileage=61000,
    )
    db.session.add_all([owner, car])
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"M5-{suffix:03d}-LA",
        mileage_at_transfer=50000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return owner, car, ownership


def _result(*, state: str, car_id: int, item_key: str = "engine_oil"):
    reasons = ("current_odometer_stale",) if state == "unknown" else ()
    return MaintenanceStateResult(
        maintenance_item_key=item_key,
        display_name="Engine oil service",
        state=state,
        rule_id=77,
        rule_version="v1",
        rule_source={
            "source_type": "oem",
            "source_name": "Reviewed manufacturer schedule reference",
            "source_reference": "private://advisor/source/77",
            "source_version": "v1",
        },
        verification_status="advisor_verified",
        vehicle_identity_used={"car_id": car_id, "brand": "Mercedes-Benz"},
        current_odometer_km=61000,
        odometer_provenance={
            "source": "advisor_observation",
            "source_label": "Advisor observation",
            "verification_status": "advisor_verified",
            "verification_label": "Advisor verified",
            "freshness_status": "current",
            "freshness_label": "Current",
            "age_days": 1,
            "provenance_available": True,
        },
        latest_matching_service_event_id=91,
        latest_matching_service_mileage=50000,
        latest_matching_service_date="2026-01-01",
        next_due_mileage=60000,
        next_due_date=None,
        evaluated_at="2026-09-12T16:00:00",
        unknown_reasons=reasons,
    )


def _evaluation(*, car_id: int, states: tuple[str, ...]):
    return MaintenanceVehicleEvaluation(
        car_id=car_id,
        evaluated_at="2026-09-12T16:00:00",
        results=tuple(_result(state=state, car_id=car_id) for state in states),
    )


def _neutralise_other_signals(monkeypatch):
    monkeypatch.setattr(
        "services.health_alert_service.calculate_vehicle_health",
        lambda _car, _ownership: {
            "health_score": 80,
            "risk_reasons": ["Legacy prose says service overdue"],
        },
    )
    monkeypatch.setattr(
        "services.health_alert_service.HealthTrendService.analyze_car_trajectory",
        lambda _car_id: {"rapid_decline": False},
    )


def test_legacy_overdue_prose_and_unknown_state_cannot_raise_maintenance_signal(
    app, monkeypatch
):
    with app.app_context():
        _owner, car, _ownership = _context(suffix=1)
        _neutralise_other_signals(monkeypatch)
        monkeypatch.setattr(
            "maintenance.care_signal.MaintenanceStateEngine.evaluate_vehicle",
            lambda **_kwargs: _evaluation(car_id=car.id, states=("unknown",)),
        )

        CareSignalService.evaluate(car.id, trigger="event_created")

        assert VehicleHealthAlert.query.filter_by(
            car_id=car.id,
            alert_type="maintenance_monitoring",
        ).count() == 0


def test_due_and_upcoming_states_do_not_raise_overdue_monitoring(app, monkeypatch):
    with app.app_context():
        _owner, car, _ownership = _context(suffix=2)
        _neutralise_other_signals(monkeypatch)

        for state in ("upcoming", "due"):
            monkeypatch.setattr(
                "maintenance.care_signal.MaintenanceStateEngine.evaluate_vehicle",
                lambda state=state, **_kwargs: _evaluation(
                    car_id=car.id,
                    states=(state,),
                ),
            )
            CareSignalService.evaluate(car.id, trigger="event_updated")

        assert VehicleHealthAlert.query.filter_by(
            car_id=car.id,
            alert_type="maintenance_monitoring",
        ).count() == 0


def test_typed_overdue_raises_then_non_overdue_resolves_same_occurrence(app, monkeypatch):
    with app.app_context():
        _owner, car, _ownership = _context(suffix=3)
        _neutralise_other_signals(monkeypatch)
        current = {"state": "overdue"}

        monkeypatch.setattr(
            "maintenance.care_signal.MaintenanceStateEngine.evaluate_vehicle",
            lambda **_kwargs: _evaluation(
                car_id=car.id,
                states=(current["state"],),
            ),
        )

        CareSignalService.evaluate(car.id, trigger="mileage_observed")
        signal = VehicleHealthAlert.query.filter_by(
            car_id=car.id,
            alert_type="maintenance_monitoring",
        ).one()
        assert signal.is_active is True
        assert signal.status == "new"

        current["state"] = "due"
        CareSignalService.evaluate(car.id, trigger="event_updated")
        db.session.refresh(signal)
        assert signal.is_active is False
        assert signal.status == "resolved"

        events = VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert",
            subject_id=signal.id,
        ).order_by(VehicleEvent.id.asc()).all()
        assert [event.event_type for event in events] == [
            "care_signal.raised",
            "care_signal.resolved",
        ]


def test_owner_projection_is_calm_and_does_not_leak_advisor_provenance(app, monkeypatch):
    with app.app_context():
        _owner, car, _ownership = _context(suffix=4)
        monkeypatch.setattr(
            "maintenance.presentation.MaintenanceStateEngine.evaluate_vehicle",
            lambda **_kwargs: _evaluation(car_id=car.id, states=("overdue", "unknown")),
        )

        view = MaintenancePresentationService.owner_view(car)
        text = repr(view)
        assert "private://advisor/source/77" not in text
        assert "rule_id" not in text
        assert "current_odometer_stale" not in text
        assert "non-diagnostic" in view["disclaimer"].lower()
        assert "immediate repair" not in text.lower()
        assert "must repair" not in text.lower()
        assert {item["state"] for item in view["items"]} == {"overdue", "unknown"}
        assert all("guidance" in item for item in view["items"])


def test_advisor_projection_preserves_bounded_evidence(app, monkeypatch):
    with app.app_context():
        _owner, car, _ownership = _context(suffix=5)
        monkeypatch.setattr(
            "maintenance.presentation.MaintenanceStateEngine.evaluate_vehicle",
            lambda **_kwargs: _evaluation(car_id=car.id, states=("overdue",)),
        )

        view = MaintenancePresentationService.advisor_view(car)
        item = view["items"][0]
        assert item["state"] == "overdue"
        assert item["rule_id"] == 77
        assert item["rule_source"]["source_reference"] == "private://advisor/source/77"
        assert item["latest_matching_service_event_id"] == 91
        assert item["next_due_mileage"] == 60000
