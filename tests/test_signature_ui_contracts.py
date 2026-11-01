"""Non-invasive guardrails for Aura's Signature UI visual-only rollout."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "static" / "css"
TEMPLATES = ROOT / "templates"


def test_signature_stylesheet_is_loaded_last():
    imports = (CSS / "style.css").read_text(encoding="utf-8")
    assert imports.rstrip().endswith('@import url("aura-signature.css");')
    assert (CSS / "aura-signature.css").exists()


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
