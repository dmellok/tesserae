"""The lineup editor's Add dashboard picker in a live Chromium, against the real
routes: Add in the picker adds the dashboard at once (no second form), Cancel,
Esc and a click outside add nothing, the keyboard works it alone, and every
picture in it, and in a gallery's Recent photos, has the display's own shape
(a landscape picture is cropped to a portrait frame, never a thin band)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

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

#: The frame: a portrait Visionect panel, 1440x2560.
FRAME_RATIO = 1440 / 2560
#: A landscape picture's colour, and a portrait one's.
WIDE_RGB = (200, 40, 40)
TALL_RGB = (40, 90, 200)
MORE = [f"Dashboard {n:02d}" for n in range(1, 10)]


def _png(size: tuple[int, int], rgb: tuple[int, int, int]) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, rgb).save(buf, "PNG")
    return buf.getvalue()


def _register(app: Flask, dev: str, kind: str) -> None:
    code = app.config["PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": dev, "kind": kind}),
    )
    assert resp.status_code == 201


def _sent(app: Flask, page_id: str, device: str, size: tuple[int, int], n: int) -> list[str]:
    """``n`` frames of ``size`` pushed to ``device``, oldest first; their digests."""
    out = []
    for i in range(n):
        rgb = WIDE_RGB if size[0] > size[1] else TALL_RGB
        data = _png(size, (rgb[0], rgb[1] + i, rgb[2]))
        digest = hashlib.sha256(data).hexdigest()[:16]
        (app.config["RENDERS_DIR"] / f"{digest}.png").write_bytes(data)
        app.config["EVENT_LOG"].record(
            type="push",
            source="lineup",
            target=page_id,
            status="sent",
            extra={"composition_digest": digest, "device_ids": [device]},
        )
        out.append(digest)
    return out


@pytest.fixture
def real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    app: Flask = create_app(
        testing=False,
        data_root=tmp_path / "data",
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    app.config["SCHEDULER"].stop()
    settings = app.config["SETTINGS_STORE"]
    settings.patch_section("app", {"onboarded": True, "locale": "en-US"})
    settings.patch_section("auth", {"disabled": True})
    _register(app, "frame", "visionect")  # portrait
    _register(app, "tv", "opendisplay_ha")  # landscape
    app.config["DEVICE_REGISTRY"].get("tv").manifest["buttons"] = False
    # A gallery like Civitai's AI art: a new picture each send, no list (a draw
    # widget; the Google album stands in, without its server module).
    reg = app.config["PLUGIN_REGISTRY"]
    real_get = reg.get
    art = SimpleNamespace(manifest={"name": "AI art"}, server_module=SimpleNamespace())
    monkeypatch.setattr(
        reg, "get", lambda pid: art if pid == "picture_google_album" else real_get(pid)
    )
    cell = Cell(id="a", plugin="picture_google_album", x=0, y=0, w=10, h=10, options={})
    pages = app.config["PAGE_STORE"]
    pages.save(Page(id="art", name="Civitai Art", device_ids=["tv"], cells=[cell]))
    pages.save(Page(id="clock", name="Clock", device_ids=["frame"]))
    pages.save(Page(id="weather", name="Weather", device_ids=["frame"]))
    pages.save(Page(id="agenda", name="Agenda", device_ids=[]))
    for n, name in enumerate(MORE, 1):
        pages.save(Page(id=f"d{n:02d}", name=name, device_ids=["frame"]))
    app.config["DECK_STORE"].upsert(
        Deck(id="hall", name="Hallway", device_ids=["frame"], advance="timer",
             advance_trigger="cycle", advance_anchor="06:00",
             pages=[DeckPage(page_id="clock")])
    )  # fmt: skip
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


def _open(
    p: Any, base: str, path: str, width: int = 1440, dark: bool = False
) -> tuple[Any, Any, list[str]]:
    browser = _launch(p)
    phone = width < 600
    ctx = browser.new_context(
        viewport={"width": width, "height": 844 if phone else 900},
        color_scheme="dark" if dark else "light",
        has_touch=phone,
        is_mobile=phone,
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # Every dashboard preview: the AI-art one drawn landscape (it was made on the
    # TV), the rest portrait. No renderer needed.
    wide, tall = _png((640, 360), WIDE_RGB), _png((360, 640), TALL_RGB)
    page.route(
        "**/compose/**",
        lambda r: (
            r.fulfill(body=wide if "/art" in r.request.url else tall, content_type="image/png")
            if "/compose/thumbs/" not in r.request.url
            else r.fallback()
        ),
    )
    page.route("**/events/stream**", lambda r: r.fulfill(status=204, body=""))
    page.goto(base + path, wait_until="load")
    return browser, page, errors


def _steps(page: Any) -> list[str]:
    return [r.get_attribute("data-step") for r in page.locator("[data-step]").all()]


def _settle(page: Any) -> None:
    page.wait_for_function("() => [...document.images].every(i => i.complete)")


def _picker(page: Any) -> Any:
    page.locator("[data-picker]").first.click()
    dlg = page.locator("dialog#dxe-picker")
    dlg.wait_for(state="visible")
    return dlg


@pytest.mark.parametrize("width", [1440, 390])
def test_add_in_the_picker_adds_at_once(real: tuple[str, Flask], width: int) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/hall/edit", width)
        dlg = _picker(page)
        ok = dlg.locator("[data-add-confirm]")
        assert ok.is_disabled()  # nothing picked yet
        dlg.locator('[data-add="weather"]').click()
        assert ok.inner_text() == "Add Weather"
        ok.click()
        dlg.wait_for(state="detached")
        # In the lineup at once, no second form, and its tile has the focus.
        assert _steps(page) == ["clock", "weather"]
        assert page.locator("[data-add-confirm]").count() == 0
        page.wait_for_function("() => document.activeElement.dataset.fk === 'sel-weather'")
        page.wait_for_function(
            "() => document.getElementById('dxe-live').textContent === 'Added Weather.'"
        )
        assert page.locator("#dxe-save-state").inner_text() == "Unsaved changes"
        _settle(page)
        assert errors == []
        browser.close()


def test_the_picker_is_a_full_width_sheet_with_big_targets_on_a_phone(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/hall/edit", 390)
        dlg = _picker(page)
        page.wait_for_timeout(250)  # the sheet's rise
        box = dlg.bounding_box()
        assert box["x"] == 0 and box["width"] == 390
        assert abs(box["y"] + box["height"] - 844) <= 1  # sits on the bottom edge
        for sel in (".dxe-pick-opt", "[data-add-confirm]", "button[data-add-cancel]",
                    ".wizard-close", ".dxe-pick-find .dxe-search", ".dxe-pick-cond summary"):  # fmt: skip
            h = dlg.locator(sel).first.bounding_box()["height"]
            assert h >= 44, (sel, h)
        _settle(page)
        assert errors == []
        browser.close()


def test_cancel_escape_and_a_click_outside_add_nothing(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/hall/edit")
        for close in ("Escape", "backdrop", "Cancel", "x"):
            dlg = _picker(page)
            dlg.locator('[data-add="weather"]').click()
            if close == "Escape":
                page.keyboard.press("Escape")
            elif close == "backdrop":
                page.mouse.click(8, 8)
            elif close == "Cancel":
                dlg.get_by_role("button", name="Cancel", exact=True).click()
            else:
                dlg.get_by_role("button", name="Close", exact=True).click()
            dlg.wait_for(state="detached")
            assert _steps(page) == ["clock"], close
            assert page.locator("#dxe-save-state").inner_text() == "All changes saved"
            # Back on what opened it; no half-added form left on the page.
            page.wait_for_function("() => document.activeElement.dataset.fk === 'picker'")
            assert page.locator("[data-add-confirm], #dxe-add-conditions").count() == 0
        _settle(page)
        assert errors == []
        browser.close()


def test_the_keyboard_alone_finds_and_adds_one(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/hall/edit")
        page.locator("[data-picker]").focus()
        page.keyboard.press("Enter")
        dlg = page.locator("dialog#dxe-picker")
        dlg.wait_for(state="visible")
        # A long list opens on its search.
        q = dlg.locator("[data-pick-q]")
        assert page.evaluate("() => document.activeElement.matches('[data-pick-q]')")
        q.press_sequentially("dash")
        assert dlg.locator(".dxe-pick-opt:not([hidden])").count() == len(MORE)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        assert page.evaluate("() => document.activeElement.dataset.add") == "d03"
        page.keyboard.press("ArrowUp")
        assert dlg.locator('[aria-selected="true"]').get_attribute("data-add") == "d02"
        page.keyboard.press("Enter")
        dlg.wait_for(state="detached")
        assert _steps(page) == ["clock", "d02"]
        # Enter in the search picks the first match; a second Enter adds it.
        page.locator("[data-picker]").first.focus()
        page.keyboard.press("Enter")
        dlg.wait_for(state="visible")
        page.keyboard.type("weath")
        assert dlg.locator(".dxe-pick-opt:not([hidden])").count() == 1
        page.keyboard.press("Enter")
        assert dlg.locator('[aria-selected="true"]').get_attribute("data-add") == "weather"
        page.keyboard.press("Enter")
        dlg.wait_for(state="detached")
        assert _steps(page) == ["clock", "d02", "weather"]
        # No match says so.
        _picker(page).locator("[data-pick-q]").fill("zzz")
        assert dlg.locator("[data-pick-none]").is_visible()
        page.keyboard.press("Escape")
        _settle(page)
        assert errors == []
        browser.close()


def test_one_already_in_the_lineup_is_listed_but_cannot_be_added(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/hall/edit")
        dlg = _picker(page)
        taken = dlg.locator('[data-add="clock"]')
        assert taken.get_attribute("aria-disabled") == "true"
        assert "Already in this lineup" in taken.inner_text()
        taken.click(force=True)  # aria-disabled: Playwright would wait for it
        assert dlg.locator("[data-add-confirm]").is_disabled()
        # On another display: listed apart, with a note; Add asks to bind first.
        other = dlg.locator('#dxe-pg-other ~ [data-add="art"]')
        assert "Not on this display (on tv)" in other.inner_text()
        other.click()
        dlg.locator("[data-add-confirm]").click()
        bind = page.locator("dialog.bind-dialog")
        bind.get_by_role("button", name="Cancel").click()
        bind.wait_for(state="detached")
        # Cancelled: still in the picker, the choice kept, nothing added.
        assert dlg.is_visible() and _steps(page) == ["clock"]
        assert dlg.locator('[aria-selected="true"]').get_attribute("data-add") == "art"
        dlg.locator("[data-add-confirm]").click()
        bind.get_by_role("button", name="Bind to frame").click()
        dlg.wait_for(state="detached")
        assert _steps(page) == ["clock", "art"]
        _settle(page)
        assert errors == []
        browser.close()


_FIT = """(sel) => [...document.querySelectorAll(sel)].map((box) => {
    const b = box.getBoundingClientRect();
    const img = box.querySelector('img');
    const i = img ? img.getBoundingClientRect() : null;
    return {w: b.width, h: b.height, fit: img ? getComputedStyle(img).objectFit : '',
            iw: i ? i.width : 0, ih: i ? i.height : 0};
})"""


def _pixels(page: Any, loc: Any) -> list[tuple[int, int, int]]:
    """The colours down a box's middle, inset from its border and clear of a
    badge at its foot: one colour when the picture fills it, the paper above
    and below when a wide picture sits in a band."""
    from PIL import Image

    shot = Image.open(io.BytesIO(loc.screenshot())).convert("RGB")
    w, h = shot.size
    return [
        cast(tuple[int, int, int], shot.getpixel((w // 2, int(h * f))))
        for f in (0.1, 0.3, 0.5, 0.7)
    ]


def _filled(px: list[tuple[int, int, int]]) -> bool:
    mid = px[2]
    return all(all(abs(a - b) <= 12 for a, b in zip(p, mid, strict=True)) for p in px)


@pytest.mark.parametrize("width", [1440, 390])
def test_picker_previews_take_the_displays_shape(real: tuple[str, Flask], width: int) -> None:
    from playwright.sync_api import sync_playwright

    base, _app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/hall/edit", width)
        dlg = _picker(page)
        _settle(page)
        boxes = page.evaluate(_FIT, "#dxe-picker .dxe-th .lu-thumb")
        assert len(boxes) == 4 + len(MORE)  # clock, weather, agenda, art
        for b in boxes:
            # Portrait like the frame, the picture filling it.
            assert abs(b["w"] / b["h"] - FRAME_RATIO) < 0.03, b
            assert b["fit"] == "cover" and b["iw"] == b["w"] and b["ih"] == b["h"], b
        # The AI art's landscape preview is cropped to the frame: its colour from
        # top to bottom, no band of paper above and below it.
        art = dlg.locator('[data-add="art"] .dxe-th .lu-thumb')
        art.scroll_into_view_if_needed()
        assert _filled(_pixels(page, art)), _pixels(page, art)
        page.keyboard.press("Escape")
        assert errors == []
        browser.close()


def test_recent_photos_are_this_displays_and_fill_their_box(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    _sent(app, "art", "tv", (640, 360), 3)
    mine = _sent(app, "art", "frame", (360, 640), 2)
    decks = app.config["DECK_STORE"]
    hall = decks.get("hall")
    decks.upsert(hall.model_copy(update={"pages": [*hall.pages, DeckPage(page_id="art")]}))
    app.config["PAGE_STORE"].save(
        app.config["PAGE_STORE"].get("art").model_copy(update={"device_ids": ["tv", "frame"]})
    )
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/hall/edit")
        page.locator('[data-step="art"] .dxe-sel').click()
        kids = page.locator('[data-panel="art"] .dxe-kids')
        assert kids.locator(".dxe-mono").text_content() == "Recent photos"
        _settle(page)
        srcs = kids.locator(".dxe-it img").evaluate_all("(xs) => xs.map((x) => x.src)")
        # Only what was sent to this frame (newest first), none of the TV's.
        assert [s.rsplit("/", 1)[1].split(".")[0] for s in srcs] == mine[::-1]
        for b in page.evaluate(_FIT, '[data-panel="art"] .dxe-it .dxe-it-pic'):
            assert abs(b["w"] / b["h"] - FRAME_RATIO) < 0.03, b
            assert b["fit"] == "cover", b
        first = kids.locator(".dxe-it .dxe-it-pic").first
        assert _filled(_pixels(page, first)), _pixels(page, first)
        assert errors == []
        browser.close()


def test_recent_photos_from_another_shape_are_cropped_not_banded(
    real: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    # Only ever sent to the landscape TV: those are all there is to show.
    _sent(app, "art", "tv", (640, 360), 2)
    decks = app.config["DECK_STORE"]
    hall = decks.get("hall")
    decks.upsert(hall.model_copy(update={"pages": [*hall.pages, DeckPage(page_id="art")]}))
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/hall/edit")
        page.locator('[data-step="art"] .dxe-sel').click()
        kids = page.locator('[data-panel="art"] .dxe-kids')
        kids.locator(".dxe-it img").first.wait_for()
        _settle(page)
        first = kids.locator(".dxe-it .dxe-it-pic").first
        box = first.bounding_box()
        assert abs(box["width"] / box["height"] - FRAME_RATIO) < 0.03
        assert _filled(_pixels(page, first)), _pixels(page, first)
        assert errors == []
        browser.close()


def test_the_new_lineup_adds_from_the_picker_in_one_step(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser, page, errors = _open(p, base, "/decks/new")
        page.locator('[data-pick-display="frame"]').click()
        page.locator('[data-when="manual"]').click()
        page.locator("[data-goto='3']").click()
        # Continue lands on the way to add one.
        assert page.evaluate("() => document.activeElement.dataset.fk") == "picker"
        dlg = _picker(page)
        _settle(page)
        boxes = page.evaluate(_FIT, "#dxe-picker .dxe-th .lu-thumb")
        assert all(abs(b["w"] / b["h"] - FRAME_RATIO) < 0.03 for b in boxes), boxes
        dlg.locator('[data-add="art"]').click()
        # An optional condition, inside the picker, closed until asked for.
        cond = dlg.locator(".dxe-pick-cond")
        assert cond.get_attribute("open") is None
        cond.locator("> summary").click()
        dlg.get_by_role("button", name="Add condition", exact=True).click()
        dlg.locator(".cp-row").get_by_role("combobox").first.select_option("time_window")
        dlg.locator("[data-add-confirm]").click()
        page.locator("dialog.bind-dialog").get_by_role("button", name="Bind to frame").click()
        dlg.wait_for(state="detached")
        assert _steps(page) == ["art"]
        assert page.locator("[data-create]").is_enabled()
        with page.expect_navigation():
            page.locator("[data-create]").click()
        _settle(page)
        made = [d for d in app.config["DECK_STORE"].all() if d.id != "hall"]
        assert len(made) == 1 and made[0].page_ids == ["art"]
        assert len(made[0].pages[0].conditions) == 1
        assert app.config["PAGE_STORE"].get("art").device_ids == ["frame"]
        assert errors == []
        browser.close()
