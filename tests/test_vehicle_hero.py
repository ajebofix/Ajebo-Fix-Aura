"""Family-aware vehicle hero behavior and no-regression checks."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from services.vehicle_hero import resolve_vehicle_hero

ROOT = Path(__file__).resolve().parents[1]


def vehicle(brand="Mercedes-Benz", model="", body_style=None):
    profile = SimpleNamespace(body_style=body_style) if body_style else None
    return SimpleNamespace(brand=brand, model=model, vehicle_profile=profile)


@pytest.mark.parametrize(
    ("model", "expected"),
    (
        ("S 450", "s-class"),
        ("S450 4MATIC", "s-class"),
        ("S 500", "s-class"),
        ("S-Class", "s-class"),
        ("G63 AMG", "g-class"),
        ("G 550", "g-class"),
        ("G-Class", "g-class"),
        ("GLE 450", "gle"),
        ("GLE350", "gle"),
        ("GLK 350", "glk"),
        ("C300", "c-class"),
        ("E 350", "e-class"),
        ("GL 450", "gl-gls"),
        ("GLS 450", "gl-gls"),
        ("ML 350", "ml"),
    ),
)
def test_mercedes_model_routes_to_recognisable_family_art(model, expected):
    hero = resolve_vehicle_hero(vehicle(model=model))
    assert hero.family == expected
    assert hero.match == "family"
    assert hero.asset == f"vehicle-heroes/mercedes/{expected}.svg"
    assert (ROOT / "static" / hero.asset).is_file()


def test_other_brand_or_untrusted_model_name_does_not_claim_mercedes():
    hero = resolve_vehicle_hero(vehicle(brand="Honda", model="S 450"))
    assert hero.match == "generic"
    assert hero.asset == "vehicle-heroes/fallback/generic.svg"


@pytest.mark.parametrize(
    ("body_style", "family"),
    (("SUV", "suv"), ("Crossover SUV", "suv"), ("Sedan", "sedan"), ("Saloon", "sedan")),
)
def test_unsupported_vehicle_uses_only_recorded_category(body_style, family):
    hero = resolve_vehicle_hero(vehicle(
        brand="Lexus", model="Unknown", body_style=body_style
    ))
    assert hero.family == family
    assert hero.match == "category"


def test_unknown_or_incomplete_metadata_has_generic_fallback():
    for car in (
        vehicle(brand="BMW", model="X5"),
        vehicle(model=""),
        vehicle(model="NOT-A-REAL-MODEL"),
        vehicle(brand="Mercedes-Benz", model="A 180"),
    ):
        hero = resolve_vehicle_hero(car)
        assert hero.match == "generic"
        assert (ROOT / "static" / hero.asset).is_file()


def test_assets_are_local_original_scripts_free_and_not_identical():
    paths = sorted((ROOT / "static" / "vehicle-heroes").rglob("*.svg"))
    assert len(paths) >= 11
    contents = [path.read_text(encoding="utf-8") for path in paths]
    assert len(set(contents)) == len(contents)
    for svg in contents:
        assert 'viewBox="0 0 640 280"' in svg
        assert "<script" not in svg
        assert "<image" not in svg
        assert "foreignObject" not in svg


def test_record_page_preserves_existing_anchors_actions_and_permissions():
    html = (ROOT / "templates" / "car_detail.html").read_text(encoding="utf-8")
    assert '{% include "components/vehicle_hero.html" %}' in html
    assert 'id="aura-record-title"' in html
    for anchor in ("record-health", "record-history", "rina-chat-shell"):
        assert f'#{anchor}' in html
    for endpoint in (
        "admin.add_vehicle_dtc",
        "cars.book_consultation",
        "client_billing.advisor_billing_workspace",
        "client_billing.owner_repair_progress",
    ):
        assert endpoint in html
    assert "if not is_admin_view" in html
    assert "if is_admin_view" in html


def test_hero_respects_reduced_motion_and_does_not_block_actions():
    component = (ROOT / "templates/components/vehicle_hero.html").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "static/css/aura-vehicle-record.css").read_text(
        encoding="utf-8"
    )
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "resolve_vehicle_hero=resolve_vehicle_hero" in app
    assert 'data-hero-match="{{ hero.match }}"' in component
    assert 'alt="{{ hero.label }}"' in component
    assert "not an exact year or trim rendering" in component
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".aura-vehicle-visual-art" in css
    assert "pointer-events: none" in css


def test_owner_vehicle_record_renders_specific_asset(app, client):
    from datetime import datetime

    from extensions import db
    from models import Car, CarOwnership, User

    with app.app_context():
        user = User(
            name="Hero Test Owner",
            email="hero-test-owner@example.com",
            phone_number="08009551001",
            role="user",
            is_active=True,
            email_verified_at=datetime(2026, 9, 1, 8, 0, 0),
        )
        user.set_password("HeroTestPass123")
        db.session.add(user)
        db.session.flush()

        car = Car(
            brand="Mercedes-Benz",
            model="S 450",
            year=2021,
            vin="W1NHERO2021000001",
            current_mileage=34000,
        )
        db.session.add(car)
        db.session.flush()
        db.session.add(CarOwnership(
            user_id=user.id, car_id=car.id, is_active=True,
            plate_number="HERO-101",
        ))
        db.session.commit()
        car_id = car.id

    resp = client.get("/auth/login")
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        csrf = sess.get("_csrf_token")
    response = client.post(
        "/auth/login",
        data={
            "email": "hero-test-owner@example.com",
            "password": "HeroTestPass123",
            "csrf_token": csrf or "",
        },
    )
    assert response.status_code in (302, 303)
    page = client.get(f"/cars/{car_id}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "vehicle-heroes/mercedes/s-class.svg" in html
    assert 'data-vehicle-family="s-class"' in html
    assert "Health overview" in html
    assert "Care history" in html
    assert "A.J. Rina" in html
    assert "Vehicle Identification" in html
