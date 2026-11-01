"""The queued notice in a live Chromium: Lineups "Push now" on a sleeping
frame reads "Queued · picks up ~…" (and stays up long enough to read), and a
Send to a sleeping frame lands on History with the queued flash."""

from __future__ import annotations

import io
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from flask import Flask
from PIL import Image

from app.main import REPO_ROOT, create_app
from app.push import PushResult
from tests.test_lineups_browser import _HAS_CHROMIUM, _launch, _open, live  # noqa: F401

pytestmark = pytest.mark.skipif(not _HAS_CHROMIUM, reason="Playwright Chromium not installed")

QUEUED = {
    "device_id": "kitchen",
    "name": "Kitchen",
    "mode": "queued",
    "expected_at": 1_900_000_000,
    "reason": "sleeping",
    "text": "Kitchen is asleep: it will pick this up at its next check-in (~15:48), "
    "or press its wake button to get it now.",
    "short": "Queued · picks up ~15:48",
}


def test_push_now_on_a_sleeping_frame_reads_queued(live: str) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, api = _open(p, live)
        api.push_message = "Queued Dinner for Kitchen. " + QUEUED["text"]

        def reply(route: Any) -> None:
            body = {
                "ok": True,
                "lands": "lands ~15:48",
                "message": api.push_message,
                "fresh": True,
                "delivery": [QUEUED],
            }
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

        page.route("**/decks/dinner/push-step", reply)
        tile = page.locator('#lu-row-dinner .lu-step[data-step="0"] .lu-tile')
        tile.hover()
        tile.locator("[data-push]").click()
        page.wait_for_function(
            "() => document.querySelector('#lu-row-dinner .lu-tile.push-queued')"
        )
        assert tile.locator(".lu-pushed-msg").inner_text() == "Queued · picks up ~15:48"
        live_text = page.locator("[data-lu-live]").inner_text()
        assert "Kitchen is asleep: it will pick this up" in live_text
        # Still up after the plain "Pushed" note would have gone (2.4 s).
        page.wait_for_timeout(3200)
        assert "push-queued" in (tile.get_attribute("class") or "")
        page.wait_for_function(
            "() => !document.querySelector('#lu-row-dinner .lu-tile.push-queued')",
            timeout=8000,
        )
        assert not page._lu_errors
        browser.close()


@pytest.fixture
def send_server(tmp_path: Path) -> Iterator[str]:
    from werkzeug.serving import make_server

    app: Flask = create_app(
        testing=False,
        data_root=tmp_path / "data",
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True, "locale": "en-US"})
    store.patch_section("auth", {"disabled": True})
    client = app.test_client()
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "hallway", "kind": "esp32_client"}),
    )
    assert resp.status_code == 201
    now = time.time()
    app.config["DEVICE_TELEMETRY"].record_heartbeat(
        "hallway", received_at=now, parsed={"sleep_until": now + 20 * 60}, configured_sleep_s=900
    )
    pm = MagicMock()
    pm.push_image.return_value = PushResult(status="sent", page_id="x.png")
    pm.latest_render_for.return_value = None
    app.config["PUSH_MANAGER"] = pm
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_send_to_a_sleeping_frame_shows_the_queued_flash(
    send_server: str, tmp_path: Path, scheme: str
) -> None:
    from playwright.sync_api import sync_playwright

    png = tmp_path / "photo.png"
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (200, 80, 40)).save(buf, format="PNG")
    png.write_bytes(buf.getvalue())
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme=scheme)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(send_server + "/send", wait_until="load")
        page.set_input_files("[data-send-file]", str(png))
        page.check('input[name="device_id"][value="hallway"]')
        page.wait_for_selector("[data-send-push]:not([disabled])")
        with page.expect_navigation():
            page.click("[data-send-push]")
        assert "/history" in page.url
        flash = page.locator(".flash--queued")
        flash.wait_for()
        assert flash.locator(".ph-moon-stars").count() == 1
        assert (
            "hallway is asleep: it will pick this up at its next check-in (~" in flash.inner_text()
        )
        assert page.locator(".flashes[role=status]").count() == 1
        # Not styled as success or error.
        assert page.locator(".flash--queued.flash--ok, .flash--queued.flash--error").count() == 0
        assert not errors
        browser.close()
