"""The "Show it on this display?" prompt in a live Chromium, against the real
routes: the deck editor (adding a dashboard from another display, switching
the lineup's display), the setup wizard and the schedule form. One checkbox
per display: the lineup's display stays ticked, the dashboard's other displays
start ticked and come off when unticked. Cancel adds and saves nothing; the
saved binding matches the summary line."""

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
    page = browser.new_context(**{"viewport": {"width": 1440, "height": 900}, **ctx}).new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/decks/{deck}/edit", wait_until="load")
    return browser, page, errors


def _answer(page: Any, name: str | re.Pattern[str]) -> None:
    """Click a button in the prompt and wait for it to finish closing."""
    dlg = page.locator("dialog.bind-dialog")
    dlg.get_by_role("button", name=name).click()
    dlg.wait_for(state="detached")


def _move(page: Any) -> None:
    """The old "Bind to <display>": untick every other display, confirm."""
    dlg = page.locator("dialog.bind-dialog:not(.dxe-move-dialog)")
    dlg.wait_for(state="visible")
    for box in dlg.locator(".bind-row:not(.is-target) input").all():
        box.uncheck()
    _answer(page, re.compile(r"^Move to "))


def _also(page: Any) -> None:
    """The old "Bind to both": leave the ticks as they are, confirm."""
    _answer(page, re.compile(r"^Show on "))


def _save(page: Any) -> None:
    """Save lineup: the editor saves, then reloads itself."""
    with page.expect_navigation():
        page.locator("[data-save]").click()
    # Thumbnails are still loading; closing the browser mid-response leaves
    # werkzeug's file handle open (ResourceWarning).
    page.wait_for_function("() => [...document.images].every(i => i.complete)")


def _steps(page: Any) -> list[str]:
    return [r.get_attribute("data-step") for r in page.locator("[data-step]").all()]


def _other(page: Any, page_id: str) -> Any:
    """A dashboard bound to another display, in the picker (opened if closed)."""
    if not page.locator("#dxe-picker").count():
        page.locator("[data-picker]").click()
    return page.locator(f'#dxe-pg-other ~ [data-add="{page_id}"]')


def _add_other(page: Any, page_id: str) -> None:
    """Pick it and press Add: the bind prompt comes up over the picker."""
    _other(page, page_id).click()
    page.locator("#dxe-picker [data-add-confirm]").click()


def test_adding_a_dashboard_from_another_display_asks_first(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _editor(p, base)
        chip = _other(page, "photos")
        assert chip.count() == 1
        assert page.locator('#dxe-pg-here ~ [data-add="weather"]').count() == 1

        # Cancel: the prompt closes, the step is not added, nothing is bound; the
        # picker stays open with the choice.
        _add_other(page, "photos")
        dlg = page.locator("dialog.bind-dialog")
        assert dlg.get_by_role("heading").inner_text() == "Show “Google Photos” on hallway?"
        assert dlg.get_by_role("checkbox", name="frame").is_checked()
        _answer(page, "Cancel")
        assert _steps(page) == ["clock"]
        assert page.locator("#dxe-picker").is_visible()

        # Escape cancels the prompt only.
        page.locator("#dxe-picker [data-add-confirm]").click()
        dlg.wait_for()
        page.keyboard.press("Escape")
        page.locator("dialog.bind-dialog").wait_for(state="detached")
        assert _steps(page) == ["clock"]
        assert page.locator("#dxe-picker").is_visible()

        # Closing the picker after all adds nothing.
        page.locator("#dxe-picker .wizard-footer [data-add-cancel]").click()
        assert _steps(page) == ["clock"]
        assert _bound(app, "photos") == ["frame"]

        # Bind agreed: added at once; the display binding waits for Save.
        _add_other(page, "photos")
        _move(page)
        page.locator("#dxe-picker").wait_for(state="detached")
        assert _steps(page) == ["clock", "photos"]
        assert _bound(app, "photos") == ["frame"]  # nothing changes until Save
        _save(page)
        assert _members(app) == ["clock", "photos"]
        assert _bound(app, "photos") == ["hallway"]
        assert errors == []
        browser.close()


def test_leaving_the_ticks_keeps_the_other_display(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _editor(p, base)
        _other(page, "photos").click()
        page.locator("#dxe-picker .dxe-pick-cond > summary").click()
        page.locator("#dxe-picker").get_by_role("button", name="Add condition", exact=True).click()
        page.locator("#dxe-picker .cp-row").get_by_role("combobox").first.select_option(
            "time_window"
        )
        page.locator("#dxe-picker [data-add-confirm]").click()
        _also(page)
        assert _steps(page) == ["clock", "photos"]
        _save(page)
        assert _members(app) == ["clock", "photos"]
        assert _bound(app, "photos") == ["frame", "hallway"]
        assert len(app.config["DECK_STORE"].get("mine").pages[1].conditions) == 1
        assert errors == []
        browser.close()


def test_switching_display_asks_and_cancel_reverts(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _editor(p, base)
        loc = page.locator("#dxe-loc")
        assert loc.inner_text().startswith("On hallway")

        def move() -> Any:
            # Move to another display… lives in the ⋯ menu by the On switch.
            page.locator("[data-loc-more]").click()
            page.locator("[data-move]").click()
            mv = page.locator("dialog.dxe-move-dialog")
            mv.wait_for(state="visible")
            mv.locator("select").select_option("frame")
            mv.locator("[data-ok]").click()
            return page.locator("dialog.bind-dialog:not(.dxe-move-dialog)")

        dlg = move()
        assert dlg.get_by_role("heading").inner_text() == "Show “Clock” on frame?"
        _answer(page, "Cancel")
        assert loc.inner_text().startswith("On hallway")  # nothing changed
        assert page.locator(".dxe-step .dxe-tag.warn").count() == 0

        move()
        _move(page)
        assert loc.inner_text().startswith("Moving to frame")
        assert page.locator(".dxe-step .dxe-tag.warn").count() == 1
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
        card = page.locator(".dxe-step .dxe-tag.warn")
        assert card.count() == 1 and "Not on this display" in (card.text_content() or "")
        page.locator("[data-save]").click()
        _answer(page, "Cancel")
        assert _bound(app, "photos") == ["frame"]  # nothing saved
        page.locator("[data-save]").click()
        with page.expect_navigation():
            _also(page)
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
        _move(page)
        page.wait_for_function(
            "() => document.querySelector('#timed-wizard [data-wizard-view=created]:not([hidden])')"
        )
        assert len(app.config["DECK_STORE"].all()) == before + 1
        assert _bound(app, "photos") == ["hallway"]
        assert errors == []
        browser.close()


@pytest.mark.parametrize("editor", ["classic", "new"])
def test_schedule_form_asks_before_saving(real: tuple[str, Flask], editor: str) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    app.config["SETTINGS_STORE"].patch_section("ui", {"dashboard_editor": editor})
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/pages/photos", wait_until="load")
        if editor == "new":
            page.locator("#ev2-h-schedules").click()  # the step is collapsed to a summary
            page.get_by_role("button", name="New scheduled lineup", exact=True).click()
        else:
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
        _move(page)
        page.wait_for_function("() => !document.querySelector('#schedule-dialog-new[open]')")
        assert len(app.config["SCHEDULE_STORE"].all()) == 1
        assert _bound(app, "photos") == ["hallway"]
        assert errors == []
        browser.close()


# -- the checkbox list ---------------------------------------------------------------

_PHONE: dict[str, Any] = {
    "viewport": {"width": 390, "height": 844},
    "is_mobile": True,
    "has_touch": True,
}


def _add_display(app: Flask, dev: str) -> None:
    code = app.config["PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": dev, "kind": "esp32_client"}),
    )
    assert resp.status_code == 201


def _prompt(page: Any) -> Any:
    dlg = page.locator("dialog.bind-dialog:not(.dxe-move-dialog)")
    dlg.wait_for(state="visible")
    return dlg


def _rows(dlg: Any) -> list[tuple[str, bool]]:
    return [
        (r.locator(".bind-row-name").inner_text(), r.locator("input").is_checked())
        for r in dlg.locator(".bind-row").all()
    ]


def _summary(dlg: Any) -> str:
    return str(dlg.locator(".bind-dialog-summary").inner_text())


@pytest.mark.parametrize("phone", [False, True], ids=["desktop", "phone"])
def test_unticking_a_display_takes_the_dashboard_off_it(
    real: tuple[str, Flask], phone: bool
) -> None:
    """Bound to several: every current display is listed and ticked, the
    lineup's own is ticked and stays so; unticking one drops it from the
    summary, and the save binds exactly what the summary said."""
    from playwright.sync_api import sync_playwright

    base, app = real
    _add_display(app, "door")
    app.config["PAGE_STORE"].save(Page(id="art", name="Art", device_ids=["frame", "door"]))
    with sync_playwright() as p:
        browser, page, errors = _editor(p, base, **(_PHONE if phone else {}))
        _add_other(page, "art")
        dlg = _prompt(page)
        assert dlg.get_by_role("heading").inner_text() == "Show “Art” on hallway?"
        assert dlg.locator("fieldset legend").inner_text() == "Displays"
        assert _rows(dlg) == [("hallway", True), ("frame", True), ("door", True)]
        assert _summary(dlg) == "Art will be on hallway, frame and door."
        assert dlg.locator(".bind-dialog-summary").get_attribute("aria-live") == "polite"
        ok = dlg.locator(".wizard-footer button.primary")
        assert ok.inner_text() == "Show on 3 displays" and ok.is_enabled()

        # The lineup's display can't be dropped: the tick stays, a hint says why.
        target = dlg.get_by_role("checkbox", name="hallway")
        assert target.get_attribute("aria-disabled") == "true"
        target.click(force=True)
        assert target.is_checked()
        assert "stays ticked" in dlg.locator(".bind-dialog-hint").inner_text()
        assert ok.is_enabled()

        dlg.get_by_role("checkbox", name="door").uncheck()
        assert _summary(dlg) == "Art will be on hallway and frame. Removed from door."
        assert "Comes off" in dlg.locator(".bind-row.is-off").inner_text()
        assert ok.inner_text() == "Show on 2 displays"
        dlg.get_by_role("checkbox", name="frame").uncheck()
        assert _summary(dlg) == "Art will be on hallway. Removed from frame and door."
        assert ok.inner_text() == "Move to hallway"
        dlg.get_by_role("checkbox", name="frame").check()

        if phone:  # a bottom sheet with finger-sized targets
            box = dlg.bounding_box()
            assert abs(box["y"] + box["height"] - 844) < 2 and box["width"] >= 389
            for el in dlg.locator(".bind-row, .wizard-footer button").all():
                assert el.bounding_box()["height"] >= 44
        assert _summary(dlg) == "Art will be on hallway and frame. Removed from door."
        _answer(page, "Show on 2 displays")
        assert _bound(app, "art") == ["frame", "door"]  # nothing until Save
        _save(page)
        assert _members(app) == ["clock", "art"]
        assert sorted(_bound(app, "art")) == ["frame", "hallway"]
        assert errors == []
        browser.close()


def test_cancel_and_escape_forget_the_ticks(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    _add_display(app, "door")
    app.config["PAGE_STORE"].save(Page(id="art", name="Art", device_ids=["frame", "door"]))
    with sync_playwright() as p:
        browser, page, errors = _editor(p, base)
        _add_other(page, "art")
        dlg = _prompt(page)
        dlg.get_by_role("checkbox", name="door").uncheck()
        page.keyboard.press("Escape")
        dlg.wait_for(state="detached")
        _add_other(page, "art")
        dlg = _prompt(page)
        assert _rows(dlg) == [("hallway", True), ("frame", True), ("door", True)]
        dlg.get_by_role("checkbox", name="frame").uncheck()
        _answer(page, "Cancel")
        assert _steps(page) == ["clock"]
        assert _bound(app, "art") == ["frame", "door"]
        assert errors == []
        browser.close()


def test_keyboard_moves_through_the_list_and_stays_in_the_dialog(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _editor(p, base)
        _add_other(page, "photos")
        dlg = _prompt(page)
        focused = "() => document.activeElement.closest('dialog') ? " \
            "(document.activeElement.getAttribute('aria-labelledby') || " \
            "document.activeElement.textContent.trim()) : 'OUTSIDE'"  # fmt: skip
        assert page.evaluate(focused) == "Show on 2 displays"  # the primary has focus
        seen = []
        for _ in range(5):
            page.keyboard.press("Tab")
            seen.append(page.evaluate(focused))
        assert "OUTSIDE" not in seen
        # The rows come first, then Cancel, then the primary.
        order = [x for x in dict.fromkeys(seen)]
        assert order.index("bind-row-0-name") < order.index("bind-row-1-name")
        assert order.index("bind-row-1-name") < order.index("Cancel")
        dlg.get_by_role("checkbox", name="hallway").focus()
        page.keyboard.press("Shift+Tab")
        assert page.evaluate(focused) == "Show on 2 displays"  # wraps backwards too
        # Space toggles a row, and the summary follows.
        dlg.get_by_role("checkbox", name="frame").focus()
        page.keyboard.press("Space")
        assert _summary(dlg) == "Google Photos will be on hallway. Removed from frame."
        assert errors == []
        browser.close()


_ASK = """(pages) => {
  window.__answer = 'pending';
  TesseraeBindPrompt.ask({
    display: {id: 'lounge', name: 'Meural Lounge Frame', width: 1920, height: 1080},
    pages,
  }).then((a) => { window.__answer = a; });
}"""
_DOOR = {"id": "door", "name": "Door Frame", "width": 1200, "height": 1600}
_MINI = {"id": "mini", "name": "Mini Frame", "width": 480, "height": 800}
_LONG = {
    "id": "long",
    "name": "Upstairs landing hallway frame by the big bookshelf near the window",
    "width": 1920,
    "height": 1080,
}


@pytest.mark.parametrize("phone", [False, True], ids=["desktop", "phone"])
def test_edge_cases_none_one_many_and_long_names(real: tuple[str, Flask], phone: bool) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _editor(p, base, **(_PHONE if phone else {}))

        def ask(pages: list[dict[str, Any]]) -> Any:
            page.evaluate(_ASK, pages)
            return _prompt(page)

        def answer() -> Any:
            return page.evaluate("() => window.__answer")

        # Bound to none: just the target, a plain "Show on" and no removals.
        dlg = ask([{"id": "n", "name": "Loose", "bound_to": []}])
        assert _rows(dlg) == [("Meural Lounge Frame", True)]
        assert _summary(dlg) == "Loose will be on Meural Lounge Frame."
        assert dlg.locator(".bind-dialog-note").count() == 0
        _answer(page, "Show on Meural Lounge Frame")
        assert answer() == {"mode": "move", "modes": {"n": "move"}, "keep": {"n": []}}

        # Bound to one, then unticked: a move, with the size note.
        dlg = ask([{"id": "w", "name": "Weather", "bound_to": [_DOOR]}])
        assert _rows(dlg) == [("Meural Lounge Frame", True), ("Door Frame", True)]
        assert "1920×1080, so Weather shows scaled there" in dlg.inner_text()
        dlg.get_by_role("checkbox", name="Door Frame").uncheck()
        _answer(page, "Move to Meural Lounge Frame")
        assert answer()["mode"] == "move"

        # Several dashboards: each row says which it has; per-page answers.
        dlg = ask(
            [
                {"id": "a", "name": "Art", "bound_to": [_DOOR, _MINI]},
                {"id": "w", "name": "Weather", "bound_to": [_DOOR]},
            ]
        )
        assert (
            dlg.get_by_role("heading").inner_text() == "Show 2 dashboards on Meural Lounge Frame?"
        )
        assert "has Art and Weather" in dlg.locator(".bind-row").nth(1).inner_text()
        dlg.get_by_role("checkbox", name="Mini Frame").uncheck()
        assert _summary(dlg) == (
            "These 2 dashboards will be on Meural Lounge Frame and Door Frame. "
            "Removed from Mini Frame."
        )
        _answer(page, "Show on 2 displays")
        got = answer()
        assert got["keep"] == {"a": ["door"], "w": ["door"]}
        assert got["modes"] == {"a": "some", "w": "also"} and got["mode"] == "some"
        fields = page.evaluate(
            "(a) => [TesseraeBindPrompt.fields(a, 'a'), TesseraeBindPrompt.fields(a, 'w')]", got
        )
        assert fields == [
            [["bind_pages", "a"], ["bind_keep", '{"a":["door"]}']],
            [["bind_pages", "w"], ["bind_also", "w"]],
        ]

        # Long names truncate inside the row; nothing spills sideways.
        dlg = ask([{"id": "s", "name": "Night sky over the reservoir, long", "bound_to": [_LONG]}])
        name = dlg.locator(".bind-row-name").nth(1)
        assert name.get_attribute("title") == _LONG["name"]
        assert page.evaluate(
            "() => { const n = document.querySelectorAll('.bind-row-name')[1];"
            " return n.scrollWidth > n.clientWidth; }"
        )
        assert page.evaluate(
            "() => { const d = document.querySelector('dialog.bind-dialog');"
            " return d.scrollWidth <= d.clientWidth && d.getBoundingClientRect().right"
            " <= innerWidth + 1; }"
        )
        page.keyboard.press("Escape")
        dlg.wait_for(state="detached")
        assert answer() is None
        assert errors == []
        browser.close()
