"""The lineup editor's Timing step on a display that sleeps, in a live
Chromium at desktop and phone width: the display's facts first, the warning
when a lineup sends more often than the display wakes (stronger for a widget
that pays per render), its one-click fix, the freshness line and the way back
to a timer, and a dashboard's Updates while shown."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.deck_model import Deck, DeckPage
from app.state.page_store import Cell, Page

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

ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
WARN = "The door wakes every 60 min, so only the last of every 12 sends is ever shown."
COST = "Each send may make a new AI picture: up to 11 an hour nobody sees."


@pytest.fixture
def real(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
    """A frame that wakes hourly ("door"), a dashboard whose widget pays per
    render ("art"), and a lineup sending it every 5 min on a timer."""
    from werkzeug.serving import make_server

    app: Flask = create_app(
        testing=True,
        data_root=tmp_path / "data",
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    settings = app.config["SETTINGS_STORE"]
    settings.patch_section("app", {"onboarded": True})
    settings.patch_section("auth", {"disabled": True})
    code = app.config["PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "door", "kind": "esp32_client"}),
    )
    assert resp.status_code == 201
    settings.patch_section("devices", {"door": {"sleep_interval_s": 3600, "always_on": False}})
    # The clock stands in for a widget that pays for a new AI picture each render.
    manifest = app.config["PLUGIN_REGISTRY"].get("clock_word").manifest
    manifest["render"] = {**(manifest.get("render") or {}), "paid": True}
    pages = app.config["PAGE_STORE"]
    clock = Cell(id="c", plugin="clock_word", x=0, y=0, w=10, h=10)
    pages.save(Page(id="art", name="Art", device_ids=["door"], cells=[clock]))
    pages.save(Page(id="notes", name="Notes", device_ids=["door"]))
    pages.save(Page(id="todo", name="Todo", device_ids=["door"]))
    decks = app.config["DECK_STORE"]
    decks.upsert(
        Deck(
            id="print", name="Printing now", device_ids=["door"], advance="timer",
            advance_trigger="interval", advance_interval_minutes=5,
            advance_days_of_week=ALL_DAYS, advance_smart_sync=False,
            pages=[DeckPage(page_id="art")],
        )
    )  # fmt: skip
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


def _open(p: Any, base: str, path: str, width: int) -> tuple[Any, Any, list[str]]:
    browser = p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + path, wait_until="load")
    return browser, page, errors


def _settle(page: Any) -> None:
    page.wait_for_function("() => [...document.images].every(i => i.complete)")


def _save(page: Any) -> None:
    with page.expect_navigation():
        page.locator("[data-save]").click()
    _settle(page)


def _box(page: Any, selector: str) -> dict[str, float]:
    box = page.locator(selector).first.bounding_box()
    assert box is not None, selector
    return box


@pytest.mark.parametrize("width", [1280, 390])
def test_a_timer_faster_than_the_wakes_warns_and_fixes_in_one_click(
    real: tuple[str, Flask], width: int
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/print/edit", width)
        lead = page.locator("#dxe-h-timing + .dxe-lead").inner_text()
        assert lead == "door sleeps and wakes every 60 min. Changes show at its next wake."
        warn = page.locator(".dxe-pace .dxe-wake-warn")
        assert warn.count() == 1 and "is-paid" in (warn.get_attribute("class") or "")
        text = warn.inner_text()
        assert WARN in text and COST in text
        link = warn.locator("a.dxe-link")
        assert link.inner_text() == "Change wake interval"
        assert link.get_attribute("href") == "/settings/devices/door#timing"
        # Links left, buttons right, the primary last.
        fix = warn.locator('[data-wake-fix="smart"]')
        assert fix.inner_text() == "Send just before each wake"
        assert "lu-btn-primary" in (fix.get_attribute("class") or "")
        lb, fb = link.bounding_box(), fix.bounding_box()
        assert lb and fb and lb["x"] < fb["x"]
        assert (
            fb["x"] + fb["width"]
            > _box(page, ".dxe-wake-warn")["x"] + 0.6 * _box(page, ".dxe-wake-warn")["width"]
        )
        # Nothing spills sideways on a phone.
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        fix.click()
        assert page.locator(".dxe-wake-warn").count() == 0
        fresh = page.locator(".dxe-fresh").inner_text()
        assert fresh.startswith("Redrawn just before each wake (every 60 min)")
        assert page.locator("[data-interval]").count() == 0  # one line, no timer
        assert page.locator("#dxe-save-state").inner_text() == "Unsaved changes"
        _save(page)
        assert app.config["DECK_STORE"].get("print").advance_smart_sync is True
        assert errors == []
        browser.close()


def test_send_on_a_timer_anyway_brings_back_the_timer_and_the_warning(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    decks = app.config["DECK_STORE"]
    decks.upsert(decks.get("print").model_copy(update={"advance_smart_sync": True}))
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/print/edit", 1280)
        assert page.locator(".dxe-wake-warn").count() == 0
        # Lead seconds stay under the display's options; the switch is gone.
        page.locator('[data-fold="options"] summary').click()
        opts = page.locator('[data-fold="options"]').inner_text()
        assert "How far ahead of a wake" in opts and page.locator("[data-smart]").count() == 0
        page.locator("[data-wake-timer]").click()
        assert page.locator("[data-interval]").input_value() == "5"
        assert WARN in page.locator(".dxe-wake-warn").inner_text()
        # A timer the wakes keep up with: no warning, a quiet way back.
        page.locator("[data-interval]").select_option("60")
        assert page.locator(".dxe-wake-warn").count() == 0
        assert "Each send waits for its next wake." in page.locator(".dxe-pace").inner_text()
        _save(page)
        d = decks.get("print")
        assert (d.advance_smart_sync, d.advance_interval_minutes) == (False, 60)
        assert errors == []
        browser.close()


def test_keep_ready_is_ready_for_button_presses(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/print/edit", 1280)
        page.locator('[data-fold="options"] summary').click()
        opts = page.locator('[data-fold="options"]').inner_text()
        assert "Ready for button presses" in opts and "Keep ready" not in opts
        assert "Draws ahead so a press shows at once. Doesn't wake the display." in opts
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_updates_while_shown_faster_than_the_wakes_warns_where_it_is_set(
    real: tuple[str, Flask], width: int
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    decks = app.config["DECK_STORE"]
    decks.upsert(decks.get("print").model_copy(update={"advance_smart_sync": True}))
    pages = app.config["PAGE_STORE"]
    pages.save(pages.get("art").model_copy(update={"refresh_minutes": 5}))
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/print/edit", width)
        words = "Art updates every 5 min while shown. The door wakes every 60 min, so only the last of every 12 updates is ever shown."
        # In the Timing card...
        assert words in page.locator(".dxe-pace .dxe-wake-warn").inner_text()
        # ...and under the dashboard's own control.
        page.locator('[data-expand="art"]').click()
        page.locator('[data-edit="art"]').click()
        assert page.locator('[data-updates="art"]').count() == 1
        under = page.locator(".dxe-edit + .dxe-wake-warn")
        assert words in under.inner_text()
        assert "Each update may make a new AI picture" in under.inner_text()
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        # Redrawn on each wake already: the fix stops it, and the control goes.
        under.locator("[data-wake-updates]").click()
        assert page.locator(".dxe-wake-warn").count() == 0
        assert page.locator('[data-updates="art"]').count() == 0
        _save(page)
        assert pages.get("art").refresh_minutes == 0
        assert errors == []
        browser.close()


def test_a_rotation_shorter_than_a_wake_says_dashboards_can_be_skipped(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    app.config["DECK_STORE"].upsert(
        Deck(
            id="day", name="Day", device_ids=["door"], advance="timer",
            advance_trigger="cycle", advance_anchor="06:00", advance_end_at="09:00",
            advance_interval_minutes=30, advance_days_of_week=[5],
            advance_smart_sync=True,
            pages=[DeckPage(page_id="notes"), DeckPage(page_id="todo", dwell_minutes=90)],
        )
    )  # fmt: skip
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/day/edit", 1280)
        warn = page.locator(".dxe-wake-warn")
        assert (
            "The door wakes every 60 min, so a dashboard shown for less than that can be skipped."
            in warn.inner_text()
        )
        assert "Each change is sent just before a wake" in page.locator(".dxe-fresh").inner_text()
        warn.locator('[data-wake-fix="dwell"]').click()
        assert page.locator(".dxe-wake-warn").count() == 0
        assert page.locator("[data-interval]").input_value() == "60"
        _save(page)
        d = app.config["DECK_STORE"].get("day")
        assert d.advance_interval_minutes == 60
        assert [x.dwell_minutes for x in d.pages] == [None, 90]
        assert errors == []
        browser.close()
