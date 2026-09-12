from __future__ import annotations

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
