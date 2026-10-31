"""The Dashboards list's By display / All switch in a live Chromium: the
toggle, its memory across a plain reload, bulk select, search and the
Archived tab in the flat list, and both designs at desktop and phone width."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.main import create_app
from app.state.page_store import Page

# PLAYWRIGHT_CHROMIUM_EXECUTABLE points at another Chromium (a headless shell
# from a different Playwright release) when the bundled one is missing.
_EXE = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(_EXE or p.chromium.executable_path).exists()
    except Exception:
        return False


def _launch(p: Any) -> Any:
    return p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()


_HAS_CHROMIUM = _chromium_available()
if not _HAS_CHROMIUM and os.environ.get("TESSERAE_REQUIRE_BROWSER_TESTS") == "1":
    raise RuntimeError(
        "Playwright Chromium is required; run playwright install --with-deps chromium"
    )
pytestmark = pytest.mark.skipif(not _HAS_CHROMIUM, reason="Playwright Chromium not installed")


def _serve(tmp_path: Path, design: str) -> Iterator[str]:
    from werkzeug.serving import make_server

    plugins_dir = Path(__file__).resolve().parents[1] / "plugins"
    app = create_app(testing=False, data_root=tmp_path / "data", plugins_dir=plugins_dir)
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True, "ui_design": design})
    store.patch_section("auth", {"disabled": True})
    client = app.test_client()
    for did, name in (("lounge", "Lounge"), ("study", "Study")):
        client.post("/settings/devices/add", data={"id": did, "kind": "esp32_client", "name": name})
    pages = app.config["PAGE_STORE"]
    pages.save(Page(id="both", name="Both", device_ids=["lounge", "study"]))
    pages.save(Page(id="alpha", name="alpha", device_ids=["study"]))
    pages.save(Page(id="loose", name="Loose"))
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


@pytest.fixture(params=["paper", "classic"])
def live(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    yield from _serve(tmp_path, request.param)


def _names(page: Any) -> list[str]:
    return page.locator("[data-dashrow]:visible .dx-dash-name").all_inner_texts()


def _pressed(page: Any) -> dict[str, str | None]:
    return {
        v: page.locator(f'.dx-dashtb-view button[value="{v}"]').get_attribute("aria-pressed")
        for v in ("groups", "all")
    }


def test_toggle_remembers_and_lists_each_dashboard_once(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{live}/pages")
        assert _pressed(page) == {"groups": "true", "all": "false"}
        assert len(_names(page)) == 4  # Both is listed under each display

        page.get_by_role("button", name="All", exact=True).click()
        page.wait_for_url("**/pages?view=all")
        assert _pressed(page) == {"groups": "false", "all": "true"}
        assert _names(page) == ["alpha", "Both", "Loose"]
        both = page.locator('[data-page-id="both"] .dx-dash-chips')
        assert both.inner_text().split() == ["Lounge", "Study"]
        assert page.locator('[data-page-id="loose"] .dx-dash-chips').inner_text() == "Unbound"
        assert page.locator("[data-dashtable-count]").inner_text().startswith("3 dashboards")

        # A plain visit comes back as it was left.
        page.goto(f"{live}/pages")
        assert _pressed(page) == {"groups": "false", "all": "true"}
        assert len(_names(page)) == 3

        # Keyboard: the buttons are focusable and Enter flips back.
        page.locator('.dx-dashtb-view button[value="groups"]').focus()
        page.keyboard.press("Enter")
        page.wait_for_url("**/pages?view=groups")
        assert _pressed(page) == {"groups": "true", "all": "false"}
        assert len(_names(page)) == 4
        browser.close()


def test_all_view_bulk_select_search_and_archived_tab(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{live}/pages?view=all")
        page.locator('[data-page-id="both"] [data-dash-select]').check()
        page.locator('[data-page-id="loose"] [data-dash-select]').check()
        assert page.locator("#dx-bulk-count").inner_text() == "2 selected"
        page.locator("#dx-bulk-clear").click()
        page.locator("[data-dash-selall]").check()
        assert page.locator("#dx-bulk-count").inner_text() == "3 selected"
        page.locator("[data-dash-selall]").uncheck()

        page.locator("[data-dashtable-search]").fill("stud")
        assert _names(page) == ["alpha", "Both"]
        assert page.locator("[data-dashtable-count]").inner_text().startswith("2 dashboards")
        page.locator("[data-dashtable-search]").fill("")

        # The Archived tab keeps the choice and its toggle stays on All.
        page.get_by_role("tab", name="Archived").click()
        page.wait_for_url("**tab=archived")
        assert _pressed(page)["all"] == "true"
        browser.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_all_view_has_no_sideways_scroll(live: str, width: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(f"{live}/pages?view=all")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.locator(".dx-dashtb-view").is_visible()
        browser.close()
