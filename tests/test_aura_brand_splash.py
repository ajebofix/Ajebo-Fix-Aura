"""Brand regression tests for the real Aura asset and financial viewer."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_splash_reuses_existing_logo_and_has_safe_exit():
    base = (ROOT / "templates/base.html").read_text(encoding="utf-8")
    assert "aura-icon-health.png" in base
    assert (ROOT / "static/images/aura-icon-health.png").is_file()
    assert "Preparing your secure view" in base
    assert "prefers-reduced-motion" in base
    assert "window.addEventListener('load', clearLoading" in base
    assert "window.setTimeout(clearLoading, 6500)" in base
    assert "window.addEventListener('pageshow', clearLoading)" in base
    assert "@media print" in base


def test_owner_financial_document_uses_official_brand_asset_and_current_disclosure():
    document = (ROOT / "templates/billing/owner_document.html").read_text(encoding="utf-8")
    assert 'src="{{ brand.logo }}"' in document
    assert "Official {{ brand.name }} logo" in document
    assert "Premium Automotive Health &amp; Concierge" in document
    assert "Ajebo Fix Billing · Secure client access via Aura by Ajebo Fix" in document
    assert "manually entered estimate remains unpublished" not in document
    assert "It is <strong>not</strong> a native Billing PDF" not in document  # New scoped text, not old warning
    assert "is <strong>not</strong> a native Billing PDF" in document
