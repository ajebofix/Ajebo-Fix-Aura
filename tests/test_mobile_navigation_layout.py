from pathlib import Path


def _mobile_css() -> str:
    return Path("static/css/mobile.css").read_text(encoding="utf-8")


def test_mobile_toggle_does_not_float_over_scrolled_report_content():
    css = _mobile_css()

    mobile_toggle_block = css.split(".mobile-toggle{", 2)[2].split("}", 1)[0]

    assert "position:absolute;" in mobile_toggle_block
    assert "position:fixed;" not in mobile_toggle_block


def test_sidebar_overlay_uses_same_open_state_as_toggle_script():
    css = _mobile_css()

    assert ".sidebar-overlay.open{" in css
    assert ".sidebar-overlay.show{" not in css
