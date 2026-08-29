from pathlib import Path


def _mobile_css() -> str:
    return Path("static/css/mobile.css").read_text(encoding="utf-8")


def _base_template() -> str:
    return Path("templates/base.html").read_text(encoding="utf-8")


def test_mobile_toggle_does_not_float_over_scrolled_report_content():
    css = _mobile_css()

    mobile_toggle_block = css.split(".mobile-toggle{", 2)[2].split("}", 1)[0]

    assert "position:absolute;" in mobile_toggle_block
    assert "position:fixed;" not in mobile_toggle_block


def test_sidebar_overlay_uses_same_open_state_as_toggle_script():
    css = _mobile_css()

    assert ".sidebar-overlay.open{" in css
    assert ".sidebar-overlay.show{" not in css


def test_open_sidebar_hides_menu_toggle_and_exposes_close_control():
    css = _mobile_css()
    template = _base_template()

    assert ".mobile-toggle.open{" in css
    assert "display:none;" in css.split(".mobile-toggle.open{", 1)[1].split("}", 1)[0]
    assert ".sidebar.open .sidebar-close{" in css
    assert "class=\"sidebar-close\"" in template
    assert 'aria-label="Close navigation"' in template


def test_toggle_script_keeps_button_state_in_sync_with_sidebar():
    template = _base_template()

    assert 'mobileToggle.classList.toggle("open", isOpen);' in template
    assert 'mobileToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");' in template
