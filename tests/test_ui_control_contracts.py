"""Cross-route UI smoke contracts for Aura's owner, driver and admin pages."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
CSS = ROOT / "static" / "css"


def _all_templates():
    return sorted(TEMPLATES.rglob("*.html"))


def test_every_jinja_page_template_compiles(app):
    """A syntax error must not hide an action, particularly on mobile."""
    templates = _all_templates()
    assert len(templates) >= 75
    for path in templates:
        app.jinja_env.get_template(str(path.relative_to(TEMPLATES)))


def test_every_static_template_url_for_target_is_a_registered_route(app):
    """Catch dead linked buttons before a release, across all roles."""
    registered = set(app.view_functions)
    unused = []
    pattern = re.compile(r"""url_for\(\s*['"]([^'"]+)['"]""")
    for page in _all_templates():
        source = page.read_text(encoding="utf-8")
        for name in pattern.findall(source):
            if name not in registered:
                unused.append(f"{page.relative_to(ROOT)}: {name}")
    assert unused == [], (
        "Template references to missing endpoints:\n" + "\n".join(unused)
    )


def test_shared_button_styles_exist():
    css = (CSS / "buttons.css").read_text(encoding="utf-8")
    for selector in (
        ".btn-secondary", ".btn-navy", ".btn-ghost", ".btn-primary",
        ".btn:focus-visible", ".button-stack",
    ):
        assert selector in css


def test_mobile_layout_rules_are_scoped_to_narrow_screens():
    mobile = (CSS / "mobile.css").read_text(encoding="utf-8")
    start = mobile.index("@media (max-width: 900px)")
    assert ".app-shell" in mobile[start:]
    assert ".app-shell" not in mobile[:start]
    assert ".mobile-toggle" in mobile[:start]  # hidden on desktop
    assert "overflow: hidden" in mobile[start:]


def test_choice_controls_not_styled_as_text_fields():
    forms = (CSS / "forms.css").read_text(encoding="utf-8")
    assert 'input:not([type="checkbox"])' in forms
    assert 'input[type="checkbox"]' in forms
    assert 'input[type="radio"]' in forms


def test_key_user_actions_explain_errors_and_keep_manual_paths():
    dashboard = (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")
    assert "if (!response.ok" in dashboard
    assert "vehicle-action-error" in dashboard
    billing = (TEMPLATES / "billing/advisor_invoice_receipt_delivery.html")
    source = billing.read_text(encoding="utf-8")
    assert "Open Billing to edit or share manually" in source
    assert 'name="action" value="publish"' in source
    assert 'name="action" value="send"' in source
    assert "csrf_token" in source


def test_password_security_hints_match_backend():
    for name in ("change_password.html", "reset_password.html"):
        template = (TEMPLATES / "auth" / name).read_text(encoding="utf-8")
        assert 'minlength="8"' not in template
        assert 'minlength="10"' in template


def test_navigation_accessible_mobile_close_and_escape():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert 'aria-controls="aura-sidebar"' in base
    assert 'aria-label="Close navigation"' in base
    assert 'event.key === "Escape"' in base
    assert 'aria-current", "page"' in base
