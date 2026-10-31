"""The "Bind to this display?" prompt in a live Chromium, against the real
routes: the deck editor (adding a dashboard from another display, switching
the lineup's display), the setup wizard and the schedule form. Cancel adds
and saves nothing; each bind choice binds exactly what it says."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.deck_model import Deck, DeckPage
from app.state.page_store import Page

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


@pytest.fixture
def real(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
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
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})
    c = app.test_client()
    for dev in ("hallway", "frame"):
        code = app.config["PAIRING_STORE"].issue(note="t").code
        resp = c.post(
            "/api/v1/device/register",
            headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
            data=json.dumps({"device_id": dev, "kind": "esp32_client"}),
        )
        assert resp.status_code == 201
    pages = app.config["PAGE_STORE"]
    pages.save(Page(id="photos", name="Google Photos", device_ids=["frame"]))
    pages.save(Page(id="clock", name="Clock", device_ids=["hallway"]))
    pages.save(Page(id="weather", name="Weather", device_ids=["hallway"]))
    app.config["DECK_STORE"].upsert(
        Deck(id="mine", name="Mine", device_ids=["hallway"], pages=[DeckPage(page_id="clock")])
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


def _bound(app: Flask, page_id: str) -> list[str]:
    return list(app.config["PAGE_STORE"].get(page_id).device_ids)


def _members(app: Flask, deck_id: str = "mine") -> list[str]:
    return app.config["DECK_STORE"].get(deck_id).page_ids


def _editor(p: Any, base: str, deck: str = "mine", **ctx: Any) -> tuple[Any, Any, list[str]]:
    browser = _launch(p)
    page = browser.new_context(viewport={"width": 1440, "height": 900}, **ctx).new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/decks/{deck}/edit", wait_until="load")
    return browser, page, errors


def _answer(page: Any, name: str | re.Pattern[str]) -> None:
    """Click a button in the prompt and wait for it to finish closing."""
    dlg = page.locator("dialog.bind-dialog")
    dlg.get_by_role("button", name=name).click()
    dlg.wait_for(state="detached")


_BIND = re.compile(r"^Bind to (?!both)")


def _save(page: Any) -> None:
    page.locator("#deck-form").evaluate("f => f.requestSubmit()")
    page.wait_for_url(re.compile(r".*/decks(\?|#|$).*"))
    # The Lineups page is still fetching thumbnails; closing the browser
    # mid-response leaves werkzeug's file handle open (ResourceWarning).
    # (Not "networkidle": the page holds an event stream open.)
    page.wait_for_function("() => [...document.images].every(i => i.complete)")


def test_adding_a_dashboard_from_another_display_asks_first(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _editor(p, base)
        chip = page.locator("#dxe-avail .dxe-chip--other", has_text="Google Photos")
        assert chip.count() == 1
        assert page.locator("#dxe-avail .dxe-chip", has_text="Weather").count() == 1

        # Cancel: the dialog closes, the step is not added, nothing is bound.
        chip.click()
        dlg = page.locator("dialog.bind-dialog")
        assert dlg.get_by_role("heading").inner_text().startswith("Bind “Google Photos” to ")
        assert "currently bound to" in dlg.inner_text()
        _answer(page, "Cancel")
        assert [
            c.get_attribute("data-page") for c in page.locator("#dxe-rail .dxe-pcard").all()
        ] == ["clock"]
        assert page.locator("#dxe-pages-field").input_value() == "clock"

        # Escape cancels too.
        chip.click()
        page.keyboard.press("Escape")
        page.locator("dialog.bind-dialog").wait_for(state="detached")
        assert page.locator("#dxe-rail .dxe-pcard").count() == 1

        # Bind: added to the lineup, and bound to this display only on save.
        chip.click()
        _answer(page, _BIND)
        assert page.locator("#dxe-rail .dxe-pcard").count() == 2
        assert _bound(app, "photos") == ["frame"]  # nothing changes until Save
        _save(page)
        assert _members(app) == ["clock", "photos"]
        assert _bound(app, "photos") == ["hallway"]
        assert errors == []
        browser.close()


def test_bind_to_both_keeps_the_other_display(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _editor(p, base)
        page.locator("#dxe-avail .dxe-chip--other", has_text="Google Photos").click()
        _answer(page, "Bind to both")
        _save(page)
        assert _members(app) == ["clock", "photos"]
        assert _bound(app, "photos") == ["frame", "hallway"]
        assert errors == []
        browser.close()


def test_switching_display_asks_and_cancel_reverts(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _editor(p, base)
        sel = page.locator("#dxe-device")
        assert sel.input_value() == "hallway"
        sel.select_option("frame")
        dlg = page.locator("dialog.bind-dialog")
        assert dlg.get_by_role("heading").inner_text().startswith("Bind “Clock” to ")
        _answer(page, "Cancel")
        assert sel.input_value() == "hallway"  # put back, nothing changed
        assert page.locator("#dxe-rail .dxe-pcard.is-offdisplay").count() == 0

        sel.select_option("frame")
        _answer(page, _BIND)
        assert sel.input_value() == "frame"
        assert page.locator("#dxe-rail .dxe-pcard.is-offdisplay").count() == 1
        _save(page)
        assert app.config["DECK_STORE"].get("mine").device_ids == ["frame"]
        assert _bound(app, "clock") == ["frame"]
        assert errors == []
        browser.close()


def test_an_already_broken_lineup_asks_on_save(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    app.config["DECK_STORE"].upsert(
        Deck(id="bad", name="Bad", device_ids=["hallway"], pages=[DeckPage(page_id="photos")])
    )
    with sync_playwright() as p:
        browser, page, errors = _editor(p, base, "bad")
        card = page.locator("#dxe-rail .dxe-pcard.is-offdisplay")
        assert card.count() == 1 and "Not on this display" in card.inner_text()
        page.locator("button[form=deck-form][type=submit]").click()
        _answer(page, "Cancel")
        assert _bound(app, "photos") == ["frame"]  # nothing saved
        page.locator("button[form=deck-form][type=submit]").click()
        _answer(page, "Bind to both")
        page.wait_for_url(re.compile(r".*/decks(\?|#|$).*"))
        assert _bound(app, "photos") == ["frame", "hallway"]
        assert errors == []
        browser.close()


def test_wizard_asks_before_creating_and_cancel_creates_nothing(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    before = len(app.config["DECK_STORE"].all())
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route(re.compile(r".*/events/stream.*"), lambda r: r.fulfill(status=204, body=""))
        page.goto(base + "/decks", wait_until="load")
        page.locator("[data-open-timed-wizard]").first.click()
        wiz = page.locator("#timed-wizard")
        wiz.locator('[data-wizard-mode="interval"]').click()
        wiz.locator("[data-wizard-forward]").click()
        wiz.locator("[data-wizard-dash]").select_option("photos")
        wiz.locator("[data-wizard-device]").select_option("hallway")
        wiz.locator("[data-wizard-forward]").click()
        wiz.locator("[data-wizard-forward]").click()  # create
        prompt = page.locator("dialog.bind-dialog")
        prompt.wait_for()
        _answer(page, "Cancel")
        assert len(app.config["DECK_STORE"].all()) == before
        assert "Nothing was created" in wiz.locator("[data-wizard-error]").inner_text()
        assert _bound(app, "photos") == ["frame"]

        wiz.locator("[data-wizard-forward]").click()
        _answer(page, _BIND)
        page.wait_for_function(
            "() => document.querySelector('#timed-wizard [data-wizard-view=created]:not([hidden])')"
        )
        assert len(app.config["DECK_STORE"].all()) == before + 1
        assert _bound(app, "photos") == ["hallway"]
        assert errors == []
        browser.close()


def test_schedule_form_asks_before_saving(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/pages/photos", wait_until="load")
        page.locator('[data-open-schedule-dialog="schedule-dialog-new"]').click()
        dlg = page.locator("#schedule-dialog-new")
        dlg.locator('input[name="name"]').fill("Photos hourly")
        dlg.locator('label:has(input[name="device_ids"][value="hallway"])').click()
        dlg.locator('button[type="submit"]').first.click()
        prompt = page.locator("dialog.bind-dialog")
        prompt.wait_for()
        _answer(page, "Cancel")
        assert app.config["SCHEDULE_STORE"].all() == []
        assert _bound(app, "photos") == ["frame"]
        dlg.locator('button[type="submit"]').first.click()
        _answer(page, _BIND)
        page.wait_for_function("() => !document.querySelector('#schedule-dialog-new[open]')")
        assert len(app.config["SCHEDULE_STORE"].all()) == 1
        assert _bound(app, "photos") == ["hallway"]
        assert errors == []
        browser.close()
