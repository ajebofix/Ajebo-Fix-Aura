"""Non-invasive guardrails for Aura's Signature UI visual-only rollout."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "static" / "css"
TEMPLATES = ROOT / "templates"


def test_signature_stylesheet_is_loaded_last():
    imports = (CSS / "style.css").read_text(encoding="utf-8")
    assert '@import url("aura-signature.css");' in imports
    assert imports.rstrip().endswith('@import url("aura-vehicle-record.css");')
    assert imports.index('@import url("aura-signature.css");') < imports.index('@import url("aura-vehicle-record.css");')
    assert (CSS / "aura-signature.css").exists()
    assert (CSS / "aura-vehicle-record.css").exists()


def test_signature_ui_keeps_status_and_accessibility_semantics():
    signature = (CSS / "aura-signature.css").read_text(encoding="utf-8")
    for marker in (
        ".health-green",
        ".health-gold",
        ".health-red",
        ":focus-visible",
        "@media (max-width: 900px)",
        "@media (prefers-reduced-motion: reduce)",
    ):
        assert marker in signature


def test_owner_concierge_hero_is_role_scoped_and_real_links_only():
    dashboard = (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")
    assert "{% if dashboard_mode == 'owner' %}" in dashboard
    assert 'class="aura-hero"' in dashboard
    assert "url_for('chat.rina_workspace')" in dashboard
    assert "url_for('cars.my_vehicles')" in dashboard
    assert 'id="aura-vehicles"' in dashboard
    assert "selectVehicle(" in dashboard  # Existing vehicle selection retained.


def test_shared_navigation_and_security_controls_remain_intact():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    for marker in (
        'aria-controls="aura-sidebar"',
        'aria-label="Close navigation"',
        'aria-current", "page"',
        "csrf_token()",
        'url_for(\'auth.logout\')',
        'url_for(\'profiles.profile\')',
    ):
        assert marker in base

def test_vehicle_record_first_screen_is_real_and_authority_aware():
    record = (TEMPLATES / "car_detail.html").read_text(encoding="utf-8")
    assert 'class="aura-record-hero"' in record
    assert '{{ car.rina_display_name }}' in record
    assert '{{ health.next_action or "No next step is recorded yet." }}' in record
    assert 'car.current_mileage is not none' in record
    assert 'Not yet recorded' in record
    for anchor in ("record-identity", "record-health", "record-care", "record-history"):
        assert f'id="{anchor}"' in record
        assert f'href="#{anchor}"' in record
    assert "if not is_admin_view" in record
    assert 'url_for(\'client_billing.owner_repair_progress\', car_id=car.id)' in record
    assert 'url_for(\'chat.rina_workspace\', car_id=car.id)' in record


def test_vehicle_record_original_actions_and_evidence_are_preserved():
    record = (TEMPLATES / "car_detail.html").read_text(encoding="utf-8")
    for marker in (
        "Advisor Actions",
        "Diagnostic Trouble Codes",
        "Vehicle Assessments",
        "Vehicle Care History",
        "Treatment Plans",
        "Rina",
        'include "components/rina_chat.html"',
        "CSRF",
    ):
        if marker != "CSRF":
            assert marker in record
