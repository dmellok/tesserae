"""The Samsung The Frame device page in a live Chromium: the Home Assistant
switch field follows the Power control select without a reload."""

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
        data_root=tmp_path_factory.mktemp("frame-page"),
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"kind": "samsung_frame", "id": "den_frame", "name": "Den", "host": "192.0.2.20"},
    )
    store.patch_section("auth", {"disabled": True})
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
        b = p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()
        yield b
        b.close()


def test_the_switch_field_follows_power_control_without_a_reload(live: str, browser: Any) -> None:
    ctx = browser.new_context(viewport={"width": 1440, "height": 1100}, color_scheme="dark")
    ctx.add_init_script("localStorage.setItem('tesserae-theme', 'dark')")
    pg = ctx.new_page()
    pg.goto(f"{live}/settings/devices/den_frame", wait_until="load")
    field = "#device-den_frame-power_switch"
    # Default Power control is the TV's own power: no switch field.
    assert pg.input_value("#device-den_frame-power_control") == "tv"
    assert not pg.is_visible(field)
    pg.select_option("#device-den_frame-power_control", "switch")
    assert pg.is_visible(field)
    pg.select_option("#device-den_frame-power_control", "none")
    assert not pg.is_visible(field)
    pg.select_option("#device-den_frame-power_control", "switch")
    assert pg.is_visible(field)
    # It sits right after Power control, in the Power group.
    order = pg.evaluate(
        """() => Array.from(document.querySelectorAll('#grp-power [name]'))
                 .map((el) => el.name)"""
    )
    assert order == ["power_control", "power_switch", "sleep_when_in_use"]
    # A hidden field is still submitted, so its value survives a save.
    pg.fill(field, "switch.den_tv")
    pg.select_option("#device-den_frame-power_control", "tv")
    assert not pg.is_visible(field)
    assert pg.input_value(field) == "switch.den_tv"
    ctx.close()
