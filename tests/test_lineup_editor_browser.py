"""The lineup editor in a live Chromium, against the real routes: the header's
slice of the day, 1 When it runs (an end time turns a push into a rotation),
2 Dashboards (Alt+Arrow reorder, a gallery's items and their searchable grid,
add and remove, Discard), 3 Pace (options only where the display has them;
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
        assert "Gallery · 31 photos" in row.inner_text()
        assert row.locator(".dxe-badge").inner_text() == "×31"
        # Alt+Down moves it; the live region says where it went.
        row.locator("[data-grip]").focus()
        page.keyboard.press("Alt+ArrowDown")
        assert _steps(page) == ["clock", "photos", "climate"]
        assert page.locator("[data-step]:focus-within").get_attribute("data-step") == "photos"
        # Its items: the first few, then every one in a searchable, paged grid.
        page.locator('[data-expand="photos"]').click()
        strip = page.locator('[data-step="photos"] .dxe-strip .dxe-it')
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
        page.locator('[data-time="start"]').fill("06:00")
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
            "06:00",
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
