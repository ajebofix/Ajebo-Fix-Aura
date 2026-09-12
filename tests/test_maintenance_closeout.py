from __future__ import annotations

from extensions import db
from maintenance.care_signal import MaintenanceCareSignalService
from maintenance.presentation import MaintenancePresentationService
from maintenance.runtime import maintenance_intelligence_enabled
from maintenance.state_engine import MaintenanceStateResult, MaintenanceVehicleEvaluation
from models import Car, CarOwnership, User, VehicleEvent, VehicleHealthAlert


def _context(*, suffix: int):
    owner = User(
        name=f"M6 Owner {suffix}",
        email=f"m6-owner-{suffix}@example.com",
        phone_number=f"+2348128{suffix:06d}",
        role="user",
        is_active=True,
    )
    owner.set_password("Password123")
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2021,
        vin=f"W1NM6{suffix:012d}",
        current_mileage=61000,
    )
    db.session.add_all([owner, car])
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"M6-{suffix:03d}-LA",
        mileage_at_transfer=50000,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return owner, car, ownership


def _overdue_evaluation(*, car_id: int):
    result = MaintenanceStateResult(
        maintenance_item_key="engine_oil",
        display_name="Engine oil service",
        state="overdue",
        rule_id=91,
        rule_version="v3",
        rule_source={
            "source_type": "oem",
            "source_name": "Reviewed schedule reference",
            "source_reference": "reference://m6/91",
            "source_version": "v3",
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
        latest_matching_service_event_id=301,
        latest_matching_service_mileage=50000,
        latest_matching_service_date="2026-01-01",
        next_due_mileage=60000,
        next_due_date=None,
        evaluated_at="2026-09-12T17:00:00",
        unknown_reasons=(),
    )
    return MaintenanceVehicleEvaluation(
        car_id=car_id,
        evaluated_at="2026-09-12T17:00:00",
        results=(result,),
    )


def test_runtime_gate_defaults_enabled_and_invalid_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("AURA_MAINTENANCE_INTELLIGENCE_ENABLED", raising=False)
    assert maintenance_intelligence_enabled() is True

    monkeypatch.setenv("AURA_MAINTENANCE_INTELLIGENCE_ENABLED", "off")
    assert maintenance_intelligence_enabled() is False

    monkeypatch.setenv("AURA_MAINTENANCE_INTELLIGENCE_ENABLED", "definitely")
    assert maintenance_intelligence_enabled() is False

    monkeypatch.setenv("AURA_MAINTENANCE_INTELLIGENCE_ENABLED", "true")
    assert maintenance_intelligence_enabled() is True


def test_runtime_disable_preserves_existing_signal_without_new_mutation(app, monkeypatch):
    with app.app_context():
        _owner, car, _ownership = _context(suffix=1)
        monkeypatch.setenv("AURA_MAINTENANCE_INTELLIGENCE_ENABLED", "true")
        monkeypatch.setattr(
            "maintenance.care_signal.MaintenanceStateEngine.evaluate_vehicle",
            lambda **_kwargs: _overdue_evaluation(car_id=car.id),
        )

        first = MaintenanceCareSignalService.sync(
            car_id=car.id,
            trigger="mileage_observed",
        )
        db.session.commit()
        signal = first["signal"]
        assert signal is not None
        assert signal.is_active is True

        events_before = VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert",
            subject_id=signal.id,
        ).count()

        monkeypatch.setenv("AURA_MAINTENANCE_INTELLIGENCE_ENABLED", "false")
        monkeypatch.setattr(
            "maintenance.care_signal.MaintenanceStateEngine.evaluate_vehicle",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("disabled runtime must not evaluate maintenance state")
            ),
        )

        disabled = MaintenanceCareSignalService.sync(
            car_id=car.id,
            trigger="event_updated",
        )
        db.session.commit()
        db.session.refresh(signal)

        assert disabled["runtime_enabled"] is False
        assert disabled["evaluation"] is None
        assert disabled["signal"] is None
        assert signal.is_active is True
        assert signal.status == "new"
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert",
            subject_id=signal.id,
        ).count() == events_before


def test_runtime_reenable_restores_typed_maintenance_behavior(app, monkeypatch):
    with app.app_context():
        _owner, car, _ownership = _context(suffix=2)
        monkeypatch.setenv("AURA_MAINTENANCE_INTELLIGENCE_ENABLED", "false")

        disabled = MaintenanceCareSignalService.sync(
            car_id=car.id,
            trigger="manual",
        )
        assert disabled["runtime_enabled"] is False
        assert VehicleHealthAlert.query.filter_by(
            car_id=car.id,
            alert_type="maintenance_monitoring",
        ).count() == 0

        monkeypatch.setenv("AURA_MAINTENANCE_INTELLIGENCE_ENABLED", "true")
        monkeypatch.setattr(
            "maintenance.care_signal.MaintenanceStateEngine.evaluate_vehicle",
            lambda **_kwargs: _overdue_evaluation(car_id=car.id),
        )

        enabled = MaintenanceCareSignalService.sync(
            car_id=car.id,
            trigger="manual",
        )
        db.session.commit()

        assert enabled["runtime_enabled"] is True
        assert enabled["signal"] is not None
        assert enabled["signal"].is_active is True
        assert enabled["overdue_results"][0].maintenance_item_key == "engine_oil"


def test_disabled_owner_and_advisor_views_do_not_evaluate_or_leak_evidence(app, monkeypatch):
    with app.app_context():
        _owner, car, _ownership = _context(suffix=3)
        monkeypatch.setenv("AURA_MAINTENANCE_INTELLIGENCE_ENABLED", "0")
        monkeypatch.setattr(
            "maintenance.presentation.MaintenanceStateEngine.evaluate_vehicle",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("disabled presentation must not evaluate maintenance state")
            ),
        )

        owner_view = MaintenancePresentationService.owner_view(car)
        advisor_view = MaintenancePresentationService.advisor_view(car)

        assert owner_view["runtime_enabled"] is False
        assert owner_view["items"] == []
        assert "maintenance_intelligence_runtime_disabled" not in repr(owner_view)
        assert "temporarily unavailable" in owner_view["summary"].lower()

        assert advisor_view["runtime_enabled"] is False
        assert advisor_view["items"] == []
        assert advisor_view["evaluation_unknown_reasons"] == [
            "maintenance_intelligence_runtime_disabled"
        ]
        assert MaintenancePresentationService.overdue_evidence(car) == ()
