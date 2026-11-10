"""The lineup editor in a live Chromium, against the real routes: the header's
slice of the day, 1 When it runs (an end time turns a push into a rotation),
2 Dashboards (Alt+Arrow reorder, a gallery's items and their searchable grid,
add and remove, Discard), 3 Timing (options only where the display has them;
Smart sync switches off), the new-lineup flow, and a phone-width page."""

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


def _launch(p: Any) -> Any:
    return p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()


_HAS_CHROMIUM = _chromium_available()
if not _HAS_CHROMIUM and os.environ.get("TESSERAE_REQUIRE_BROWSER_TESTS") == "1":
    raise RuntimeError(
        "Playwright Chromium is required; run playwright install --with-deps chromium"
    )
pytestmark = pytest.mark.skipif(not _HAS_CHROMIUM, reason="Playwright Chromium not installed")

ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]


@pytest.fixture
def real(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
    from PIL import Image
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
    c = app.test_client()
    for dev, kind in (("hall", "esp32_client"), ("tv", "opendisplay_ha")):
        code = app.config["PAIRING_STORE"].issue(note="t").code
        resp = c.post(
            "/api/v1/device/register",
            headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
            data=json.dumps({"device_id": dev, "kind": kind}),
        )
        assert resp.status_code == 201
    # A TV that takes pushes: no buttons, never sleeps.
    app.config["DEVICE_REGISTRY"].get("tv").manifest["buttons"] = False
    folder = tmp_path / "data" / "plugins" / "picture_gallery" / "trip"
    folder.mkdir(parents=True)
    for i in range(30):
        Image.new("RGB", (32, 20), (i * 8, 90, 120)).save(folder / f"IMG_{i:03d}.jpg")
    Image.new("RGB", (32, 20), (200, 180, 90)).save(folder / "zz_beach.jpg")
    pages = app.config["PAGE_STORE"]
    gallery = Cell(
        id="g", plugin="picture_gallery", x=0, y=0, w=10, h=10, options={"folder": "trip"}
    )
    pages.save(
        Page(id="photos", name="Photos", device_ids=["tv"], refresh_minutes=30, cells=[gallery])
    )
    for pid, name, dev in (
        ("clock", "Clock", "tv"),
        ("climate", "Climate", "tv"),
        ("weather", "Weather", "hall"),
        ("agenda", "Agenda", "hall"),
    ):
        pages.save(Page(id=pid, name=name, device_ids=[dev]))
    decks = app.config["DECK_STORE"]
    decks.upsert(
        Deck(
            id="art", name="Art", device_ids=["tv"], advance="timer", advance_trigger="daily",
            advance_fires_at="06:00",
            pages=[DeckPage(page_id="photos"), DeckPage(page_id="clock"), DeckPage(page_id="climate")],
        )
    )  # fmt: skip
    decks.upsert(
        Deck(
            id="morning", name="Morning", device_ids=["hall"], advance="both",
            advance_trigger="cycle", advance_anchor="00:00", advance_smart_sync=True,
            pages=[DeckPage(page_id="weather"), DeckPage(page_id="agenda")],
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


def _open(p: Any, base: str, path: str, width: int = 1280) -> tuple[Any, Any, list[str]]:
    browser = _launch(p)
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + path, wait_until="load")
    return browser, page, errors


def _settle(page: Any) -> None:
    """Let the thumbnails finish (closing mid-response leaves a file handle open)."""
    page.wait_for_function("() => [...document.images].every(i => i.complete)")


def _save(page: Any) -> None:
    with page.expect_navigation():
        page.locator("[data-save]").click()
    _settle(page)


def _steps(page: Any) -> list[str]:
    return [r.get_attribute("data-step") for r in page.locator("[data-step]").all()]


def test_header_shows_this_lineups_slice_of_the_day(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/morning/edit")
        assert page.locator("#dxe-crumb-here").inner_text() == "hall"
        assert page.locator("#dxe-title").input_value() == "Morning"
        # The Lineups page's own bar, this lineup in the accent.
        assert page.locator(".dxe-daybar .lu-trk .lu-blk.is-mine").count() >= 1
        assert page.locator(".dxe-daybar .lu-scale").count() == 1
        assert page.locator(".dxe-live-line").count() == 1
        # Turning it off says so, and is an unsaved change.
        page.locator("#dxe-enabled").click()
        assert "Off" in page.locator(".dxe-live-line").inner_text()
        assert page.locator("#dxe-save-state").inner_text() == "Unsaved changes"
        _settle(page)
        assert errors == []
        browser.close()


def test_the_day_bar_drops_a_lineup_switched_off_or_set_to_by_hand(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    mine = ".dxe-daybar .lu-trk .lu-blk.is-mine"
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/morning/edit")
        assert page.locator(mine).count() >= 1
        page.locator("#dxe-enabled").click()
        assert page.locator(mine).count() == 0
        page.locator("#dxe-enabled").click()
        assert page.locator(mine).count() >= 1
        page.locator('[data-when="manual"]').click()
        assert page.locator(mine).count() == 0
        page.locator('[data-when="between"]').click()
        assert page.locator(mine).count() >= 1
        _settle(page)
        assert errors == []
        browser.close()


def test_a_redraw_from_an_item_list_keeps_the_raw_conditions_open(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    decks = app.config["DECK_STORE"]
    art = decks.get("art")
    sun = {"source_kind": "sun", "operator": "is_day", "value": {}}
    decks.upsert(
        art.model_copy(
            update={
                "pages": [
                    DeckPage.model_validate({"page_id": "photos", "conditions": [sun]}),
                    *art.pages[1:],
                ]
            }
        )
    )
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/art/edit")
        held: list[Any] = []
        page.route("**/decks/dashboard-items?**", lambda route: held.append(route))
        page.locator('[data-expand="photos"]').click()
        page.locator('[data-edit="photos"]').click()
        picker = page.locator('[data-panel="photos"] [data-condition-picker]')
        picker.locator("summary").click()
        raw = picker.locator("textarea")
        raw.focus()
        assert raw.is_visible() and _fk(page) == "cond-photos"
        assert held, "the item list should still be on its way"
        held[0].continue_()  # its response redraws the editor
        page.wait_for_function("() => document.querySelector('[data-panel=photos] .dxe-strip img')")
        page.wait_for_timeout(100)  # the pickers start just after the redraw
        assert page.locator('[data-cond="photos"]').is_visible()
        assert _fk(page) == "cond-photos"
        _settle(page)
        assert errors == []
        browser.close()


def test_a_rotation_cut_to_one_dashboard_carries_its_cadence(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    decks = app.config["DECK_STORE"]
    morning = decks.get("morning")
    decks.upsert(
        morning.model_copy(
            update={
                "pages": [
                    morning.pages[0].model_copy(update={"dwell_minutes": 5}),
                    morning.pages[1],
                ]
            }
        )
    )
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/morning/edit")
        page.locator('[data-expand="weather"]').click()
        page.locator('[data-edit="weather"]').click()
        assert page.locator('[data-dwell="weather"]').count() == 1  # a cycle: its own time
        page.locator('[data-expand="agenda"]').click()
        page.locator('[data-remove="agenda"]').click()
        # Now an interval: its one cadence is what the dashboard showed for.
        assert "Sent again every 5 min" in page.locator("#dxe-sections").inner_text()
        assert page.locator("[data-dwell]").count() == 0
        _save(page)
        d = decks.get("morning")
        assert (d.advance_trigger, d.advance_interval_minutes) == ("interval", 5)
        assert [x.dwell_minutes for x in d.pages] == [None]
        assert errors == []
        browser.close()


def test_when_an_end_time_turns_a_push_into_a_rotation(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/art/edit")
        lead = page.locator("#dxe-h-when + .dxe-lead")
        assert lead.inner_text().startswith("Sends at ")
        warn = page.locator(".dxe-warn")
        assert warn.count() == 1 and "Only dashboard 1 is ever sent." in warn.inner_text()
        assert page.locator(".dxe-step.is-muted").count() == 2  # not sent by a push
        warn.locator("[data-end=add]").click()
        assert page.locator(".dxe-warn").count() == 0
        page.locator('[data-time="end"]').fill("16:30")
        page.locator('[data-time="end"]').dispatch_event("change")
        assert lead.inner_text().startswith("Runs ")
        # A day off, with the Lineups tiles' mark.
        page.locator('.lu-dt[data-day="6"]').click()
        assert page.locator('.lu-dt[data-day="6"]').get_attribute("aria-pressed") == "false"
        assert "is-edited" in page.locator('.lu-dt[data-day="6"]').get_attribute("class")
        _save(page)
        d = app.config["DECK_STORE"].get("art")
        assert (d.advance_trigger, d.advance_anchor, d.advance_end_at) == (
            "cycle",
            "06:00",
            "16:30",
        )
        assert d.advance_days_of_week == [0, 1, 2, 3, 4, 5]
        assert page.locator("[data-flash]").first.inner_text().startswith("Saved Art")
        assert page.locator("#dxe-save-state").inner_text() == "All changes saved"
        # And back: x removes the end, a push at the start again.
        page.locator("[data-end=rm]").click()
        _save(page)
        d = app.config["DECK_STORE"].get("art")
        assert (d.advance_trigger, d.advance_fires_at) == ("daily", "06:00")
        assert errors == []
        browser.close()


def test_dashboards_reorder_items_add_remove_and_discard(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/art/edit")
        assert _steps(page) == ["photos", "clock", "climate"]
        row = page.locator('[data-step="photos"]')
        assert row.locator(".lu-count").inner_text() == "×31"
        assert row.locator(".lu-tile.stacked").count() == 1
        # Alt+Down moves it; the live region says where it went.
        row.locator("[data-grip]").focus()
        page.keyboard.press("Alt+ArrowDown")
        assert _steps(page) == ["clock", "photos", "climate"]
        assert page.locator("[data-step]:focus-within").get_attribute("data-step") == "photos"
        # Its items: the first few, then every one in a searchable, paged grid.
        page.locator('[data-expand="photos"]').click()
        head = page.locator('[data-panel="photos"] .dxe-detail-head')
        assert "Gallery · 31 photos" in head.inner_text()
        strip = page.locator('[data-panel="photos"] .dxe-strip .dxe-it')
        strip.first.wait_for()
        assert strip.count() == 5
        page.locator('[data-grid="photos"]').click()
        grid = page.locator('[data-grid-for="photos"] .dxe-grid-items .dxe-it')
        grid.first.wait_for()
        assert grid.count() == 18
        assert page.locator(".dxe-range").text_content() == "1–18 of 31"
        page.locator('[data-gpage="2"]').first.click()
        page.wait_for_function(
            "() => document.querySelector('.dxe-range')?.textContent === '19–31 of 31'"
        )
        page.locator('[data-grid-q="photos"]').fill("beach")
        page.wait_for_function(
            "() => document.querySelector('.dxe-range')?.textContent === '1–1 of 1'"
        )
        assert "zz_beach.jpg" in page.locator('[data-grid-for="photos"]').inner_text()
        # Remove one, add one from the picker, then Discard puts it all back.
        page.locator('[data-remove="photos"]').click()
        page.locator("[data-picker]").click()
        assert page.locator('#dxe-picker [data-add="photos"]').count() == 1
        page.locator('#dxe-picker [data-add="photos"]').click()
        assert _steps(page) == ["clock", "climate", "photos"]
        page.locator("[data-discard]").click()
        assert _steps(page) == ["photos", "clock", "climate"]
        assert page.locator("#dxe-save-state").inner_text() == "All changes saved"
        # A move that is saved persists.
        page.locator('[data-step="climate"] [data-grip]').focus()
        page.keyboard.press("Alt+ArrowUp")
        _save(page)
        assert app.config["DECK_STORE"].get("art").page_ids == ["photos", "climate", "clock"]
        assert errors == []
        browser.close()


def test_pace_shows_only_the_options_a_display_has(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/art/edit")
        # A push-only TV: one line, no options.
        assert "always on, no buttons" in page.locator(".dxe-opts-none").inner_text()
        assert page.locator('[data-fold="options"]').count() == 0
        assert "Only for a window." in page.locator(".dxe-pace").inner_text()
        _settle(page)
        page.goto(base + "/decks/morning/edit", wait_until="load")
        page.locator('[data-fold="options"] summary').click()
        opts = page.locator('[data-fold="options"]').inner_text()
        assert "Taps also flip" in opts and "Send just before it wakes" in opts
        assert page.locator("[data-taps]").is_checked()
        page.locator("[data-smart]").click()
        assert not page.locator("[data-smart]").is_checked()
        _save(page)
        d = app.config["DECK_STORE"].get("morning")
        assert d.advance_smart_sync is False and d.advance == "both"
        assert errors == []
        browser.close()


def test_new_lineup_steps_and_creates(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    before = {d.id for d in app.config["DECK_STORE"].all()}
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/new")
        create = page.locator("[data-create]")
        assert create.is_disabled()
        assert page.locator("#dxe-save-state").inner_text() == "Step 1 of 3"
        page.locator('[data-pick-display="tv"]').click()
        assert page.locator(".dxe-done").first.inner_text().startswith("")
        assert "Display" in page.locator(".dxe-done").first.inner_text()
        page.locator('[data-when="between"]').click()
        # (Art pushes at 06:00 on the TV: a window from 07:00 stays clear of it.)
        page.locator('[data-time="start"]').fill("07:00")
        page.locator('[data-time="end"]').fill("16:30")
        page.locator('[data-time="end"]').dispatch_event("change")
        page.locator("[data-goto='3']").click()
        assert page.locator(".dxe-done").count() == 2
        assert "No dashboards yet." in page.locator(".dxe-empty").first.inner_text()
        assert create.is_disabled()
        page.locator('[data-add="photos"]').click()
        page.locator('[data-add="clock"]').click()
        assert create.is_enabled()
        page.locator("#dxe-title").fill("Day art")
        with page.expect_navigation():
            create.click()
        _settle(page)
        made = [d for d in app.config["DECK_STORE"].all() if d.id not in before]
        assert len(made) == 1
        d = made[0]
        assert page.url.endswith(f"/decks/{d.id}/edit")
        assert (d.name, d.device_ids, d.page_ids) == ("Day art", ["tv"], ["photos", "clock"])
        assert (d.advance_trigger, d.advance_anchor, d.advance_end_at) == (
            "cycle",
            "07:00",
            "16:30",
        )
        assert errors == []
        browser.close()


@pytest.mark.parametrize(
    "path", ["/decks/art/edit", "/decks/morning/edit", "/decks/new?display=hall"]
)
def test_a_phone_width_page_never_scrolls_sideways(real: tuple[str, Flask], path: str) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, path, width=420)
        for sel in ("[data-expand]", "[data-picker]"):
            if page.locator(sel).count():
                page.locator(sel).first.click()
        over = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert over <= 0, over
        # Every control is reachable by keyboard and labelled.
        unlabelled = page.evaluate(
            """() => [...document.querySelectorAll('#deck-editor button, #deck-editor input, #deck-editor select')]
                .filter((e) => !e.closest('[hidden]') && e.type !== 'hidden')
                .filter((e) => !(e.getAttribute('aria-label') || e.textContent.trim() || e.labels?.length || e.title))
                .map((e) => e.outerHTML.slice(0, 80))"""
        )
        assert unlabelled == []
        _settle(page)
        assert errors == []
        browser.close()


# -- review fixes ------------------------------------------------------------------


def _fk(page: Any) -> str | None:
    return page.evaluate("() => document.activeElement?.dataset?.fk || null")


def test_a_lineup_on_two_displays_keeps_both_and_a_stale_save_is_refused(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    app.config["PAGE_STORE"].save(Page(id="both", name="Both", device_ids=["hall", "tv"]))
    store = app.config["DECK_STORE"]
    store.upsert(
        Deck(
            id="shared", name="Shared", device_ids=["hall", "tv"], pages=[DeckPage(page_id="both")]
        )
    )
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/shared/edit")
        assert page.locator("#dxe-crumb-here").inner_text() == "hall + tv"
        assert "hall + tv" in page.locator("#dxe-loc").inner_text()
        _save(page)  # no edits
        assert store.get("shared").device_ids == ["hall", "tv"]
        # Renamed elsewhere (another tab) after this page loaded: refused.
        store.upsert(store.get("shared").model_copy(update={"name": "Theirs"}))
        page.locator("#dxe-title").fill("Mine")
        page.locator("[data-save]").click()
        state = page.locator("#dxe-save-state")
        page.wait_for_function(
            "() => document.querySelector('#dxe-save-state').textContent.startsWith('Changed')"
        )
        assert state.inner_text() == "Changed elsewhere; reload to see it."
        assert page.locator("[data-reload]").is_visible()
        assert store.get("shared").name == "Theirs"
        # Deleted elsewhere: refused, not made again.
        store.delete("shared")
        page.locator("[data-save]").click()
        page.wait_for_function(
            "() => document.querySelector('#dxe-save-state').textContent.includes('deleted')"
        )
        assert store.get("shared") is None
        _settle(page)
        assert errors == []
        browser.close()


def test_keyboard_focus_stays_put_through_a_redraw(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/morning/edit")
        page.locator('[data-when="at"]').focus()
        page.keyboard.press("Enter")
        assert _fk(page) == "when-at"
        page.locator('[data-when="between"]').focus()
        page.keyboard.press("Enter")
        assert _fk(page) == "when-between"
        page.locator('[data-expand="agenda"]').click()
        page.locator('[data-home="agenda"]').focus()
        page.keyboard.press("Enter")
        assert _fk(page) == "home-agenda"
        assert page.locator('[data-home="agenda"]').inner_text() == "★ Home"
        # 12:00 AM as an end is midnight, not a window that ends before it starts.
        page.locator('[data-time="end"]').fill("00:00")
        page.locator('[data-time="end"]').dispatch_event("change")
        assert page.locator("#dxe-end-err").count() == 0
        # Discard puts the On word back with the switch.
        page.locator("#dxe-enabled").click()
        assert page.locator("#dxe-on-word").inner_text() == "Off"
        page.locator("[data-discard]").click()
        assert page.locator("#dxe-on-word").inner_text() == "On"
        page.locator('[data-when="between"]').click()  # discard put it back to between
        page.locator('[data-time="end"]').fill("00:00")
        page.locator('[data-time="end"]').dispatch_event("change")
        _save(page)
        d = app.config["DECK_STORE"].get("morning")
        assert (d.advance_trigger, d.advance_anchor, d.advance_end_at) == ("cycle", "00:00", None)
        assert errors == []
        browser.close()


def test_quiet_hours_say_what_really_happens(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {
            "locale": "en-US",  # 12-hour times, whatever the host's locale is
            "quiet_hours_enabled": True,
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "08:08",
        },
    )
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/art/edit")
        # A daily push in quiet hours is skipped, and the header says so.
        assert page.locator(".dxe-live-line").inner_text() == "Skipped: 6 AM is in quiet hours."
        # + End time doesn't make a window that is all quiet hours.
        page.locator('.dxe-time [data-end="add"]').click()
        assert page.locator('[data-time="end"]').input_value() == "09:45"
        page.locator('[data-time="end"]').fill("07:00")
        page.locator('[data-time="end"]').dispatch_event("change")
        assert "All of this is in quiet hours." in page.locator(".dxe-warn").inner_text()
        _settle(page)
        # A new lineup starts after quiet hours; an end before the start is said
        # at once, and Continue waits for it.
        page.goto(base + "/decks/new?display=hall", wait_until="load")
        assert page.locator('[data-time="start"]').input_value() == "08:15"
        page.locator('[data-when="between"]').click()
        page.locator('[data-time="end"]').fill("08:00")
        page.locator('[data-time="end"]').dispatch_event("change")
        assert page.locator("#dxe-end-err").inner_text() == "End must be after start."
        page.locator("[data-goto='3']").click(force=True)  # aria-disabled: still focusable
        assert page.locator(".dxe-done").count() == 1
        assert page.locator("#dxe-save-state").inner_text() == "End must be after start."
        # Started from a display's +: its display can't be changed.
        assert page.locator(".dxe-done [data-goto='1']").count() == 0
        _settle(page)
        assert errors == []
        browser.close()


def test_a_save_in_flight_a_cancelled_drag_and_a_double_push(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/art/edit")
        # Esc (dropEffect none) puts the rows back; a real drop keeps the move.
        drag = """(effect) => {
            const rows = [...document.querySelectorAll('[data-step]')];
            const dt = new DataTransfer();
            rows[0].dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
            const r = rows[2].getBoundingClientRect();
            rows[2].dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true,
                dataTransfer: dt, clientX: r.right - 1, clientY: r.top + 5}));
            dt.dropEffect = effect;
            rows[0].dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: dt}));
        }"""
        page.evaluate(drag, "none")
        assert _steps(page) == ["photos", "clock", "climate"]
        assert page.locator("#dxe-save-state").inner_text() == "All changes saved"
        climate = page.locator('[data-step="climate"]')
        climate.evaluate("(e) => e.scrollIntoView({ block: 'center' })")
        box = climate.bounding_box()
        assert box is not None
        page.locator('[data-step="photos"] [data-grip]').drag_to(
            climate, target_position={"x": box["width"] - 30, "y": 40}
        )
        assert _steps(page) == ["clock", "climate", "photos"]
        assert page.locator("#dxe-save-state").inner_text() == "Unsaved changes"
        page.locator("[data-discard]").click()
        # Push now (in the open dashboard's details): one request however fast it is clicked.
        page.locator('[data-step="photos"] .dxe-sel').click()
        pushes: list[Any] = []
        page.route("**/push-step", lambda route: pushes.append(route))
        page.locator('[data-push="photos"]').click()
        page.locator('[data-push="photos"]').click(force=True)
        page.wait_for_timeout(200)
        assert len(pushes) == 1
        assert page.locator('[data-push="photos"]').is_disabled()
        pushes[0].fulfill(json={"ok": True, "message": "Sent."})
        page.wait_for_function("() => !document.querySelector('[data-push=\"photos\"]').disabled")
        # Nothing can be edited while a save is on its way.
        held: list[Any] = []
        page.route("**/decks/editor-save", lambda route: held.append(route))
        page.locator("#dxe-title").fill("Before")
        page.locator("[data-save]").click()
        page.wait_for_function("() => document.getElementById('dxe-sections').inert")
        assert page.evaluate("() => document.getElementById('dxe-hero').inert")
        assert page.locator("[data-discard]").is_disabled()
        with page.expect_navigation():
            held[0].continue_()
        _settle(page)
        assert app.config["DECK_STORE"].get("art").name == "Before"
        assert errors == []
        browser.close()


def test_the_audit_hides_what_does_nothing(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    app.config["DECK_STORE"].upsert(
        Deck(id="hand", name="Hand", device_ids=["hall"], pages=[DeckPage(page_id="weather")])
    )
    with sync_playwright() as p:
        # A daily push on a TV: no Keep ready, no pre-render claim, no Mode.
        browser, page, errors = _open(p, base, "/decks/art/edit")
        adv = page.locator('[data-fold="advanced"]')
        adv.locator("summary").click()
        assert "Keep ready" not in adv.inner_text() and "Mode" not in adv.inner_text()
        assert "pre-rendered" not in page.locator(".dxe-pace").inner_text()
        page.locator('[data-expand="clock"]').click()
        page.locator('[data-edit="clock"]').click()
        assert page.locator('[data-cond="clock"]').count() == 0  # no conditions, no editor
        _settle(page)
        # By hand: no days, no timer row, and the header says how it flips.
        page.goto(base + "/decks/hand/edit", wait_until="load")
        assert page.locator(".dxe-days").count() == 0
        assert "shows for" not in page.locator(".dxe-pace").inner_text()
        assert page.locator(".dxe-live-line").inner_text() == "By hand: taps and buttons flip it."
        _settle(page)
        assert errors == []
        browser.close()


def test_the_phone_bar_shows_the_lineups_name(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/art/edit", width=420)
        assert page.locator("[data-page-bar-title]").text_content() == "Art"
        page.locator("#dxe-title").fill("Art day")
        assert page.locator("[data-page-bar-title]").text_content() == "Art day"
        _settle(page)
        assert errors == []
        browser.close()


# -- the Dashboards strip: the Lineups page's tiles, bigger ---------------------------

_TILE_GEOMETRY = """(id) => {
  const tile = document.querySelector(`[data-step="${id}"]`);
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height, r: r.right, b: r.bottom };
  };
  const sheet = getComputedStyle(tile.querySelector('.lu-tile'), '::before');
  return {
    thumb: box(tile.querySelector('.lu-thumb')),
    count: box(tile.querySelector('.lu-count')),
    stacked: tile.querySelector('.lu-tile').classList.contains('stacked'),
    sheet: { transform: sheet.transform, opacity: sheet.opacity, border: sheet.borderTopColor },
  };
}"""


def _register(app: Flask, dev: str, kind: str) -> None:
    code = app.config["PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": dev, "kind": kind}),
    )
    assert resp.status_code == 201


@pytest.mark.parametrize("width", [1280, 420])
def test_dashboards_are_big_display_shaped_tiles_that_open_their_details(
    real: tuple[str, Flask], width: int
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    # A portrait panel (Visionect 1440x2560) next to the landscape TV.
    _register(app, "frame", "visionect")
    folder = {"folder": "trip"}
    gallery = Cell(id="g", plugin="picture_gallery", x=0, y=0, w=10, h=10, options=folder)
    pages = app.config["PAGE_STORE"]
    pages.save(Page(id="tall", name="Tall photos", device_ids=["frame"], cells=[gallery]))
    long = "A very long dashboard name that never runs past its tile in the strip"
    pages.save(Page(id="note", name=long, device_ids=["frame"]))
    app.config["DECK_STORE"].upsert(
        Deck(id="hallway", name="Hallway", device_ids=["frame"], advance="timer",
             advance_trigger="cycle", advance_anchor="06:00",
             pages=[DeckPage(page_id="tall"), DeckPage(page_id="note")])
    )  # fmt: skip
    with sync_playwright() as p:
        for path, first, other, portrait in (
            ("/decks/art/edit", "photos", "clock", False),
            ("/decks/hallway/edit", "tall", "note", True),
        ):
            browser, page, errors = _open(p, base, path, width=width)
            a = page.evaluate(_TILE_GEOMETRY, first)
            b = page.evaluate(_TILE_GEOMETRY, other)
            th = a["thumb"]
            # The tile is the display's shape, and much bigger than a Lineups tile.
            if portrait:
                assert th["h"] > th["w"] and abs(th["w"] / th["h"] - 1440 / 2560) < 0.03, th
                assert th["h"] >= (140 if width < 600 else 190), th
            else:
                assert th["w"] > th["h"] and abs(th["w"] / th["h"] - 800 / 480) < 0.03, th
                assert th["w"] >= (150 if width < 600 else 200), th
            assert abs(b["thumb"]["h"] - th["h"]) < 1 and abs(b["thumb"]["y"] - th["y"]) < 1
            # Many items: a stacked card with real edges and the count on its corner.
            assert a["stacked"] and not b["stacked"]
            assert a["sheet"]["opacity"] == "1" and a["sheet"]["transform"] != "none"
            c = a["count"]
            assert page.locator(f'[data-step="{first}"] .lu-count').inner_text() == "×31"
            assert th["x"] < c["x"] < th["r"] < c["r"] <= th["r"] + 14, (c, th)
            assert th["y"] < c["y"] < th["b"] < c["b"] <= th["b"] + 14, (c, th)
            # Desktop wraps onto more lines; a phone scrolls the strip sideways, not the page.
            wrap = page.evaluate(
                "() => getComputedStyle(document.getElementById('dxe-steps')).flexWrap"
            )
            assert wrap == ("nowrap" if width < 600 else "wrap")
            assert page.evaluate(
                "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
            if portrait:
                name = page.locator(f'[data-step="{other}"] .lu-step-name')
                assert name.evaluate("(e) => e.getBoundingClientRect().height") < 44  # two lines
            # Clicking a tile opens its details under the strip, without its picture again.
            page.locator(f'[data-step="{first}"] .dxe-sel').click()
            panel = page.locator(f'[data-panel="{first}"]')
            assert page.locator(f'[data-step="{first}"]').get_attribute("class").count("is-open")
            head = panel.locator(".dxe-detail-head")
            assert "Gallery · 31 photos" in head.inner_text()
            assert head.locator(".dxe-plug i.ph-images").count() == 1
            assert panel.locator(".lu-thumb").count() == 0
            # Measure in one atomic step: the panel can be re-rendered right after the click.
            page.wait_for_function(
                """(id) => {
                  const p = document.querySelector(`[data-panel="${id}"]`);
                  const t = document.querySelector('.dxe-tiles');
                  return !!p && p.getBoundingClientRect().top >= t.getBoundingClientRect().bottom;
                }""",
                arg=first,
            )
            # One open at a time; Esc closes and goes back to the tile.
            page.locator(f'[data-step="{other}"] .lu-step-name').click()
            assert page.locator("[data-panel]").count() == 1
            assert page.locator(f'[data-panel="{other}"]').count() == 1
            page.keyboard.press("Escape")
            assert page.locator("[data-panel]").count() == 0
            assert page.evaluate("() => document.activeElement.dataset.fk") == f"sel-{other}"
            # Alt+Right moves the focused tile along the strip.
            page.keyboard.press("Alt+ArrowLeft")
            assert _steps(page)[0] == other
            _settle(page)
            assert errors == []
            browser.close()


def test_a_tiles_arrows_choose_the_item_that_push_now_sends(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    asked: list[str] = []
    pushed: list[dict[str, Any]] = []

    def item(route: Any) -> None:
        from urllib.parse import parse_qs, urlparse

        key = parse_qs(urlparse(route.request.url).query)["item"][0]
        asked.append(key)
        route.fulfill(
            json={"ok": True, "state": "ready", "item": key, "thumb_url": f"/_img/{key}.png",
                  "large_url": f"/_img/{key}.png?lg=1", "sig": key}
        )  # fmt: skip

    def push(route: Any) -> None:
        pushed.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(json={"ok": True, "message": "Sent."})

    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/art/edit")
        page.route(
            "**/_img/**", lambda r: r.fulfill(path=str(REPO_ROOT / "static/brand/icon-192.png"))
        )
        page.route("**/step-item?**", item)
        page.route("**/push-step", push)
        tile = page.locator('[data-step="photos"]')
        # Only a dashboard with items has the arrows, inside its picture, named for them.
        assert page.locator('[data-step="clock"] .lu-items').count() == 0
        nxt = tile.locator('.lu-it[aria-label="Next photo"]')
        prev = tile.locator('.lu-it[aria-label="Previous photo"]')
        assert nxt.count() == 1 and prev.count() == 1
        assert tile.locator(".lu-items").evaluate("(e) => getComputedStyle(e).opacity") == "0"
        tile.hover()
        page.wait_for_function(
            "() => getComputedStyle(document.querySelector('[data-step=photos] .lu-items')).opacity === '1'"
        )
        th = tile.locator(".lu-thumb").bounding_box()
        it = tile.locator(".lu-items").bounding_box()
        assert th["x"] <= it["x"] and it["x"] + it["width"] <= th["x"] + th["width"]
        assert th["y"] <= it["y"] and it["y"] + it["height"] <= th["y"] + th["height"]
        nxt.click()
        nxt.click()
        page.wait_for_function(
            "() => document.querySelector('[data-step=photos] .lu-thumb img')?.getAttribute('src') === '/_img/IMG_001.jpg.png'"
        )
        assert asked == ["IMG_001.jpg"]  # the arrows rested on the second photo
        assert tile.locator(".lu-it-pos").inner_text() == "2/31"
        # Push now sends the photo shown.
        tile.locator(".dxe-sel").click()
        page.locator('[data-push="photos"]').click()
        page.wait_for_function("() => !document.querySelector('[data-push=\"photos\"]').disabled")
        assert pushed == [{"page_id": "photos", "display_id": "tv", "item": "IMG_001.jpg"}]
        # The keyboard reaches them too.
        prev.focus()
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => document.querySelector('[data-step=photos] .lu-thumb img')?.getAttribute('src') === '/_img/IMG_000.jpg.png'"
        )
        _settle(page)
        assert errors == []
        browser.close()


def test_a_drawing_dashboards_arrows_draw_another_and_go_back(
    real: tuple[str, Flask], monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from playwright.sync_api import sync_playwright

    base, app = real
    reg = app.config["PLUGIN_REGISTRY"]
    real_get = reg.get
    album = SimpleNamespace(manifest={"name": "Google album"}, server_module=SimpleNamespace())
    monkeypatch.setattr(
        reg, "get", lambda pid: album if pid == "picture_google_album" else real_get(pid)
    )
    cell = Cell(id="a", plugin="picture_google_album", x=0, y=0, w=10, h=10, options={})
    app.config["PAGE_STORE"].save(Page(id="album", name="Album", device_ids=["tv"], cells=[cell]))
    decks = app.config["DECK_STORE"]
    art = decks.get("art")
    decks.upsert(art.model_copy(update={"pages": [*art.pages, DeckPage(page_id="album")]}))
    drawn: list[str] = []

    def item(route: Any) -> None:
        from urllib.parse import parse_qs, urlparse

        key = parse_qs(urlparse(route.request.url).query)["item"][0]
        drawn.append(key)
        n = len(drawn)
        route.fulfill(
            json={"ok": True, "state": "ready", "item": key, "thumb_url": f"/_img/d{n}.png",
                  "large_url": f"/_img/d{n}.png", "sig": f"s{n}"}
        )  # fmt: skip

    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/art/edit")
        icon = str(REPO_ROOT / "static/brand/icon-192.png")
        page.route("**/_img/**", lambda r: r.fulfill(path=icon))
        page.route("**/step-item?**", item)
        tile = page.locator('[data-step="album"]')
        # It cannot count its photos: a stacked tile that says "draw".
        assert tile.locator(".lu-count").inner_text() == "draw"
        assert tile.locator(".lu-tile.stacked").count() == 1
        back = tile.locator('.lu-it[aria-label="Previous picture"]')
        assert back.is_disabled()  # nothing drawn here yet
        tile.hover()
        tile.locator('.lu-it[aria-label="Draw another"]').click()
        src = "() => document.querySelector('[data-step=album] .lu-thumb img')?.getAttribute('src')"
        page.wait_for_function(f"({src})() === '/_img/d1.png'")
        assert drawn[0].startswith("draw:")
        # Its details list what was drawn; Draw another there draws one more.
        tile.locator(".dxe-sel").click()
        page.locator('[data-draw="album"]').click()
        page.wait_for_function(f"({src})() === '/_img/d2.png'")
        assert page.locator('[data-panel="album"] .dxe-strip .dxe-it').count() == 3
        # Back walks through them without drawing again, then to the picture as sent.
        tile.hover()
        back.click()
        page.wait_for_function(f"({src})() === '/_img/d1.png'")
        back.click()
        page.wait_for_function(f"!({src})()?.startsWith('/_img/')")
        assert len(drawn) == 2
        _settle(page)
        assert errors == []
        browser.close()


def _loc(page: Any) -> str:
    return page.locator("#dxe-loc").inner_text().replace("\n", " ")


def _open_move(page: Any) -> Any:
    # The menu eases shut after a choice; open it again once it has closed.
    page.wait_for_function("() => !document.querySelector('.dxe-top-ctl details.lu-more').open")
    page.locator("[data-loc-more]").click()
    page.locator("[data-move]").click()
    dlg = page.locator("dialog.dxe-move-dialog")
    dlg.wait_for(state="visible")
    return dlg


def test_the_header_holds_push_now_and_a_menu_with_move_and_delete(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/morning/edit")
        assert page.locator("[data-display-select], [data-change-display]").count() == 0
        assert "Display" not in page.locator(".dxe-sections").inner_text()
        assert _loc(page).startswith("On hall")
        # Push now and the menu are gone from the footer.
        assert (
            page.locator(".dxe-actionbar [data-push-lineup], .dxe-actionbar summary").count() == 0
        )
        assert [b.inner_text() for b in page.locator(".dxe-actionbar-btns button").all()] == [
            "Discard",
            "Save lineup",
        ]
        push = page.locator("[data-push-lineup]")
        assert push.inner_text() == "Push now"
        move = page.locator("[data-move]")
        menu = page.locator(".dxe-top-ctl details.lu-more")
        assert menu.evaluate("n => n.open") is False
        # Beside the On switch, top right, and the menu opens from the keyboard.
        a, b = push.bounding_box(), page.locator("#dxe-enabled").locator("xpath=..").bounding_box()
        assert abs((a["y"] + a["height"] / 2) - (b["y"] + b["height"] / 2)) < 20
        assert a["x"] < b["x"] and a["x"] > 600
        page.locator("[data-loc-more]").focus()
        page.keyboard.press("Enter")
        assert menu.evaluate("n => n.open") is True
        assert move.inner_text() == "Move to another display…"
        assert page.locator(".dxe-top-ctl .lu-more-menu .danger").inner_text() == "Delete lineup"
        assert page.locator(".dxe-top-ctl .lu-more-sep").count() == 1
        page.wait_for_timeout(300)
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('.dxe-top-ctl details.lu-more').open")
        assert _fk(page) == "loc-more"
        page.locator("[data-loc-more]").click()
        page.wait_for_timeout(300)
        page.screenshot(
            path="/tmp/lu-shots/header-menu.png",
            clip={"x": 0, "y": 0, "width": 1280, "height": 360},
        )
        _settle(page)
        assert errors == []
        browser.close()


def test_move_stages_shows_pending_saves_and_cancel_and_discard_reset(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    store = app.config["DECK_STORE"]
    state = "#dxe-save-state"
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/morning/edit")
        # Cancel (and Escape) leave everything as it was.
        dlg = _open_move(page)
        assert dlg.locator("select option").all_inner_texts() == ["tv"]
        assert dlg.locator("#dxe-move-lines").inner_text().startswith("Stops on hall; runs on tv.")
        assert "bind 2 dashboards to tv" in dlg.inner_text()
        dlg.locator("[data-cancel]").click()
        dlg.wait_for(state="detached")
        assert _fk(page) == "loc-more"
        assert _loc(page).startswith("On hall") and page.locator(state).inner_text() == (
            "All changes saved"
        )
        _open_move(page)
        page.keyboard.press("Escape")
        page.locator("dialog.dxe-move-dialog").wait_for(state="detached")
        assert _loc(page).startswith("On hall")
        # Confirm asks to bind the dashboards, then only stages it.
        dlg = _open_move(page)
        dlg.locator("[data-ok]").click()
        page.locator("dialog.bind-dialog:not(.dxe-move-dialog) button.primary").click()
        page.locator("#dxe-loc .is-pending").wait_for()
        assert _loc(page).startswith("Moving to tv")
        assert page.locator(state).inner_text() == "Unsaved changes"
        assert store.get("morning").device_ids == ["hall"]
        assert page.locator("#dxe-crumb-here").inner_text() == "tv"
        # Discard resets the move, and the bind answer with it.
        page.locator("[data-discard]").click()
        assert _loc(page).startswith("On hall")
        assert page.locator(state).inner_text() == "All changes saved"
        assert page.locator("[data-move-undo]").count() == 0
        # Undo drops it too.
        dlg = _open_move(page)
        dlg.locator("[data-ok]").click()
        page.locator("dialog.bind-dialog:not(.dxe-move-dialog) button.primary").click()
        page.locator("[data-move-undo]").click()
        assert _loc(page).startswith("On hall")
        assert page.locator(state).inner_text() == "All changes saved"
        # Discard asks again: nothing was remembered. Then Save moves it.
        dlg = _open_move(page)
        dlg.locator("[data-ok]").click()
        page.locator("dialog.bind-dialog:not(.dxe-move-dialog)").wait_for(state="visible")
        page.locator("dialog.bind-dialog:not(.dxe-move-dialog) button.primary").click()
        _save(page)
        assert store.get("morning").device_ids == ["tv"]
        assert app.config["PAGE_STORE"].get("weather").device_ids == ["tv"]
        assert _loc(page).startswith("On tv")
        assert errors == []
        browser.close()


def test_move_cancelled_at_the_bind_prompt_stays_put(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/morning/edit")
        dlg = _open_move(page)
        dlg.locator("[data-ok]").click()
        page.locator("dialog.bind-dialog:not(.dxe-move-dialog) button.ghost").click()
        page.locator("dialog.bind-dialog:not(.dxe-move-dialog)").wait_for(state="detached")
        assert _loc(page).startswith("On hall")
        assert page.locator("#dxe-save-state").inner_text() == "All changes saved"
        assert errors == []
        browser.close()


def test_move_says_only_the_first_display_moves_on_a_shared_lineup(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    app.config["PAGE_STORE"].save(Page(id="both", name="Both", device_ids=["hall", "tv"]))
    app.config["DECK_STORE"].upsert(
        Deck(
            id="shared", name="Shared", device_ids=["hall", "tv"], pages=[DeckPage(page_id="both")]
        )
    )
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/shared/edit")
        assert _loc(page).startswith("On hall + tv")
        dlg = _open_move(page)
        assert "Only hall moves; tv stays." in dlg.locator("#dxe-move-lines").inner_text()
        assert errors == []
        browser.close()


def test_move_is_readable_in_a_phone_width_dark_page_and_labelled(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/morning/edit", 420)
        page.evaluate("() => document.documentElement.setAttribute('data-theme', 'dark')")
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path="/tmp/lu-move/header-420.png")
        dlg = _open_move(page)
        assert dlg.get_attribute("aria-labelledby") == "dxe-move-title"
        assert dlg.locator("label[for=dxe-move-to]").inner_text() == "Move to"
        # Focus starts in the dialog, on the display choice, and Escape gives it back.
        assert page.evaluate("() => document.activeElement.id") == "dxe-move-to"
        page.screenshot(path="/tmp/lu-move/dialog-420.png")
        page.keyboard.press("Escape")
        dlg.wait_for(state="detached")
        assert _fk(page) == "loc-more"
        assert errors == []
        browser.close()


# -- the "On screen now" thumbnail takes the display's shape ----------------------------


def _frame(app: Flask) -> None:
    c = app.test_client()
    code = app.config["PAIRING_STORE"].issue(note="t").code
    resp = c.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "frame", "kind": "visionect"}),
    )
    assert resp.status_code == 201
    app.config["PAGE_STORE"].save(Page(id="fp", name="Frame page", device_ids=["frame"]))
    app.config["DECK_STORE"].upsert(
        Deck(id="fd", name="Frame lineup", device_ids=["frame"], pages=[DeckPage(page_id="fp")])
    )


_BOXES = """(sel) => [...document.querySelectorAll(sel)].map((t) => {
    const r = t.getBoundingClientRect();
    return [r.width, r.height, t.style.getPropertyValue('--arw') / t.style.getPropertyValue('--arh')];
})"""


@pytest.mark.parametrize("width", [1280, 420])
def test_the_header_thumbnail_is_the_displays_exact_shape(
    real: tuple[str, Flask], width: int
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    _frame(app)
    with sync_playwright() as p:
        for path, portrait in (("/decks/fd/edit", True), ("/decks/art/edit", False)):
            browser, page, errors = _open(p, base, path, width)
            ((w, h, ratio),) = page.evaluate(_BOXES, "#dxe-now .lu-thumb")
            assert abs(w / h - ratio) < 0.02
            assert (h > w) is portrait
            assert h <= 200
            if portrait and width == 1280:
                page.screenshot(path="/tmp/lu-move/header-portrait.png")
            assert errors == []
            browser.close()


def test_the_new_lineup_display_cards_keep_each_displays_shape(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    _frame(app)
    with sync_playwright() as p:
        for width in (1280, 420):
            browser, page, _errors = _open(p, base, "/decks/new", width)
            boxes = page.evaluate(_BOXES, ".dxe-dcard-th .lu-thumb")
            assert len(boxes) == 3
            for w, h, ratio in boxes:
                assert abs(w / h - ratio) < 0.02
            assert any(h > w for w, h, _r in boxes)
            browser.close()


# -- the header's day bar: this lineup's block drags and resizes into When it runs -----


def _window_lineup(app: Flask) -> None:
    app.config["DECK_STORE"].upsert(
        Deck(
            id="mid", name="Midday", device_ids=["tv"], advance="timer", advance_trigger="cycle",
            advance_anchor="09:00", advance_end_at="12:00",
            pages=[DeckPage(page_id="clock"), DeckPage(page_id="climate")],
        )
    )  # fmt: skip


def _times(page: Any) -> tuple[str, str]:
    return (
        page.locator('[data-time="start"]').input_value(),
        page.locator('[data-time="end"]').input_value(),
    )


def test_dragging_this_lineups_block_stages_the_time_and_nothing_else(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    _window_lineup(app)
    store = app.config["DECK_STORE"]
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/mid/edit")
        seen: list[str] = []
        page.on("request", lambda r: r.method == "POST" and seen.append(r.url))
        blk = page.locator(".dxe-daybar .lu-blk[data-edit]")
        # Only this lineup's block is draggable; the day's other blocks are inert.
        assert blk.count() == 1 and blk.get_attribute("data-lineup") == "mid"
        assert page.locator(".dxe-daybar .lu-blk:not([data-edit])").count() >= 1
        assert (
            page.locator(".dxe-daybar .lu-blk:not([data-edit]):not(.is-link)[tabindex]").count()
            == 0
        )
        assert blk.get_attribute("role") == "button"
        assert "Shift+arrows to resize the end" in blk.get_attribute("aria-label")
        assert _times(page) == ("09:00", "12:00")
        box = blk.bounding_box()
        track = page.locator(".dxe-daybar .lu-trk").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        hour = track["width"] / 24
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + hour * 1.1, y, steps=5)
        assert page.locator(".lu-drag-tip").inner_text().startswith("10\u202fAM")
        page.mouse.up()
        # The fields follow, the footer says unsaved, nothing was saved.
        assert _times(page) == ("10:00", "13:00")
        assert page.locator("#dxe-save-state").inner_text() == "Unsaved changes"
        assert store.get("mid").advance_anchor == "09:00"
        assert not [u for u in seen if "/window" in u or "/edits" in u]
        # A handle resizes the end alone.
        box = page.locator(".dxe-daybar .lu-blk[data-edit]").bounding_box()
        page.mouse.move(box["x"] + box["width"] - 3, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] - 3 + hour * 0.5, box["y"] + 5, steps=5)
        page.mouse.up()
        assert _times(page) == ("10:00", "13:30")
        # Discard puts the bar and the fields back.
        page.locator("[data-discard]").click()
        assert _times(page) == ("09:00", "12:00")
        assert page.locator("#dxe-save-state").inner_text() == "All changes saved"
        # Save applies a drag.
        box = page.locator(".dxe-daybar .lu-blk[data-edit]").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + hour * 2.1, y, steps=5)
        page.mouse.up()
        _save(page)
        d = store.get("mid")
        assert (d.advance_anchor, d.advance_end_at) == ("11:00", "14:00")
        assert not [u for u in seen if "/window" in u or "/edits" in u]
        assert errors == []
        browser.close()


def test_the_block_takes_the_lineups_keys_and_stops_at_the_next_lineup(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    _window_lineup(app)
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/mid/edit")
        page.locator(".dxe-daybar .lu-blk[data-edit]").focus()
        page.keyboard.press("ArrowRight")
        assert _times(page) == ("09:15", "12:15")
        assert _fk(page) == "bar-mine"
        page.keyboard.press("Shift+ArrowRight")
        assert _times(page) == ("09:15", "12:30")
        page.keyboard.press("Alt+Shift+ArrowLeft")
        assert _times(page) == ("09:00", "12:30")
        assert page.locator("#dxe-save-state").inner_text() == "Unsaved changes"
        # Enter goes to the start field.
        page.keyboard.press("Enter")
        assert _fk(page) == "start"
        # The 06:00 push on the same display is a wall: the start stops after it.
        page.locator(".dxe-daybar .lu-blk[data-edit]").focus()
        for _ in range(20):
            page.keyboard.press("Alt+Shift+ArrowLeft")
        assert _times(page)[0] == "06:15"
        assert errors == []
        browser.close()


def test_a_pushes_block_only_moves_and_the_bar_is_read_only_when_narrow(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, _errors = _open(p, base, "/decks/art/edit")
        blk = page.locator(".dxe-daybar .lu-blk[data-edit]")
        assert blk.count() == 1
        assert "resize" not in blk.get_attribute("aria-label")
        assert page.locator(".dxe-daybar .lu-h").count() == 0
        blk.focus()
        page.keyboard.press("ArrowRight")
        assert page.locator('[data-time="start"]').input_value() == "06:15"
        page.keyboard.press("Shift+ArrowRight")  # no end to resize
        assert page.locator('[data-time="start"]').input_value() == "06:30"
        browser.close()
        browser, page, _errors = _open(p, base, "/decks/art/edit", 420)
        assert page.locator(".dxe-daybar .lu-blk[data-edit]").count() == 0
        browser.close()


def test_another_lineups_block_opens_its_editor_and_guards_unsaved_edits(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    _window_lineup(app)
    app.config["DECK_STORE"].upsert(
        Deck(
            id="eve", name="Evening", device_ids=["tv"], advance="timer", advance_trigger="cycle",
            advance_anchor="18:00", advance_end_at="21:00",
            pages=[DeckPage(page_id="clock"), DeckPage(page_id="climate")],
        )
    )  # fmt: skip
    other = '.dxe-daybar .lu-blk.is-link[data-lineup="eve"]'
    with sync_playwright() as p:
        browser, page, _errors = _open(p, base, "/decks/mid/edit")
        link = page.locator(other)
        assert link.get_attribute("role") == "link"
        assert link.get_attribute("title") == "Edit Evening"
        assert "Edit Evening" in link.get_attribute("aria-label")
        # This lineup's own block and holds never navigate.
        assert page.locator(".dxe-daybar .lu-blk[data-edit]").get_attribute("role") == "button"
        assert page.locator(".dxe-daybar .lu-blk.hold.is-link").count() == 0
        page.locator(".dxe-daybar .lu-blk[data-edit]").click()
        assert page.url.endswith("/decks/mid/edit")
        # A click opens the other lineup's editor.
        link.click()
        page.wait_for_url("**/decks/eve/edit")
        _settle(page)
        # Keyboard: focus and Enter.
        page.goto(base + "/decks/mid/edit")
        _settle(page)
        page.locator(other).focus()
        page.keyboard.press("Enter")
        page.wait_for_url("**/decks/eve/edit")
        # With unsaved edits the leave-page prompt appears; dismissing stays.
        page.goto(base + "/decks/mid/edit")
        _settle(page)
        page.locator("#dxe-enabled").click()
        assert page.locator("#dxe-save-state").inner_text() == "Unsaved changes"
        dialogs: list[str] = []
        page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
        page.locator(other).evaluate("(n) => n.click()")
        page.wait_for_timeout(300)
        assert dialogs == ["beforeunload"]
        assert page.url.endswith("/decks/mid/edit")
        browser.close()


def test_show_all_edits_the_whole_week_from_one_page(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    app.config["DECK_STORE"].upsert(
        Deck(
            id="wk", name="Weekdays", device_ids=["tv"], advance="timer", advance_trigger="cycle",
            advance_anchor="09:00", advance_end_at="12:00", advance_days_of_week=[0, 1, 2, 3, 4],
            pages=[DeckPage(page_id="clock"), DeckPage(page_id="climate")],
        )
    )  # fmt: skip
    app.config["DECK_STORE"].upsert(
        Deck(
            id="eve", name="Evening", device_ids=["tv"], advance="timer", advance_trigger="cycle",
            advance_anchor="18:00", advance_end_at="21:00",
            pages=[DeckPage(page_id="clock"), DeckPage(page_id="climate")],
        )
    )  # fmt: skip
    mine = ".dxe-daybar .lu-blk[data-edit]"
    with sync_playwright() as p:
        browser, page, _errors = _open(p, base, "/decks/wk/edit")
        assert page.locator(".dxe-wk-row").count() == 0
        toggle = page.locator("[data-week-toggle]")
        assert toggle.inner_text() == "Show all"
        toggle.click()
        assert page.locator("[data-week-toggle]").inner_text() == "Show today"
        labels = page.locator(".dxe-wk-row:not(.dxe-wk-scale) .dxe-wk-lab").all_inner_texts()
        assert [t.lower() for t in labels] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        assert page.locator(".dxe-wk-row.is-today").count() == 1
        # Its block is on its five days only, each draggable.
        assert page.locator(mine).count() == 5
        assert page.locator(".dxe-wk-row .lu-blk.is-link").count() >= 5
        # Dragging a block on a day that is not today edits the same start and end.
        today = int(page.locator(".dxe-wk-row.is-today").get_attribute("data-day"))
        day = next(d for d in range(5) if d != today)
        blk = page.locator(f'.dxe-wk-row[data-day="{day}"] .lu-blk[data-edit]')
        box = blk.bounding_box()
        hour = page.locator(f'.dxe-wk-row[data-day="{day}"] .lu-trk').bounding_box()["width"] / 24
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + hour * 1.1, y, steps=5)
        page.mouse.up()
        assert _times(page) == ("10:00", "13:00")
        # An empty cell on a day it does not run never adds the day.
        off = page.locator('.dxe-wk-row[data-day="6"] .lu-trk')
        before = page.locator("[data-day].lu-dt.on").count()
        off.click(position={"x": 60, "y": 8})
        assert page.locator("[data-day].lu-dt.on").count() == before
        assert page.locator(mine).count() == 5
        # The state is remembered per browser and survives a switch to another lineup.
        page.evaluate("window.scrollTo(0, 40)")
        dialogs: list[str] = []
        page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
        page.locator(".dxe-wk-row .lu-blk.is-link[data-lineup='eve']").first.evaluate(
            "(n) => n.click()"
        )
        page.wait_for_timeout(300)
        assert dialogs == ["beforeunload"]  # unsaved edits still guard the switch
        page.locator("[data-discard]").click()
        page.reload()
        _settle(page)
        assert page.locator("[data-week-toggle]").inner_text() == "Show today"
        page.locator(".dxe-wk-row .lu-blk.is-link[data-lineup='eve']").first.click()
        page.wait_for_url("**/decks/eve/edit")
        _settle(page)
        assert page.locator(".dxe-wk-row").count() == 8
        # Show today collapses it again.
        page.locator("[data-week-toggle]").click()
        assert page.locator(".dxe-wk-row").count() == 0
        browser.close()


def test_pace_names_the_dashboards_with_their_own_time_and_links_to_them(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    decks = app.config["DECK_STORE"]
    morning = decks.get("morning")
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/morning/edit")
        # No dashboard has its own time: nothing is said.
        assert page.locator(".dxe-overridden").count() == 0
        assert "its own time" not in page.locator("#dxe-sections").inner_text()
        browser.close()
        decks.upsert(
            morning.model_copy(
                update={
                    "pages": [
                        morning.pages[0].model_copy(update={"dwell_minutes": 50}),
                        morning.pages[1].model_copy(update={"dwell_minutes": 63}),
                    ]
                }
            )
        )
        browser, page, errors = _open(p, base, "/decks/morning/edit")
        line = page.locator(".dxe-overridden")
        assert line.inner_text() == "Overridden by: Weather (50 min), Agenda (1 h 3 min)"
        line.locator('[data-overridden="agenda"]').click()
        assert page.locator('[data-step="agenda"].is-open').count() == 1
        assert page.locator('[data-step="agenda"].is-flash').count() == 1
        _settle(page)
        assert errors == []
        browser.close()


def test_the_item_control_says_rotate_items_and_show_chosen(real: tuple[str, Flask]) -> None:
    from types import SimpleNamespace

    from playwright.sync_api import sync_playwright

    base, app = real
    papers = [{"key": k, "label": k.upper()} for k in ("nyt", "wsj", "ft")]
    reg = app.config["PLUGIN_REGISTRY"]
    real_get = reg.get
    fake = SimpleNamespace(
        manifest={"lineups": {"pin_option": "paper", "noun": "papers"}},
        server_module=SimpleNamespace(lineup_items=lambda options: papers),
    )
    reg.get = lambda pid: fake if pid == "papers" else real_get(pid)
    app.config["PAGE_STORE"].save(
        Page(
            id="news",
            name="News",
            device_ids=["tv"],
            cells=[Cell(id="n", plugin="papers", x=0, y=0, w=10, h=10, options={"paper": "nyt"})],
        )
    )
    app.config["DECK_STORE"].upsert(
        Deck(id="readers", name="Readers", device_ids=["tv"], pages=[DeckPage(page_id="news")])
    )
    app.config["DECK_STORE"].upsert(
        Deck(id="more", name="More", device_ids=["tv"], pages=[DeckPage(page_id="news")])
    )  # a dashboard in two lineups says its choice applies to both
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/readers/edit")
        page.locator('[data-expand="news"]').click()
        seg = page.locator("[data-pin-mode]")
        assert [b.inner_text() for b in seg.all()] == ["Rotate items", "Show chosen"]
        assert (
            "Applies to this dashboard in every lineup." in page.locator("#dxe-detail").inner_text()
        )
        assert page.locator(".dxe-it .dxe-tag").first.inner_text().lower() == "chosen"
        _settle(page)
        assert errors == []
        browser.close()
