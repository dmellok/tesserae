"""The redesigned device page in a live Chromium, for a display of every kind
(tests/device_page_kinds.py): the rail fits a laptop screen without its own
scrollbar, a phone gets no horizontal overflow, toggles sit level with the
inputs beside them, the dependent controls come and go, the More menu and the
Delete flow work from the keyboard, the save bar follows edits, and an open
page follows a push (thumbnail, Queued badge and call-out) without a reload."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from tests.device_page_kinds import KINDS, _push, build_app, fake_visionect_service

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
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    from app import visionect

    mp = pytest.MonkeyPatch()
    mp.setattr(visionect, "service", fake_visionect_service)
    app = build_app(tmp_path_factory.mktemp("device-page"), live=True)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)
        mp.undo()


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()
        yield b
        b.close()


def _open(browser: Any, url: str, width: int = 1440, height: int = 900, theme: str = "dark") -> Any:
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
    ctx.add_init_script(f"localStorage.setItem('tesserae-theme', '{theme}')")
    pg = ctx.new_page()
    pg.goto(url, wait_until="load")
    return pg


@pytest.mark.parametrize("ui", ["paper", "classic"])
def test_the_rail_fits_a_laptop_screen_without_its_own_scrollbar(
    live: tuple[str, Flask], browser: Any, ui: str
) -> None:
    base, app = live
    app.config["SETTINGS_STORE"].patch_section("app", {"ui_design": ui})
    for _label, dev, _ in KINDS:
        pg = _open(browser, f"{base}/settings/devices/{dev}")
        box = pg.evaluate(
            "() => { const r = document.querySelector('.dx-rail');"
            " return [r.scrollHeight, r.clientHeight, r.getBoundingClientRect().height,"
            " getComputedStyle(r).position]; }"
        )
        assert box[0] <= box[1] + 1, (dev, box)  # no internal scroll
        assert box[2] <= 800, (dev, box)
        assert box[3] == "sticky"
        pg.context.close()


@pytest.mark.parametrize("ui", ["paper", "classic"])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_a_phone_gets_no_horizontal_overflow(
    live: tuple[str, Flask], browser: Any, ui: str, theme: str
) -> None:
    base, app = live
    app.config["SETTINGS_STORE"].patch_section("app", {"ui_design": ui})
    for _label, dev, _ in KINDS:
        pg = _open(browser, f"{base}/settings/devices/{dev}", width=390, height=844, theme=theme)
        # Open everything a phone might open, then measure.
        pg.evaluate("() => document.querySelectorAll('.dx-dp details').forEach(d => d.open = true)")
        wide = pg.evaluate(
            """() => {
              const W = document.documentElement.clientWidth, out = [];
              document.querySelectorAll('.dx-dp *').forEach((e) => {
                if (e.closest('.dx-vitals, .dx-secnav, pre, .dx-battery-chartbox')) return;
                const r = e.getBoundingClientRect();
                if (r.width && r.right > W + 1) out.push(e.className || e.tagName);
              });
              return out.slice(0, 5);
            }"""
        )
        assert wide == [], (dev, wide)
        # The rail is a compact strip above the sections, not a sticky column.
        assert pg.evaluate("getComputedStyle(document.querySelector('.dx-rail')).position") == (
            "static"
        )
        pg.context.close()


def test_toggles_sit_level_with_the_inputs_beside_them(
    live: tuple[str, Flask], browser: Any
) -> None:
    base, app = live
    app.config["SETTINGS_STORE"].patch_section("app", {"ui_design": "paper"})
    pg = _open(browser, f"{base}/settings/devices/tablet", width=1600)
    # Timing: Sleep interval, Update delivery, Button wake window, Stay awake.
    level = pg.evaluate(
        """() => {
          const row = document.querySelector('#timing .dx-fg');
          const ctls = [...row.children].filter(f => f.matches('.dx-f') && !f.hidden)
            .map(f => f.querySelector('.dx-f-ctl').getBoundingClientRect());
          return ctls.map(r => Math.round(r.top));
        }"""
    )
    assert len(level) == 4 and max(level) - min(level) <= 1, level
    # Quiet hours: a row of toggles only, with no empty label line above it.
    gap = pg.evaluate(
        """() => {
          const head = document.querySelector('#quiet-hours .dx-grp-head').getBoundingClientRect();
          const sw = document.querySelector('#quiet-hours .dx-f.is-toggle').getBoundingClientRect();
          return Math.round(sw.top - head.bottom);
        }"""
    )
    assert gap <= 14, gap
    pg.context.close()


def test_dependent_controls_follow_their_switches(live: tuple[str, Flask], browser: Any) -> None:
    base, _app = live
    pg = _open(browser, f"{base}/settings/devices/tablet")
    # Poll every appears beside Stay awake only while it is on.
    assert not pg.is_visible("[data-awake-poll]")
    pg.click("label[for='device-tablet-always_on']")
    assert pg.is_visible("[data-awake-poll]")
    # Quiet hours: off hides (and disables) the window fields.
    assert not pg.is_visible("#device-tablet-qh-start")
    assert pg.is_visible("[data-dep-off-note]")
    pg.click("label[for='device-tablet-qh-enabled']")
    assert pg.is_visible("#device-tablet-qh-start")
    assert not pg.is_visible("[data-dep-off-note]")
    assert pg.evaluate("document.getElementById('device-tablet-qh-start').disabled") is False
    # Synchronized wake: the mode picks Anchor or Times.
    pg.click("#sync-wake > summary")
    pg.select_option("#device-tablet-wa-mode", "times")
    assert pg.is_visible("#device-tablet-wa-times") and not pg.is_visible("#device-tablet-wa-anchor")
    pg.select_option("#device-tablet-wa-mode", "interval")
    assert pg.is_visible("#device-tablet-wa-anchor") and not pg.is_visible("#device-tablet-wa-times")
    # Edits show the save bar; Discard puts it away.
    assert pg.is_visible("[data-save-bar]")
    pg.click(".dx-save-bar-discard")
    pg.wait_for_function("() => document.querySelector('[data-save-bar]').hidden")
    pg.context.close()


def test_rotation_swaps_the_size_only_when_the_aspect_changes(
    live: tuple[str, Flask], browser: Any
) -> None:
    base, _app = live
    pg = _open(browser, f"{base}/settings/devices/study")

    def size() -> list[str]:
        return pg.evaluate(
            "() => ['w', 'h'].map(k => document.getElementById('device-study-panel-' + k).value)"
        )

    assert size() == ["800", "480"]
    pg.select_option("#device-study-orientation", "landscape_flipped")  # 180: same aspect
    assert size() == ["800", "480"]
    pg.select_option("#device-study-orientation", "portrait")  # 90: aspect changes
    assert size() == ["480", "800"]
    pg.click("[data-size-swap]")
    assert size() == ["800", "480"]
    pg.context.close()


def test_more_menu_and_delete_work_from_the_keyboard(
    live: tuple[str, Flask], browser: Any
) -> None:
    base, _app = live
    pg = _open(browser, f"{base}/settings/devices/study")
    pg.focus("[data-more-menu] > summary")
    pg.keyboard.press("Enter")
    assert pg.is_visible("text=Open mirror")
    pg.keyboard.press("Escape")
    assert not pg.is_visible("text=Open mirror")
    # Delete opens the confirmation under it; Cancel puts it away.
    pg.click("[data-del-open]")
    assert pg.is_visible("[data-del-form]")
    assert pg.get_attribute("[data-del-open]", "aria-expanded") == "true"
    pg.click("[data-delete-cancel]")
    assert not pg.is_visible("[data-del-form]")
    assert pg.get_attribute("[data-del-open]", "aria-expanded") == "false"
    pg.context.close()


def test_logs_and_frame_log_show_all_on_request(live: tuple[str, Flask], browser: Any) -> None:
    base, _app = live
    pg = _open(browser, f"{base}/settings/devices/kitchen")
    assert pg.locator("[data-log-list] li:visible").count() == 3
    pg.click("[data-log-more]")
    assert pg.locator("[data-log-list] li:visible").count() == 5
    pg.context.close()
    pg = _open(browser, f"{base}/settings/devices/frame")
    assert pg.is_visible("[data-flog-short]") and not pg.is_visible("[data-flog-all]")
    pg.click("[data-flog-more]")
    assert pg.is_visible("[data-flog-all]") and not pg.is_visible("[data-flog-short]")
    pg.context.close()


def test_an_open_page_follows_a_push(live: tuple[str, Flask], browser: Any) -> None:
    base, app = live
    pm = app.config["PUSH_MANAGER"]
    pg = _open(browser, f"{base}/settings/devices/study")
    assert pg.locator("[data-device-queued-thumb]").count() == 0
    assert pg.locator("[data-device-queued]").count() == 0
    _push(app, "study", "S1", "compS1")
    pm.record_frame_served("study", pm.latest_render_for("study"))
    pg.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    pg.wait_for_function(
        "() => (document.querySelector('.dx-now img') || {}).src?.includes('compS1')"
    )
    # A newer picture lands while the frame sleeps: the glass stays, a Queued
    # badge and a call-out appear.
    time.sleep(0.01)
    _push(app, "study", "S2", "compS2")
    pg.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    pg.locator("[data-device-queued-thumb]").wait_for(state="visible")
    assert "/renders/compS1.png" in pg.get_attribute(".dx-now img", "src")
    assert "New image queued" in pg.inner_text("[data-device-queued]")
    # The frame fetches it: one picture again, the call-out goes.
    pm.record_frame_served("study", pm.latest_render_for("study"))
    pg.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    pg.wait_for_function("!document.querySelector('[data-device-queued]')")
    assert "compS2" in pg.get_attribute(".dx-now img", "src")
    pg.context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_links_sit_left_of_buttons_on_every_kind(
    live: tuple[str, Flask], browser: Any, width: int
) -> None:
    """The action-order sweep (tests/test_action_order_browser.py) over every
    kind's page, with every state shown: a link-styled control is left of a
    button, the primary is rightmost, the DOM order is the visual order."""
    from tests.test_action_order_browser import _REVEAL_JS, _SWEEP_JS

    base, app = live
    found: list[str] = []
    for ui in ("paper", "classic"):
        app.config["SETTINGS_STORE"].patch_section("app", {"ui_design": ui})
        for _label, dev, _ in KINDS:
            pg = _open(browser, f"{base}/settings/devices/{dev}", width=width)
            for reveal in (False, True):
                if reveal:
                    pg.evaluate(_REVEAL_JS)
                found += [
                    f"{ui} {dev}{' (all shown)' if reveal else ''}: {'; '.join(i['problems'])}: "
                    f"{' | '.join(i['order'])}"
                    for i in pg.evaluate(_SWEEP_JS)
                ]
            pg.context.close()
    assert not found, "\n".join(found)
