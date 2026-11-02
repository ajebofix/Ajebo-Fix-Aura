"""Release blockers for Aura Signature UI: protect critical existing controls.

These are contract checks, not a substitute for real-browser interaction testing.
The manifest intentionally covers controls across owner, driver, advisor and
administrator interfaces; it does not make any gated action available to a
role that should not have it.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
CSS = ROOT / "static" / "css"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


# Important pre-redesign features are a baseline: removing or replacing one
# should require a deliberate product/permission review, not a CSS-only PR.
PRESERVED_CONTROLS = {
    "templates/base.html": (
        "/dashboard",
        "url_for('chat.rina_workspace')",
        "url_for('cars.my_vehicles')",
        "url_for('cars.owner_treatment_plans')",
        "url_for('client_billing.owner_financial_records_index')",
        "url_for('admin.admin_clients')",
        "url_for('client_billing.advisor_billing_index')",
        "url_for('owner_onboarding.create_client')",
        "url_for('admin.treatment_action_console')",
        "url_for('gulf_pilot.dashboard')",
        "url_for('profiles.profile')",
        "url_for('auth.logout')",
        'name="csrf_token"',
        'aria-controls="aura-sidebar"',
        'aria-label="Close navigation"',
    ),
    "templates/dashboard.html": (
        'id="aura-vehicles"',
        "Use This Vehicle",
        "Active Vehicle",
        "selectVehicle(",
        "if (!response.ok",
        "vehicle-action-error",
        "View Full Record",
        "Open Driver Vehicle View",
        "Add Another Vehicle",
        "Add Your First Vehicle",
        "Open Driver Portal",
        "url_for('client_billing.owner_vehicle_billing'",
        "url_for('chat.rina_workspace')",
        'include "components/rina_chat.html"',
    ),
    "templates/car_detail.html": (
        "Vehicle Identification",
        "Health Overview",
        "Diagnostic Trouble Codes",
        "Care Pathway",
        "Priority Scheduling",
        "Emergency Review Request",
        "Consultation Activity",
        "Vehicle Assessments",
        "Vehicle Care History",
        "Report an Issue",
        "Driver Management",
        "Treatment Plans",
        "admin.add_vehicle_dtc",
        "admin.clear_vehicle_dtc",
        "cars.book_consultation",
        "cars.add_reported_concern",
        "cars.vehicle_records",
        "cars.request_emergency_review",
        "cars.request_priority_scheduling",
        "historical_ingestion.import_document",
        "client_billing.owner_repair_progress",
        "client_billing.owner_vehicle_billing",
        "admin.repair_progress_console",
        "client_billing.advisor_billing_workspace",
        'include "components/rina_chat.html"',
    ),
    "templates/components/rina_chat.html": (
        'id="rina-vehicle-select"',
        'id="rina-vehicle-search"',
        'id="rina-vehicle-results"',
        'id="rina-historical-copilot"',
        'id="rina-clear-context"',
        'id="rina-authority-banner"',
        'id="rina-messages"',
        'id="rina-input"',
        'id="rina-send"',
        'id="rina-jump-latest"',
        "function selectVehicle(",
        "function sendToRina()",
        "/chat/account",
        "/chat/select-vehicle",
        "/chat/history",
        "/chat/historical-copilot",
        'selectedAuthority !== "owner"',
        'data-csrf-token="{{ csrf_token() }}"',
        "speechSynthesis",
    ),
}


def test_signature_redesign_preserves_critical_existing_controls():
    missing = []
    for path, controls in PRESERVED_CONTROLS.items():
        current = source(path)
        for control in controls:
            if control not in current:
                missing.append(f"{path}: {control}")
    assert not missing, (
        "A pre-existing capability/control may have been removed:\n"
        + "\n".join(missing)
    )


def test_signature_redesign_keeps_original_role_gates():
    base = source("templates/base.html")
    dashboard = source("templates/dashboard.html")
    vehicle = source("templates/car_detail.html")
    rina = source("templates/components/rina_chat.html")

    # Do not expose advisor, financial or owner controls to other roles.
    assert "current_user.is_admin" in base
    assert "current_user.role == 'driver'" in base
    assert "current_user.role == 'user'" in base
    assert "config.get('AURA_GULF_PILOT_ENABLED', false)" in base
    assert "v.authority == 'owner'" in dashboard
    assert "dashboard_mode == 'driver'" in dashboard
    assert "if not is_admin_view" in vehicle
    assert "if is_admin_view" in vehicle
    assert "billing_portal_enabled|default(false)" in vehicle
    assert "current_user.role in [\"admin\", \"advisor\"]" in rina
    assert "updateAuthorityBanner()" in rina
    assert "selectedAuthority !== \"owner\"" in rina


def test_signature_redesign_keeps_current_working_paths_and_sources():
    dashboard = source("templates/dashboard.html")
    vehicle = source("templates/car_detail.html")
    rina = source("templates/components/rina_chat.html")

    assert 'url_for(\'dashboard.select_vehicle\')' in dashboard
    assert 'car.current_mileage is not none' in vehicle
    assert '{{ health.next_action or "No next step is recorded yet." }}' in vehicle
    assert 'function renderAssistantMarkdown(container, text)' in rina
    assert 'const isProfessional = shell.dataset.professional === "true"' in rina
    assert 'fetchJson(selectedCarId ? "/chat" : "/chat/account"' in rina
    assert 'role === "user" ? "user" : "assistant"' in rina
    assert 'shell.dataset.busy = loading ? "true" : "false"' in rina


def test_signature_styles_do_not_blank_or_disable_critical_action_containers():
    entry = source("static/css/style.css")
    for stylesheet in ("aura-signature.css", "aura-vehicle-record.css", "aura-rina-presence.css"):
        assert f'@import url("{stylesheet}");' in entry
        assert (CSS / stylesheet).is_file()

    # A hidden legacy avatar is acceptable; invisible actions are not.
    presence_css = source("static/css/aura-rina-presence.css")
    assert ".rina-shell .rina-message.user" in presence_css
    assert ".rina-shell .rina-input-row" in presence_css
    assert ".rina-shell #rina-send" in presence_css
    assert "@media (max-width: 680px)" in presence_css
    assert "@media (prefers-reduced-motion: reduce)" in presence_css


def test_owner_and_advisor_context_semantics_remain_explicit():
    rina = source("templates/components/rina_chat.html")
    for authority in ("driver", "owner", "advisor", "administrator"):
        assert f'authority === "{authority}"' in rina

    assert "Owner financial and approval authority are not available" in rina
    assert "Rina uses recorded Aura data and does not diagnose mechanical faults." in rina
    assert "Physical inspection and advisor review remain necessary" in rina


def test_signed_in_owner_can_still_open_vehicle_record_and_original_actions(app, client):
    """Render the actual protected page with ephemeral test records.

    Static source contracts cannot guarantee that an authenticated Jinja view
    builds its buttons, or that the record page even responds successfully.
    """
    from extensions import db
    from test_authority_product_surfaces import _car, _own, _sign_in, _user

    with app.app_context():
        owner = _user(suffix=971)
        car = _car(suffix=971)
        _own(owner=owner, car=car, suffix=971)
        db.session.commit()
        car_id = car.id
        _sign_in(client, owner)

    response = client.get(f"/cars/{car_id}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for text in (
        "Vehicle Identification",
        "Health Overview",
        "Diagnostic Trouble Codes",
        "Care Pathway",
        "Priority Scheduling",
        "Vehicle Care History",
        "Report Concern",
        "Driver Management",
        "Treatment Plans",
        'id="rina-chat-shell"',
        'id="rina-send"',
        'id="record-health"',
        'id="record-history"',
    ):
        assert text in body
    assert f"/cars/{car_id}" in body
    assert f'data-page-car-id="{car_id}"' in body
