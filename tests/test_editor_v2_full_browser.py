"""Dashboard editor v2, the whole page in a live Chromium.

These drive the real ``/pages/<id>`` editor (steps, preview and bottom bar
together), not the preview partial on its own, and check what ended up in the
page store: pending autosaves must survive a layout change, a widget swap and
a duplicate, and the padding preset must save.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Cell, Page, Panel

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
pytestmark = [
    pytest.mark.skipif(not _HAS_CHROMIUM, reason="Playwright Chromium not installed"),
    # Closing the browser mid-request can leave a static file open in the test
    # server thread; that surfaces as a stray ResourceWarning in a later test.
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

PAGE_ID = "full"


@pytest.fixture(scope="module")
def pw() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    app: Flask = create_app(
        testing=False,
        data_root=tmp_path / "data",
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    settings = app.config["SETTINGS_STORE"]
    settings.patch_section("app", {"onboarded": True})
    settings.patch_section("auth", {"disabled": True})
    app.config["PAGE_STORE"].save(
        Page(
            id=PAGE_ID,
            name="Full editor",
            panel=Panel(w=800, h=480),
            cells=[
                Cell(
                    id="c1",
                    plugin="weather_now",
                    options={"label": "Home"},
                    x=0,
                    y=0,
                    w=400,
                    h=480,
                ),
                Cell(id="c2", plugin="clock_word", x=400, y=0, w=400, h=480),
            ],
        )
    )
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}", app
    finally:
        srv.shutdown()
        thread.join(timeout=10)


def _open(pw: Any, base: str) -> tuple[Any, Any, list[str]]:
    browser = pw.chromium.launch(executable_path=_EXE) if _EXE else pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/pages/{PAGE_ID}", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => window.EV2 && window.EV2Preview && document.querySelector('.ev2pv-edge')"
    )
    return browser, page, errors


def _cell(app: Flask, page_id: str, cell_id: str) -> Cell:
    page = app.config["PAGE_STORE"].get(page_id)
    return next(c for c in page.cells if c.id == cell_id)


def _wait_for(
    check: Callable[[], bool], what: str, timeout: float = 15.0, page: Any = None
) -> None:
    """Poll ``check``. Pass ``page`` when the page has a route handler: that only
    runs while Playwright is being pumped (``time.sleep`` would starve it)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        if page is not None:
            page.wait_for_timeout(100)
        else:
            time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def _type_zoom(page: Any, cell_id: str, value: str) -> None:
    """A range edit: it autosaves after a short debounce, so it stays pending."""
    page.locator(f'.ev2-cl[data-cell-id="{cell_id}"] [data-ev2-row]').click()
    page.evaluate(
        """([id, v]) => {
          const el = document.getElementById('ev2-' + id + '-zoom');
          el.value = v;
          el.dispatchEvent(new Event('input', { bubbles: true }));
        }""",
        [cell_id, value],
    )


def test_pending_autosave_and_resize_both_survive(server: tuple[str, Flask], pw: Any) -> None:
    base, app = server
    browser, page, errors = _open(pw, base)
    _type_zoom(page, "c1", "1.5")
    line = page.locator(".ev2pv-edge--v")
    lb = line.bounding_box()
    ob = page.locator(".ev2pv-overlay").bounding_box()
    assert lb and ob
    x0 = lb["x"] + lb["width"] / 2
    y = lb["y"] + lb["height"] / 2
    target = ob["x"] + ob["width"] * 0.25
    page.mouse.move(x0, y)
    page.mouse.down()
    for k in range(1, 11):
        page.mouse.move(x0 + (target - x0) * k / 10, y)
    page.mouse.up()
    _wait_for(
        lambda: _cell(app, PAGE_ID, "c1").w != 400 and _cell(app, PAGE_ID, "c1").zoom == 1.5,
        "the resize and the zoom edit to both be saved",
    )
    c1, c2 = _cell(app, PAGE_ID, "c1"), _cell(app, PAGE_ID, "c2")
    assert c1.w + c2.w == 800
    assert c1.zoom == 1.5
    assert not errors
    browser.close()


def test_replace_with_a_pending_edit_keeps_the_new_widgets_defaults(
    server: tuple[str, Flask], pw: Any
) -> None:
    base, app = server
    browser, page, errors = _open(pw, base)
    _type_zoom(page, "c1", "1.5")
    page.locator('.ev2-cl[data-cell-id="c1"] [data-ev2-replace]').click()
    page.locator("[data-ev2-pk-q]").fill("clock_word")  # the list starts on Weather's category
    page.locator('[data-ev2-pk] [data-widget="clock_word"]').click()
    _wait_for(lambda: _cell(app, PAGE_ID, "c1").plugin == "clock_word", "the swap to be saved")
    page.wait_for_load_state("load")
    cell = _cell(app, PAGE_ID, "c1")
    assert cell.zoom == 1.5  # the pending edit was sent first
    # The new widget's default-on switches were not cleared by a second post.
    assert cell.options.get("show_seconds_dot") is True
    assert cell.options.get("show_tone") is True
    assert cell.options.get("show_phase_badge") is True
    assert not errors
    browser.close()


def test_duplicate_copies_a_pending_edit(server: tuple[str, Flask], pw: Any) -> None:
    base, app = server
    browser, page, errors = _open(pw, base)
    _type_zoom(page, "c1", "1.5")
    page.locator(
        '.ev2-bar form[data-ev2-after-save] button[aria-label="Duplicate dashboard"]'
    ).click()
    store = app.config["PAGE_STORE"]

    def copied() -> bool:
        return any(p.id != PAGE_ID for p in store.list())

    _wait_for(copied, "the duplicate to exist")
    copy = next(p for p in store.list() if p.id != PAGE_ID)
    left = next(c for c in copy.cells if c.x == 0)  # the copy's cells get new ids
    assert left.plugin == "weather_now"
    assert left.zoom == 1.5
    assert _cell(app, PAGE_ID, "c1").zoom == 1.5
    assert not errors
    browser.close()


def test_padding_preset_saves(server: tuple[str, Flask], pw: Any) -> None:
    base, app = server
    browser, page, errors = _open(pw, base)
    page.locator('.ev2-cl[data-cell-id="c1"] [data-ev2-row]').click()
    page.locator(
        '.ev2-cl[data-cell-id="c1"] details.ev2-adv:has-text("Refresh, taps") summary'
    ).click()
    select = page.locator("#ev2-c1-pad")
    select.select_option("8")
    _wait_for(lambda: _cell(app, PAGE_ID, "c1").padding_override == 8, "the 8 px preset to save")
    select.select_option("")  # back to the dashboard padding
    _wait_for(
        lambda: _cell(app, PAGE_ID, "c1").padding_override is None, "the padding reset to save"
    )
    assert not errors
    browser.close()


@pytest.mark.parametrize("is_new", [False, True])
def test_new_lineup_round_trip_keeps_dashboard_and_returns_to_open_step(
    server: tuple[str, Flask], pw: Any, is_new: bool
) -> None:
    from urllib.parse import parse_qs, urlsplit

    from app import device_service

    base, app = server
    device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id="hall",
        kind_id="esp32_client",
    )
    store = app.config["PAGE_STORE"]
    original = store.get(PAGE_ID)
    # The new-dashboard case starts on the test display and must ask for one.
    store.save(original.model_copy(update={"device_ids": [] if is_new else ["hall"]}))
    store.save(Page(id="companion", name="Companion", device_ids=["hall"]))
    browser, page, errors = _open(pw, base)
    try:
        if is_new:
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(f"{base}/pages/{PAGE_ID}?new=1", wait_until="domcontentloaded")
            page.wait_for_function("() => window.EV2 && EV2.steps")
        step = "lineup" if is_new else "schedules"
        page.evaluate("key => EV2.steps.open(key)", step)
        page.locator("[data-ev2-lopen]").click()
        page.get_by_role("link", name="Make a new lineup", exact=True).click()
        query = parse_qs(urlsplit(page.url).query)
        assert query["page_id"] == [PAGE_ID]
        assert query.get("display") == (None if is_new else ["hall"])
        target = query["return"][0]
        assert page.locator('[data-fk="f-cancel"]').get_attribute("href") == target
        assert page.get_by_role("link", name="Back to dashboard").get_attribute("href") == target
        page.evaluate("() => sessionStorage.removeItem('ev2-step:full')")
        page.locator('[data-fk="f-cancel"]').click()
        page.wait_for_function("key => EV2.steps.current() === key", arg=step)
        page.locator("[data-ev2-lopen]").click()
        page.get_by_role("link", name="Make a new lineup", exact=True).click()
        page.locator("#dxe-title").fill("Dashboard round trip")
        if is_new:
            page.locator('[data-pick-display="hall"]').click()
        page.locator('[data-when="between"]').click()
        page.locator('[data-time="end"]').fill("16:30")
        page.locator('[data-time="end"]').dispatch_event("change")
        page.locator('[data-goto="3"]').click()
        assert page.locator(f'[data-step="{PAGE_ID}"]').count() == 1
        page.locator('[data-add="companion"]').click()
        page.evaluate("() => sessionStorage.removeItem('ev2-step:full')")
        page.locator("[data-create]").click()
        page.wait_for_url(f"**/pages/{PAGE_ID}{'?new=1' if is_new else ''}#{step}")
        page.wait_for_function("key => EV2.steps.current() === key", arg=step)
        joined = page.locator("[data-ev2-joined]")
        assert "Dashboard round trip" in joined.inner_text()
        assert joined.get_by_role("button", name="Remove").count() == 1
        assert store.get(PAGE_ID).device_ids == ["hall"]
        assert not errors
    finally:
        browser.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_look_warning_updates_and_reset_keeps_widget_options(
    server: tuple[str, Flask], pw: Any, width: int
) -> None:
    base, app = server
    store = app.config["PAGE_STORE"]
    original = store.get(PAGE_ID)
    cells = [
        c.model_copy(
            update={
                "plugin": "ha_dashboard",
                "options": {"dark_mode": True},
                "theme": "dark",
                "padding_override": 8,
            }
        )
        if c.id == "c1"
        else c
        for c in original.cells
    ]
    store.save(original.model_copy(update={"cells": cells}))
    browser, page, errors = _open(pw, base)
    try:
        page.set_viewport_size({"width": width, "height": 1000})
        page.locator('[data-ev2-toggle="look"]').click()
        box = page.locator("[data-ev2-ovbox]")
        assert "uses its own dark mode" in box.inner_text()
        assert "own padding" in box.inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("[data-ev2-resetov]").click()
        _wait_for(
            lambda: (
                _cell(app, PAGE_ID, "c1").padding_override is None
                and _cell(app, PAGE_ID, "c1").theme is None
            ),
            "cell look reset",
            page=page,
        )
        assert _cell(app, PAGE_ID, "c1").options["dark_mode"] is True
        assert "uses its own dark mode" in box.inner_text()
        box.locator('[data-ev2-setting="opt_dark_mode"]').click()
        theme = page.locator('[name="opt_dark_mode"]')
        assert theme.evaluate("el => document.activeElement === el")
        page.locator('label[for="ev2-c1-opt-dark_mode"]').click()
        assert not theme.is_checked()
        _wait_for(
            lambda: _cell(app, PAGE_ID, "c1").options.get("dark_mode") is False,
            "widget dark mode cleared",
            page=page,
        )
        assert box.get_attribute("hidden") is not None
        assert "override" not in page.locator('[data-ev2-sum="look"]').inner_text()
        page.locator('label[for="ev2-c1-opt-dark_mode"]').click()
        assert theme.is_checked()
        page.locator('[data-ev2-toggle="look"]').click()
        assert "uses its own dark mode" in box.inner_text()
        assert "1 widget overrides it" in page.locator('[data-ev2-sum="look"]').inner_text()
        assert not errors
    finally:
        browser.close()


@pytest.mark.parametrize("is_new", [False, True])
def test_unified_lineups_create_return_edit_and_remove_schedule(
    server: tuple[str, Flask], pw: Any, is_new: bool
) -> None:
    from app.state.deck_model import Deck, DeckPage

    base, app = server
    app.config["PAGE_STORE"].save(Page(id="companion", name="Companion"))
    decks = app.config["DECK_STORE"]
    decks.upsert(
        Deck(
            id="rotation",
            name="Rotation",
            pages=[DeckPage(page_id=PAGE_ID), DeckPage(page_id="companion")],
        )
    )
    decks.upsert(
        Deck(
            id="morning",
            name="Morning",
            pages=[DeckPage(page_id=PAGE_ID)],
            advance="timer",
            advance_trigger="daily",
            advance_fires_at="07:00",
            advance_days_of_week=[0, 1, 2, 3, 4],
        )
    )
    browser, page, errors = _open(pw, base)
    try:
        if is_new:
            page.set_viewport_size({"width": 390, "height": 1000})
            page.goto(f"{base}/pages/{PAGE_ID}?new=1#lineup", wait_until="domcontentloaded")
            page.wait_for_function("() => EV2.steps.current() === 'lineup'")
        else:
            page.locator('[data-ev2-toggle="schedules"]').click()
        joined = page.locator("[data-ev2-joined]")
        assert joined.locator("[data-ev2-lrow]").count() == 2
        assert "Schedule · Weekdays at " in joined.locator('[data-ev2-lrow="morning"]').inner_text()
        assert "Lineup · by hand" in joined.locator('[data-ev2-lrow="rotation"]').inner_text()
        assert (
            joined.locator('[data-ev2-lrow="rotation"] a').get_attribute("href")
            == "/decks/rotation/edit"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_role("button", name="New scheduled lineup", exact=True).click()
        dialog = page.locator("#schedule-dialog-new")
        dialog.locator('[name="name"]').fill("Evening schedule")
        with page.expect_navigation():
            dialog.locator('button[type="submit"]').click()
        target = f"/pages/{PAGE_ID}{'?new=1#lineup' if is_new else '#schedules'}"
        assert page.url.endswith(target)
        page.wait_for_function(
            "key => EV2.steps.current() === key", arg="lineup" if is_new else "schedules"
        )
        assert joined.locator("[data-ev2-lrow]").count() == 3
        assert "Evening schedule" in joined.inner_text()
        joined.locator('[data-open-schedule-dialog="schedule-dialog-morning"]').click()
        assert page.locator("#schedule-dialog-morning").is_visible()
        page.locator("#schedule-dialog-morning [data-schedule-dialog-close]").first.click()
        messages = []

        def reject(d):
            messages.append(d.message)
            d.dismiss()

        page.once("dialog", reject)
        joined.locator('[data-ev2-lremove="morning"]').click()
        assert decks.get("morning") is not None
        assert "delete" in messages[0].lower() and "schedule" in messages[0].lower()
        page.once("dialog", lambda d: d.accept())
        with page.expect_navigation():
            joined.locator('[data-ev2-lremove="morning"]').click()
        assert joined.locator('[data-ev2-lrow="morning"]').count() == 0
        assert decks.get("morning") is None
        assert app.config["SCHEDULE_STORE"].get("morning") is None
        assert decks.get("rotation") is not None
        assert not errors
    finally:
        browser.close()


def test_replace_stops_when_the_pending_save_fails(server: tuple[str, Flask], pw: Any) -> None:
    """A failed autosave must not be papered over by Replace, a reload or "Saved"."""
    base, app = server
    browser, page, errors = _open(pw, base)
    failing = {"on": True, "hits": 0}

    def intercept(route: Any) -> None:
        if failing["on"] and route.request.method == "POST":
            failing["hits"] += 1
            time.sleep(0.4)  # still running when Replace asks to flush
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"ok": false, "message": "boom"}',
            )
        else:
            route.continue_()

    page.route(f"**/pages/{PAGE_ID}/cells/c1", intercept)
    page.evaluate("() => { window.__same_page = true; }")
    page.locator('.ev2-cl[data-cell-id="c1"] [data-ev2-row]').click()
    label = page.locator("#ev2-c1-opt-label")
    label.fill("Trip")
    # Replace blurs the box, which starts the (failing) save, then picks Hourly.
    page.locator('.ev2-cl[data-cell-id="c1"] [data-ev2-replace]').click()
    page.locator("[data-ev2-pk-q]").fill("weather_hourly")
    page.locator('[data-ev2-pk] [data-widget="weather_hourly"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-ev2-status]').dataset.state === 'error'"
    )
    page.wait_for_timeout(1500)  # a reload, if one were coming, would have happened
    assert page.evaluate("() => window.__same_page === true")  # no reload
    assert page.locator("[data-ev2-status]").get_attribute("data-state") == "error"
    cell = _cell(app, PAGE_ID, "c1")
    assert cell.plugin == "weather_now"
    assert cell.options.get("label") == "Home"
    assert label.input_value() == "Trip"  # the edit is still in the form
    assert page.locator('[data-ev2-pk] [data-widget="weather_hourly"]').is_enabled()

    # Retry once the server is back: the label and the swap both land.
    failing["on"] = False
    page.locator('[data-ev2-pk] [data-widget="weather_hourly"]').click()
    _wait_for(
        lambda: _cell(app, PAGE_ID, "c1").plugin == "weather_hourly",
        "the retry to save",
        page=page,
    )
    assert _cell(app, PAGE_ID, "c1").options.get("label") == "Trip"
    assert not [e for e in errors if "boom" in e]
    browser.close()


# ---------------------------------------------------------------------------
# Phones: real touch emulation (iPhone 15, Pixel 7, a 360px Android)
# ---------------------------------------------------------------------------

PHONES = [
    ("iPhone 15", 393, 852),
    ("Pixel 7", 412, 915),
    ("Pixel 7", 360, 800),
]


@pytest.fixture
def phone_server(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
    """A new dashboard, an edited one and a grey display, with a lineup."""
    from werkzeug.serving import make_server

    from app import device_service
    from app.state.deck_model import Deck, DeckPage

    app: Flask = create_app(
        testing=False,
        data_root=tmp_path / "data",
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    settings = app.config["SETTINGS_STORE"]
    settings.patch_section("app", {"onboarded": True})
    settings.patch_section("auth", {"disabled": True})
    for iid, kind in (("hall", "esp32_client"), ("greyish", "visionect_place_and_play_32")):
        device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DEVICE_DATA_ROOT"],
            instance_id=iid,
            kind_id=kind,
            name=iid.title(),
            orientation="landscape" if iid == "hall" else "portrait",
        )
    store = app.config["PAGE_STORE"]
    store.save(
        Page(
            id="mnew",
            name="New one",
            cells=[Cell(id="a", x=0, y=0, w=400, h=480), Cell(id="b", x=400, y=0, w=400, h=480)],
        )
    )
    store.save(
        Page(
            id="medit",
            name="Edited",
            panel=Panel(w=800, h=480),
            cells=[
                Cell(id="c1", plugin="weather_now", x=0, y=0, w=400, h=480),
                Cell(id="c2", plugin="clock_word", x=400, y=0, w=400, h=480),
            ],
        )
    )
    store.save(
        Page(
            id="mgrey",
            name="Grey",
            device_ids=["greyish"],
            cells=[Cell(id="g1", plugin="clock_word", x=0, y=0, w=400, h=480)],
        )
    )
    app.config["DECK_STORE"].upsert(
        Deck(
            id="rot",
            name="Morning rotation",
            device_ids=["hall"],
            pages=[DeckPage(page_id="medit")],
        )
    )
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}", app
    finally:
        srv.shutdown()
        thread.join(timeout=10)


def _phone(
    pw: Any, name: str, width: int, height: int, scheme: str = "light"
) -> tuple[Any, Any, list[str]]:
    browser = pw.chromium.launch(executable_path=_EXE) if _EXE else pw.chromium.launch()
    profile = {k: v for k, v in pw.devices[name].items() if k != "screen"}
    profile["viewport"] = {"width": width, "height": height}
    context = browser.new_context(color_scheme=scheme, **profile)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


def _no_sideways_scroll(page: Any) -> None:
    sizes = page.evaluate(
        "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    assert sizes[0] <= sizes[1], f"page scrolls sideways: {sizes}"


_SMALL_TARGETS = """() => {
  const out = [];
  const shell = '.brand, .page-footer, footer, nav, [data-shell], header.topbar';
  document.querySelectorAll('button, a[href], input, select, textarea, summary').forEach((e) => {
    if (e.closest(shell) || e.closest('.ev2-sr, dialog')) return;
    if (['hidden', 'checkbox', 'radio'].includes(e.type)) return;
    for (let a = e; a; a = a.parentElement) {
      if (a.hidden || getComputedStyle(a).display === 'none' || getComputedStyle(a).visibility === 'hidden') return;
    }
    const r = e.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    if (r.width < 43.5 || r.height < 43.5)
      out.push((e.className || e.tagName) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.tagName) && !['range', 'color'].includes(e.type)
        && parseFloat(getComputedStyle(e).fontSize) < 16)
      out.push('font<16: ' + (e.id || e.name));
  });
  return out;
}"""


_INPUT_FONTS = """(scope) => {
  const els = [...document.querySelectorAll(scope + ' :is(input, select, textarea)')].filter((e) =>
    !['hidden', 'checkbox', 'radio', 'range', 'color'].includes(e.type) && e.getClientRects().length);
  return {
    n: els.length,
    small: els.filter((e) => parseFloat(getComputedStyle(e).fontSize) < 16)
      .map((e) => (e.id || e.name || e.className) + ' ' + getComputedStyle(e).fontSize),
  };
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize(("name", "width", "height"), PHONES)
def test_phone_layout_has_no_sideways_scroll_and_44px_targets(
    phone_server: tuple[str, Flask], pw: Any, name: str, width: int, height: int, scheme: str
) -> None:
    base, _app = phone_server
    browser, page, errors = _phone(pw, name, width, height, scheme)
    for url in ("/pages/medit", "/pages/mnew?new=1"):
        page.goto(base + url, wait_until="domcontentloaded")
        page.wait_for_function("() => window.EV2 && window.EV2Preview")
        _no_sideways_scroll(page)
        if "new=1" in url:
            page.locator("[data-ev2-size]").first.scroll_into_view_if_needed()
        small = page.evaluate(_SMALL_TARGETS)
        assert not small, f"{url}: {small}"
    # An open row (its fields come from the shared form macros, which Paper
    # styles at 14px) and the catalog search: 16px type, so iOS doesn't zoom.
    page.goto(base + "/pages/medit", wait_until="domcontentloaded")
    page.wait_for_function("() => window.EV2 && window.EV2Preview")
    page.locator('.ev2-cl[data-cell-id="c1"] [data-ev2-row]').tap()
    page.evaluate(
        "() => document.querySelectorAll('.ev2-cl.open details:not([hidden])')"
        ".forEach((d) => (d.open = true))"
    )
    fonts = page.evaluate(_INPUT_FONTS, ".ev2-cl.open")
    assert fonts["n"] > 3 and not fonts["small"], fonts
    assert page.evaluate(_SMALL_TARGETS) == [], "open row"
    page.locator('.ev2-cl[data-cell-id="c1"] [data-ev2-replace]').tap()
    fonts = page.evaluate(_INPUT_FONTS, "[data-ev2-pk]")
    assert fonts["n"] == 1 and not fonts["small"], fonts
    assert not errors
    browser.close()


def test_phone_new_dashboard_by_touch(phone_server: tuple[str, Flask], pw: Any) -> None:
    base, _app = phone_server
    browser, page, errors = _phone(pw, "iPhone 15", 393, 852)
    page.goto(f"{base}/pages/mnew?new=1", wait_until="domcontentloaded")
    page.wait_for_function("() => window.EV2 && window.EV2Preview")
    # Display: tap a card, a size, and type a custom size with the numeric keypad.
    page.locator('[data-ev2-dev="hall"]').tap()
    page.wait_for_function("() => document.querySelector('#ev2').dataset.usingTest === '0'")
    page.locator('[data-ev2-dev=""]').tap()
    page.wait_for_function("() => document.querySelector('#ev2').dataset.usingTest === '1'")
    width_box = page.locator("#ev2-tw")
    assert width_box.get_attribute("inputmode") == "numeric"
    assert page.locator('[data-ev2-size="1920x1080"]').count() == 1
    # Next: lands focus on the step, and the layout step opens.
    page.locator("[data-ev2-next=display]").tap()
    assert page.locator("#ev2-step-layout").get_attribute("data-state") == "open"
    # A layout preset.
    page.locator("[data-ev2-layout='2x2_grid']").tap()
    page.wait_for_function("() => document.querySelectorAll('.ev2-cl[data-n]').length === 4")
    # Edit on preview: scrolls up to the preview; the preview carries Done drawing.
    page.wait_for_function("() => window.EV2Preview && document.querySelector('.ev2pv-cell')")
    page.locator("[data-ev2-draw]").scroll_into_view_if_needed()
    page.locator("[data-ev2-draw]").tap()
    page.wait_for_function(
        "() => document.getElementById('ev2-preview').getBoundingClientRect().top < 300"
    )
    assert page.locator("[data-ev2pv-done]").is_visible()
    # Split a cell by touch; edit mode survives the reload.
    page.locator(".ev2pv-cell").first.tap()
    page.locator("[data-ev2pv-tool=split-v]").tap()
    page.wait_for_function("() => document.querySelectorAll('.ev2-cl[data-n]').length === 5")
    page.wait_for_function("() => document.querySelector('.ev2pv-overlay.is-edit')")
    # Remove one again.
    page.locator(".ev2pv-cell").first.tap()
    page.once("dialog", lambda d: d.accept())
    page.locator("[data-ev2pv-tool=remove]").tap()
    page.wait_for_function("() => document.querySelectorAll('.ev2-cl[data-n]').length === 4")
    page.locator("[data-ev2pv-done]").tap()
    page.wait_for_function(
        "() => document.querySelector('[data-ev2-draw]').getAttribute('aria-pressed') === 'false'"
    )
    # Widgets: an empty row opens the catalog, which fills the phone.
    page.locator("[data-ev2-next=layout]").scroll_into_view_if_needed()
    page.locator("[data-ev2-next=layout]").tap()
    page.wait_for_function(
        "() => document.querySelector('[data-ev2-pk]') && !document.querySelector('[data-ev2-pk]').hidden"
    )
    box = page.locator("[data-ev2-pk]").bounding_box()
    assert box and box["width"] >= 392 and box["height"] >= 800  # full screen
    chips = page.locator(".ev2-pk-cats")
    assert chips.evaluate("e => getComputedStyle(e).display") == "flex"  # chips, not a side rail
    page.locator("[data-ev2-pk-q]").tap()
    page.keyboard.type("word")
    page.locator('[data-ev2-pk] [data-widget="clock_word"]').tap()
    page.wait_for_function("() => document.querySelector('.ev2-cl[data-widget=\"clock_word\"]')")
    # Look and Lineup by touch.
    page.locator("[data-ev2-next=widgets]").scroll_into_view_if_needed()
    page.locator("[data-ev2-next=widgets]").tap()
    assert page.locator("#ev2-step-look").get_attribute("data-state") == "open"
    page.locator("[data-ev2-next=look]").scroll_into_view_if_needed()
    page.locator("[data-ev2-next=look]").tap()
    page.locator("[data-ev2-lopen]").tap()
    row = page.locator("[data-ev2-ladd]").first
    row.scroll_into_view_if_needed()
    row.tap()
    assert page.locator("[data-ev2-lconfirm]").is_visible()
    page.locator("[data-ev2-lcancel]").tap()
    # Done leaves for the finished editor.
    page.locator("[data-ev2-finish]").tap()
    page.wait_for_function("() => !location.search.includes('new=1')")
    assert not errors
    browser.close()


def test_phone_edge_drag_does_not_scroll_the_page(phone_server: tuple[str, Flask], pw: Any) -> None:
    base, app = phone_server
    browser, page, errors = _phone(pw, "Pixel 7", 412, 915)
    page.goto(f"{base}/pages/medit", wait_until="domcontentloaded")
    page.wait_for_function("() => window.EV2Preview && document.querySelector('.ev2pv-edge')")
    edge = page.locator(".ev2pv-edge--v")
    edge.scroll_into_view_if_needed()
    page.evaluate("() => window.scrollBy(0, 0)")
    lb = edge.bounding_box()
    ob = page.locator(".ev2pv-overlay").bounding_box()
    assert lb and ob
    x0, y = lb["x"] + lb["width"] / 2, lb["y"] + lb["height"] / 2
    x1 = ob["x"] + ob["width"] * 0.25
    scroll_before = page.evaluate("() => window.scrollY")
    cdp = page.context.new_cdp_session(page)

    def touch(kind: str, x: float, yy: float) -> None:
        points = [] if kind == "touchEnd" else [{"x": x, "y": yy}]
        cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})

    touch("touchStart", x0, y)
    for k in range(1, 11):
        touch("touchMove", x0 + (x1 - x0) * k / 10, y + 6 * k)  # a little vertical wobble
    touch("touchEnd", x1, y)
    _wait_for(lambda: _cell(app, "medit", "c1").w < 400, "the touch drag to save", page=page)
    assert abs(page.evaluate("() => window.scrollY") - scroll_before) < 3  # the page did not scroll
    assert not errors
    browser.close()


def test_phone_edit_mode_widgets_info_and_bar(phone_server: tuple[str, Flask], pw: Any) -> None:
    base, _app = phone_server
    browser, page, errors = _phone(pw, "iPhone 15", 393, 852)
    page.goto(f"{base}/pages/medit", wait_until="domcontentloaded")
    page.wait_for_function("() => window.EV2 && window.EV2Preview")
    # The tooltip opens by tap, and its button is reachable.
    page.locator("[data-ev2-info]").tap()
    assert page.locator("#ev2-info-pop").is_visible()
    assert page.locator("#ev2-info-pop button").bounding_box()["height"] >= 43.5
    page.mouse.click(20, 500)
    # A row expands, Replace opens the catalog, Look opens and its slider works.
    page.locator('.ev2-cl[data-cell-id="c1"] [data-ev2-row]').tap()
    assert page.locator('.ev2-cl[data-cell-id="c1"]').get_attribute("class").find("open") >= 0
    page.locator('.ev2-cl[data-cell-id="c1"] [data-ev2-replace]').tap()
    assert page.locator("[data-ev2-pk]").is_visible()
    page.locator("[data-ev2-pk-keep]").tap()
    # The bottom bar clears the home indicator and stays below the content it follows.
    bar = page.locator(".ev2-bar").bounding_box()
    assert bar and bar["y"] + bar["height"] <= 852
    # While a text field has the keyboard the bar steps aside.
    page.locator("[data-ev2-name]").tap()
    assert not page.locator(".ev2-bar").is_visible()
    page.locator("body").tap(position={"x": 5, "y": 5})
    assert not errors
    browser.close()


def test_grey_display_preview_is_greyed_like_the_classic_editor(
    phone_server: tuple[str, Flask], pw: Any
) -> None:
    base, _app = phone_server
    browser, page, errors = _phone(pw, "iPhone 15", 393, 852)
    page.goto(f"{base}/pages/mgrey", wait_until="domcontentloaded")
    page.wait_for_function("() => window.EV2Preview && document.querySelector('.ev2pv-canvas')")
    html = page.content()
    assert 'data-gamut="grey16" data-grey-levels="16"' in html
    canvas = page.locator(".ev2pv-canvas")
    assert canvas.get_attribute("data-gamut") == "grey16"
    assert "grayscale" in page.locator(".ev2pv-iframe").evaluate("e => getComputedStyle(e).filter")
    assert not errors
    browser.close()


def test_phone_swap_widgets_with_the_move_buttons(phone_server: tuple[str, Flask], pw: Any) -> None:
    base, app = phone_server
    browser, page, errors = _phone(pw, "iPhone 15", 393, 852)
    page.goto(f"{base}/pages/medit", wait_until="domcontentloaded")
    page.wait_for_function("() => window.EV2 && window.EV2Preview")
    page.locator('.ev2-cl[data-cell-id="c1"] [data-ev2-row]').tap()
    button = page.locator('.ev2-cl[data-cell-id="c1"] [data-ev2-move="down"]')
    button.scroll_into_view_if_needed()
    box = button.bounding_box()
    assert box and box["width"] >= 43.5 and box["height"] >= 43.5
    button.tap()
    _wait_for(
        lambda: _cell(app, "medit", "c1").plugin == "clock_word", "the swap to save", page=page
    )
    assert _cell(app, "medit", "c2").plugin == "weather_now"
    assert not errors
    browser.close()
