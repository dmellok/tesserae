"""Disable / Enable on the Widgets page, in a live Chromium: the sidebar
Admin pages entry goes away when GTFS is disabled and comes back when it is
enabled, and an in-use widget asks for confirmation naming the dashboards."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app

# PLAYWRIGHT_CHROMIUM_EXECUTABLE points at another Chromium (a headless shell)
# when Playwright's own build is not installed.
_EXE = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(_EXE or p.chromium.executable_path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium not installed"
)

NAV_LINK = 'nav a[href$="/plugins/gtfs/"]'
ROW = '[data-lf-item][data-sort-id="gtfs"]'


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    app: Flask = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


def test_disable_then_enable_gtfs_from_the_widgets_page(served: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = served
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(f"{base}/plugins/", wait_until="networkidle")
        assert page.locator(NAV_LINK).count() == 1

        page.locator(f'{ROW} button[data-act="disable"]').click()
        page.wait_for_load_state("networkidle")
        assert page.locator(NAV_LINK).count() == 0
        disabled = page.locator(f'[data-lf-section="disabled"] {ROW}')
        assert disabled.count() == 1
        assert page.goto(f"{base}/plugins/gtfs/", wait_until="networkidle").status == 404
        assert "is disabled" in page.locator("main").inner_text()

        page.goto(f"{base}/plugins/", wait_until="networkidle")
        page.locator(f'{ROW} button[data-act="enable"]').click()
        page.wait_for_load_state("networkidle")
        assert page.locator(NAV_LINK).count() == 1
        assert page.locator('[data-lf-section="disabled"]').count() == 0
        browser.close()

    assert errors == []


def test_in_use_widget_asks_before_disabling(served: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    from app.state.page_store import Cell, Page

    base, app = served
    app.config["PAGE_STORE"].save(
        Page(id="k", name="Kitchen", cells=[Cell(id="c1", plugin="gtfs", x=0, y=0, w=200, h=200)])
    )
    seen: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()
        page = browser.new_page()

        def on_dialog(d: object) -> None:
            seen.append(d.message)  # type: ignore[attr-defined]
            d.dismiss()  # type: ignore[attr-defined]

        page.on("dialog", on_dialog)
        page.goto(f"{base}/plugins/", wait_until="networkidle")
        page.locator(f'{ROW} button[data-act="disable"]').click()
        page.wait_for_timeout(300)
        # Dismissed: nothing changed.
        assert page.locator(NAV_LINK).count() == 1
        browser.close()

    assert len(seen) == 1 and "Kitchen" in seen[0] and "placeholder" in seen[0]
