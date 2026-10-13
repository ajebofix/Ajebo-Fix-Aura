"""Real Chromium smoke review of Aura's public homepage at key viewport sizes."""

from __future__ import annotations

import os
import threading

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server


def test_public_homepage_desktop_and_mobile(app, tmp_path):
    """Render the actual Flask/Jinja page with its served static assets."""
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    image_dir = os.getenv("AURA_BROWSER_SCREENSHOT_DIR")
    if image_dir:
        os.makedirs(image_dir, exist_ok=True)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for label, width, height in (
                ("mobile", 375, 812),
                ("tablet", 768, 1024),
                ("desktop", 1440, 900),
            ):
                page = browser.new_page(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                )
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="networkidle",
                    timeout=30000,
                )
                assert response is not None and response.status == 200
                assert page.title() == "Aura by Ajebo Fix | Private Automotive Health Management"
                assert page.locator("h1").count() == 1
                assert "Your vehicle has a history" in page.locator("h1").inner_text()
                assert page.locator("form#access-form input[name='csrf_token']").count() == 1
                assert page.locator("form#access-form input[name='contact_consent']").count() == 1
                assert page.locator("a[href='/auth/login']").count() >= 1
                assert page.locator("a[href='#request-access']").count() >= 1
                assert page.locator(".hero-showcase .pill").inner_text() == "ILLUSTRATION"
                assert page.locator(".record-node").count() == 3
                assert page.locator(".record-flow-caption").inner_text() == "MEMORY BEFORE RECOMMENDATION"

                # Verify real CSS is being served, layout is responsive and
                # there are no overflowing columns on narrower devices.
                assert page.locator("body").evaluate(
                    "(el) => getComputedStyle(el).backgroundColor"
                ) != "rgba(0, 0, 0, 0)"
                assert page.evaluate(
                    "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
                ), f"Horizontal overflow at {width}px"

                brand_image = page.locator("header .identity img")
                assert brand_image.evaluate(
                    "(img) => img.complete && img.naturalWidth > 0"
                ), f"Brand image failed to load at {width}px"
                assert page.locator("#request-access").is_visible()
                if image_dir:
                    page.screenshot(
                        path=os.path.join(image_dir, f"aura-home-{label}.png"),
                        full_page=True,
                        animations="disabled",
                    )
                page.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
