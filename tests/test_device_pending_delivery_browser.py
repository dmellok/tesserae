"""Settings > Devices in a live Chromium: an open page follows a push.

The list is server-rendered, so before this a page left open kept the old
thumbnail after a push until a manual reload. The sync script polls the
per-device render signature and swaps in the thumbnail and tiles, so the
image URL changes with the digest and the Queued badge appears and clears."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

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

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89"
    b"\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def live(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    app = create_app(
        testing=False,
        data_root=tmp_path / "data",
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})
    client = app.test_client()
    client.post(
        "/settings/devices/add",
        data={"id": "door", "kind": "esp32_client", "name": "Hallway Frame", "transport": "rest"},
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _render(digest: str, comp: str) -> dict[str, Any]:
    return {
        "digest": digest,
        "ext": "bin",
        "filename": f"{digest}.bin",
        "renderer_id": "esp32_bin",
        "timestamp": time.time(),
        "composition_digest": comp,
        "page_id": "",
    }


def _push(app: Flask, render: dict[str, Any]) -> None:
    pm = app.config["PUSH_MANAGER"]
    renders_dir: Path = app.config["RENDERS_DIR"]
    renders_dir.mkdir(parents=True, exist_ok=True)
    (renders_dir / f"{render['composition_digest']}.png").write_bytes(_PNG)
    with pm._lock:
        if pm._latest_renders.get("door") is None:
            pm._latest_renders["door"] = render
        else:
            pm._replace_latest_render_locked("door", render)


def _fetch(app: Flask) -> None:
    pm = app.config["PUSH_MANAGER"]
    pm.record_frame_served("door", pm.latest_render_for("door"))


def test_open_page_follows_a_push_and_a_fetch(live: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = live
    _push(app, _render("A", "compA"))
    _fetch(app)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"{base}/settings/devices")
            if not page.is_visible(".dx-thumb img"):
                page.click(".dx-devrow")
            img = page.locator(".dx-thumb img")
            img.wait_for(state="visible")
            assert "/preview/door.png?v=compA" in img.get_attribute("src")
            assert page.locator("[data-device-queued]").count() == 0

            # A push lands while the page is open: the glass stays, the new
            # image is queued next to it, with no reload.
            _push(app, _render("B", "compB"))
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.locator("[data-device-queued-thumb]").wait_for(state="visible")
            assert "/renders/compA.png" in page.locator(".dx-thumb img").get_attribute("src")
            queued = page.locator("[data-device-queued-thumb] img")
            assert "/preview/door.png?v=compB" in queued.get_attribute("src")
            assert "New image queued" in page.locator("[data-device-queued]").inner_text()

            # The frame fetches it: one thumbnail again, on the new digest.
            _fetch(app)
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_function("!document.querySelector('[data-device-queued]')")
            assert "/preview/door.png?v=compB" in page.locator(".dx-thumb img").get_attribute("src")
            assert page.locator("[data-device-queued-thumb]").count() == 0
        finally:
            browser.close()
