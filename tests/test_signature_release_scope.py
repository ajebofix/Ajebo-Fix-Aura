"""Release guard: ship Signature UI without the rejected cartoon artwork.

Staging synthetic access remains strictly outside the production release tree.
This guard protects the customer's accepted scope and core entrypoints.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_rejected_vehicle_artwork_is_not_in_prod_release():
    hero = read("templates/car_detail.html")
    style = read("static/css/style.css")
    assert 'components/vehicle_hero.html' not in hero
    assert "aura-vehicle-visual" not in hero
    assert "vehicle-heroes" not in style
    assert not (ROOT / "static" / "vehicle-heroes").exists()
    assert not (ROOT / "services" / "vehicle_hero.py").exists()
    assert not (ROOT / "scripts" / "seed_signature_staging.py").exists()


def test_accepted_vehicle_record_actions_and_care_data_still_present():
    html = read("templates/car_detail.html")
    for marker in (
        'id="aura-record-title"',
        'href="#record-health"',
        'href="#record-history"',
        "url_for('chat.rina_workspace', car_id=car.id)",
        'class="aura-record-summary"',
        'class="aura-record-next"',
        'id="record-identity"',
        'id="record-health"',
        'id="record-care"',
        'id="record-history"',
        "admin.add_vehicle_dtc",
        "cars.book_consultation",
        "client_billing.owner_vehicle_billing",
        "client_billing.advisor_billing_workspace",
    ):
        assert marker in html


def test_mobile_nav_and_login_controls_are_retained():
    styles = read("static/css/aura-signature.css")
    login = read("templates/login.html")
    nav = read("templates/base.html")
    assert ".sidebar { justify-content: flex-start; }" in styles
    assert ".aura-login-card .login-mini-logo" in styles
    assert "height: 112px" in styles
    assert 'id="aura-login-form"' in login
    assert 'id="aura-login-submit"' in login
    assert 'id="identifier"' in login
    assert 'id="password"' in login
    assert "auth.forgot_password" in login
    assert "auth.logout" in nav
