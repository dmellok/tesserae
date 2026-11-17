"""Add a Meural from the desktop and phone Add device card."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.main import REPO_ROOT, create_app

_EXE = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(_EXE or p.chromium.executable_path).exists()
    except Exception:
        return False


_HAS_CHROMIUM = _chromium_available()
if not _HAS_CHROMIUM and os.environ.get("TESSERAE_REQUIRE_BROWSER_TESTS") == "1":
    raise RuntimeError(
        "Playwright Chromium is required; run playwright install --with-deps chromium"
    )
pytestmark = pytest.mark.skipif(not _HAS_CHROMIUM, reason="Playwright Chromium not installed")


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    from werkzeug.serving import make_server

    app = create_app(
        testing=False,
        data_root=tmp_path_factory.mktemp("meural-add"),
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    app.config["TESTING"] = True
    app.config["SETTINGS_STORE"].patch_section("app", {"onboarded": True})
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    app.config["SETTINGS_STORE"].patch_section("auth", {"disabled": True})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()
        yield browser
        browser.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_meural_tab_adds_a_frame_and_opens_its_setup_page(
    live: str, browser: Any, width: int
) -> None:
    context = browser.new_context(viewport={"width": width, "height": 1000})
    page = context.new_page()
    try:
        page.goto(f"{live}/settings/devices", wait_until="load")
        page.locator("[data-add-toggle][aria-expanded]").click()
        page.get_by_role("tab", name="Netgear Meural", exact=True).click()
        tab = page.get_by_role("tab", name="Netgear Meural", exact=True)
        assert tab.get_attribute("aria-selected") == "true"
        branch = page.locator('[data-transport-branch="meural"]')
        assert branch.is_visible()
        assert page.locator("[data-segmented-help-meural]").is_visible()
        assert not page.locator('[data-transport-branch="rest"]').is_visible()
        assert not page.locator("[data-segmented-help-rest]").is_visible()
        branch.get_by_label("Delivery", exact=True).select_option("local")
        branch.get_by_label("Device id").fill(f"meural_test_{width}")
        branch.get_by_label("Display name", exact=True).fill("Example frame")
        branch.get_by_label("Frame address").fill("192.0.2.10")
        # Switching tabs preserves typed values and hides the inactive help.
        page.get_by_role("tab", name="MQTT broker", exact=True).click()
        assert not page.locator("[data-segmented-help-meural]").is_visible()
        tab.click()
        assert branch.get_by_label("Device id").input_value() == f"meural_test_{width}"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        branch.get_by_role("button", name="Add Netgear Meural", exact=True).click()
        page.wait_for_url(f"{live}/settings/devices/meural_test_{width}")
        assert page.get_by_role("heading", name="Example frame", exact=True).is_visible()
        assert page.locator(f"#device-meural_test_{width}-host").count() == 1
    finally:
        context.close()
