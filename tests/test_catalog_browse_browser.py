"""Catalog Browse install flow in a live Chromium.

A successful install used to raise two toasts: the success message, then
"Install failed: network error". The success branch injects the topbar
"Restart required" button before the topbar's theme toggle, but the first
``[data-theme-toggle]`` inside the topbar is the drawer's, nested in the nav,
so ``insertBefore`` threw NotFoundError. The trailing ``.catch`` on the
promise chain then reported that DOM error as a network failure, and the
restart button never appeared until a reload. Neither half is visible to a
source-level or rendered-HTML assertion, so this runs in a real browser.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.marketplace import CatalogEntry, InstallResult, Marketplace
from app.updater import Updater


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium not installed"
)


def _fake_entry() -> CatalogEntry:
    return CatalogEntry(
        id="sample",
        name="Sample",
        description="A sample widget.",
        icon=None,
        author_name="t",
        author_github=None,
        tags=[],
        kind="widget",
        tesserae_compat="0.x",
        official=False,
        screenshot_sizes=[],
        extra_screenshot_count=0,
        folders=None,
        release_version="1.0.0",
        release_tarball_url="https://example.invalid/sample.tar.gz",
        release_sha256="0" * 64,
        source=None,
    )


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[str]:
    """The app on a real loopback port with the marketplace mocked, so the
    Browse page lists one installable entry and its install succeeds without
    touching the network. Password auth is off: loopback clients pass."""
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
    mkt = MagicMock(spec=Marketplace)
    mkt.index_url.return_value = "https://example.invalid/widgets.json"
    mkt.screenshots_base.return_value = "https://example.invalid/screenshots/"
    mkt.plugins_dir.return_value = tmp_path / "plugins"
    mkt.installed.return_value = {}
    mkt.fetch_index.return_value = [_fake_entry()]
    mkt.install.return_value = InstallResult(plugin_id="sample", version="1.0.0")
    app.config["MARKETPLACE"] = mkt
    app.config["UPDATER"] = MagicMock(spec=Updater)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


def test_install_from_sheet_shows_one_toast_and_the_restart_button(live_server: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{live_server}/plugins/browse", wait_until="networkidle")
        assert page.locator(".topbar-restart-form").count() == 0
        page.locator(".cat-row").first.click()
        page.wait_for_selector(".cat-sheet-actions button.cat-btn--primary")
        page.click(".cat-sheet-actions button.cat-btn--primary")
        page.wait_for_selector(".flash")
        page.wait_for_timeout(500)
        flashes = page.locator(".flash").all()
        messages = [f.locator(".flash-msg").inner_text() for f in flashes]
        kinds = [f.get_attribute("class") for f in flashes]
        restart = page.locator(".topbar-restart-form").count()
        browser.close()

    assert errors == []
    assert len(messages) == 1, messages
    assert messages[0].startswith("Installed Sample v1.0.0")
    assert "flash--ok" in (kinds[0] or "")
    assert restart == 1
