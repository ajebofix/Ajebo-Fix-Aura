from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from flask import render_template


def _car():
    return SimpleNamespace(
        id=1,
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin="4JGFB5KB5MA477535",
    )


def test_advisor_maintenance_template_renders_dict_items_key(app):
    maintenance = {
        "runtime_enabled": True,
        "items": [
            {
                "display_name": "Engine oil service",
                "state": "overdue",
                "maintenance_item_key": "engine_oil",
                "rule_id": 7,
                "rule_version": "v1",
                "rule_source": {
                    "source_name": "Reviewed maintenance reference",
                    "source_reference": "private://maintenance/7",
                },
                "verification_status": "advisor_verified",
                "current_odometer_km": 64100,
                "odometer_provenance": {
                    "source_label": "Advisor observation",
                    "verification_label": "Advisor verified",
                    "freshness_label": "Current",
                },
                "latest_matching_service_event_id": 91,
                "latest_matching_service_mileage": 50000,
                "latest_matching_service_date": "2026-01-01",
                "next_due_mileage": 60000,
                "next_due_date": None,
                "unknown_reasons": [],
            }
        ],
        "state_counts": {
            "upcoming": 0,
            "due": 0,
            "overdue": 1,
            "unknown": 0,
        },
        "evaluation_unknown_reasons": [],
        "evaluated_at": "2026-09-12T17:30:00",
    }

    with app.test_request_context("/admin/cars/1/maintenance"):
        html = render_template(
            "maintenance/vehicle.html",
            car=_car(),
            ownership=None,
            is_admin_view=True,
            maintenance=maintenance,
        )

    assert "Engine oil service" in html
    assert "Overdue" in html
    assert "private://maintenance/7" in html


def test_owner_maintenance_template_renders_dict_items_key(app):
    maintenance = {
        "runtime_enabled": True,
        "items": [
            {
                "display_name": "Engine oil service",
                "state": "due",
                "state_label": "Due",
                "guidance": "This item can be planned with your advisor.",
                "next_due_mileage": 65000,
                "next_due_date": None,
            }
        ],
        "state_counts": {
            "upcoming": 0,
            "due": 1,
            "overdue": 0,
            "unknown": 0,
        },
        "has_verified_knowledge": True,
        "summary": "Maintenance timing is based on verified evidence.",
        "disclaimer": "Maintenance monitoring is informational and non-diagnostic.",
    }

    with app.test_request_context("/cars/1/maintenance"):
        html = render_template(
            "maintenance/vehicle.html",
            car=_car(),
            ownership=None,
            is_admin_view=False,
            maintenance=maintenance,
        )

    assert "Engine oil service" in html
    assert "Due" in html
    assert "planned with your advisor" in html


def test_vehicle_history_hides_empty_classification_control(app):
    service = SimpleNamespace(
        id=11,
        event_type="service",
        title="Routine service",
        created_at=datetime(2026, 1, 15, 9, 0, 0),
        mileage=10000,
        data={"record_mode": "historical"},
        description="Historical service record.",
    )
    mileage_snapshot = SimpleNamespace(
        odometer_km=64100,
        freshness_label="Current",
        provenance_available=True,
        observed_at=datetime(2026, 9, 12, 12, 0, 0),
        source_label="Advisor observation",
        verification_label="Advisor verified",
    )

    with app.test_request_context("/admin/cars/1/records"):
        html = render_template(
            "reports/timeline.html",
            car=_car(),
            timeline=[],
            health=SimpleNamespace(label="Healthy", next_action="Continue monitoring"),
            services=[service],
            concerns=[],
            is_admin_view=True,
            is_pdf_export=False,
            print_mode=False,
            mileage_snapshot_for=lambda _car: mileage_snapshot,
            mileage_history_for=lambda _car_id, limit=12: [],
            mileage_source_label=lambda source: source,
            mileage_verification_label=lambda status: status,
            mileage_review_label=lambda status: status,
            maintenance_classification_options_for=lambda _car: (),
            maintenance_active_classification_for=lambda _event_id: None,
        )

    assert "No verified maintenance items are available for this vehicle yet." in html
    assert "Save Maintenance Link" not in html
    assert "Verified maintenance item" not in html
