"""The Comics admin page in a live Chromium: it fits light, dark and phone
widths, Import URL sends the address as typed and turns the strip manual,
every change works with JavaScript switched off, and the plugin's script
only adds niceties (the preview picker, a question before a remove)."""

from __future__ import annotations

import io
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from PIL import Image

from app.main import create_app


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium not installed"
)


@pytest.fixture
def live(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    plugins_dir = Path(__file__).resolve().parents[1] / "plugins"
    app = create_app(testing=False, data_root=tmp_path / "data", plugins_dir=plugins_dir)
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("L", (600, 200), 255).save(buf, format="PNG")
    return buf.getvalue()


def _config(app: Flask) -> dict[str, Any]:
    plugin = app.config["PLUGIN_REGISTRY"].get("comics_daily")
    values = plugin.server_module.config_store(plugin.data_dir).read()
    assert values is not None
    return dict(values)


def test_comics_admin_fits_and_imports_the_typed_url(
    live: tuple[str, Flask], monkeypatch: pytest.MonkeyPatch
) -> None:
    from playwright.sync_api import sync_playwright

    from app import net_guard

    base, app = live
    asked: list[str] = []

    def fake_fetch(url: str, **kw: Any) -> tuple[bytes, str]:
        asked.append(url)
        return _png(), "image/png"

    monkeypatch.setattr(net_guard, "fetch_bytes", fake_fetch)
    plugin = app.config["PLUGIN_REGISTRY"].get("comics_daily")
    store = plugin.server_module.images.Store(Path(plugin.data_dir))
    for n in range(40):
        store.append_log({"at": 1.0e9 + n, "source": "Garfield", "outcome": "ok", "text": f"n{n}"})
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        for width, theme in ((1280, "light"), (390, "light"), (390, "dark")):
            page.emulate_media(color_scheme=theme)
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(f"{base}/plugins/comics_daily/", wait_until="networkidle")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            rows = page.locator("#gocomics .cx-row")
            assert rows.count() == 4
            garfield = page.locator("#strip-garfield")
            garfield.locator("summary").click()
            assert garfield.locator('input[name="image_url"]').is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # The log opens on its newest line.
        log = page.locator(".cx-log")
        assert log.evaluate("el => el.scrollHeight - el.scrollTop - el.clientHeight < 2")
        # Dilbert is built in: it can be switched off, but not removed or renamed.
        dilbert = page.locator("#strip-dilbert")
        assert dilbert.locator(".cx-remove").count() == 0
        assert dilbert.locator('input[name="slug"]').get_attribute("readonly") is not None
        # Import URL takes the address as typed in the strip's edit form.
        garfield.locator('input[name="image_url"]').fill(
            "https://pictures.example.org/garfield.png"
        )
        with page.expect_navigation():
            garfield.get_by_role("button", name="Import URL").click()
        page.wait_for_load_state("networkidle")
        assert page.evaluate("location.hash") == "#strip-garfield"
        browser.close()
    assert errors == []
    assert asked == ["https://pictures.example.org/garfield.png"]
    garfield_row = next(r for r in _config(app)["strips"] if r["slug"] == "garfield")
    assert garfield_row["mode"] == "upload"
    assert garfield_row["image_url"] == "https://pictures.example.org/garfield.png"


def test_every_change_works_without_javascript(live: tuple[str, Flask]) -> None:
    """With scripts off: move, switch off, edit and remove strips, save the
    Far Side and Layout cards, and pick the preview's screen. (Clicks are
    forced: Playwright's wait for the element to hold still stalls on a
    page with scripts off.)"""
    from playwright.sync_api import sync_playwright

    base, app = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            java_script_enabled=False, viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        url = f"{base}/plugins/comics_daily/"
        page.goto(url)

        def press(locator: Any, anchor: str) -> None:
            with page.expect_navigation():
                locator.click(force=True)
            assert page.url.endswith(f"/plugins/comics_daily/#{anchor}")
            # Back at the top without the saved note, which only a script
            # dismisses and which may sit over the next button.
            page.goto(url)

        press(
            page.locator("#strip-garfield").get_by_role("button", name="Move Garfield up"),
            "strip-garfield",
        )
        assert page.locator("#gocomics .cx-row").first.get_attribute("id") == "strip-garfield"
        press(
            page.locator("#strip-dilbert").get_by_role("button", name="Switch Dilbert off"),
            "strip-dilbert",
        )
        assert (
            page.locator("#strip-dilbert").get_by_role("button", name="Switch Dilbert on").count()
        )
        calvin = page.locator("#strip-calvinandhobbes")
        calvin.locator("summary").click(force=True)  # a details element: no script needed
        calvin.locator('input[name="label"]').fill("Calvin")
        press(calvin.get_by_role("button", name="Save strip"), "strip-calvinandhobbes")
        press(
            page.locator("#strip-pearlsbeforeswine").get_by_role("button", name="Remove"),
            "gocomics",
        )
        farside = page.locator("#farside")
        farside.get_by_text("2", exact=True).click(force=True)
        farside.locator('label[for="cx-captions"].dx-toggle-switch').click(force=True)
        press(farside.get_by_role("button", name="Save"), "farside")
        assert page.locator('input[name="far_side_count"][value="2"]').is_checked()
        layout = page.locator("#layout")
        layout.locator("#cx-gap").fill("40")
        layout.locator("#cx-small_screens").select_option("one")
        press(layout.get_by_role("button", name="Save"), "layout")
        # The preview's picker: its Show button picks a screen.
        page.select_option("#cx-screen-pick", label="7.5-inch (800×480)")
        with page.expect_navigation():
            page.get_by_role("button", name="Show").click(force=True)
        assert "screen=2" in page.url
        shown = page.locator(".cx-screen[data-screen]:not([hidden])")
        assert shown.count() == 1 and shown.get_attribute("data-screen") == "2"
        context.close()
        browser.close()
    values = _config(app)
    assert [r["slug"] for r in values["strips"]] == ["garfield", "dilbert", "calvinandhobbes"]
    assert [r["enabled"] for r in values["strips"]] == [True, False, True]
    assert values["strips"][2]["label"] == "Calvin"
    assert (values["far_side_count"], values["captions"]) == (2, False)
    assert (values["gap"], values["small_screens"]) == (40, "one")


def test_removing_a_strip_asks_first(live: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{base}/plugins/comics_daily/", wait_until="networkidle")
        answers = iter([False, True])
        asked: list[str] = []

        def answer(dialog: Any) -> None:
            asked.append(dialog.message)
            if next(answers):
                dialog.accept()
            else:
                dialog.dismiss()

        page.on("dialog", answer)
        remove = page.locator("#strip-garfield").get_by_role("button", name="Remove Garfield")
        remove.click()
        page.wait_for_timeout(200)
        assert page.locator("#strip-garfield").count() == 1
        with page.expect_navigation():
            remove.click()
        page.wait_for_load_state("networkidle")
        assert page.locator("#strip-garfield").count() == 0
        browser.close()
    assert asked == ["Remove Garfield from the list?"] * 2
    assert "garfield" not in [r["slug"] for r in _config(app)["strips"]]


def _seed_strip(app: Flask) -> None:
    import time as _time

    plugin = app.config["PLUGIN_REGISTRY"].get("comics_daily")
    module = plugin.server_module
    store = module.images.Store(Path(plugin.data_dir))
    buf = io.BytesIO()
    Image.new("L", (1200, 380), 255).save(buf, format="PNG")
    picture = module.images.normalise(buf.getvalue())
    name = store.add("gocomics:garfield", picture)
    today = module._today().isoformat()
    store.update(
        "gocomics:garfield",
        lambda e: e.update(
            pictures=[{"file": name, "w": 1200, "h": 380}],
            edition=today,
            fetched_on=today,
            last_success=_time.time(),
            last_attempt=_time.time(),
            error=None,
        ),
    )
    strips = [
        {"enabled": False, "label": "", "slug": "dilbert", "mode": "upload", "image_url": ""},
        {"enabled": True, "label": "", "slug": "garfield", "mode": "auto", "image_url": ""},
    ]
    module.config_store(plugin.data_dir).update(
        lambda values: {**values, "far_side": False, "strips": strips}
    )


def test_a_sliver_of_a_cell_says_it_is_too_small_rather_than_clip(
    live: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = live
    _seed_strip(app)
    probe = """() => {
      const host = [...document.querySelectorAll('*')].find(
        (el) => el.shadowRoot && el.shadowRoot.querySelector('.rows'));
      const sr = host && host.shadowRoot;
      if (!sr) return null;
      const img = [...sr.querySelectorAll('.row')].find((r) => !r.hidden);
      const box = sr.querySelector('.rows').getBoundingClientRect();
      const r = img ? img.getBoundingClientRect() : null;
      return {
        small: sr.querySelector('.too-small')?.textContent || '',
        inside: r ? (r.top >= box.top - 1 && r.bottom <= box.bottom + 1) : null,
      };
    }"""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 800, "height": 300})
        page.goto(f"{base}/_test/render?plugin=comics_daily&w=800&h=300", wait_until="networkidle")
        page.wait_for_timeout(1800)
        normal = page.evaluate(probe)
        page.set_viewport_size({"width": 800, "height": 50})
        page.goto(f"{base}/_test/render?plugin=comics_daily&w=800&h=50", wait_until="networkidle")
        page.wait_for_timeout(1800)
        sliver = page.evaluate(probe)
        browser.close()
    assert normal == {"small": "", "inside": True}
    assert sliver["small"] == "Too small a space for a comic."


def test_the_screen_dropdown_swaps_the_preview_without_a_reload(
    live: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, _ = live
    shown = """() => [...document.querySelectorAll('.cx-screen[data-screen]')]
        .filter((f) => !f.hidden && f.getBoundingClientRect().height > 0)
        .map((f) => f.dataset.screen)"""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{base}/plugins/comics_daily/", wait_until="networkidle")
        page.evaluate("window.__stay = 1")
        # No screen of the user's shows comics: the first reference screen.
        first = page.evaluate(shown)
        label = page.locator("#cx-screen-pick option:checked").inner_text()
        page.select_option("#cx-screen-pick", label="7.5-inch (800×480)")
        after = page.evaluate(shown)
        stayed = page.evaluate("window.__stay === 1")
        # Beside the list on a desktop, kept in view while the list scrolls.
        aside = page.evaluate(
            """() => { const a = document.querySelector('.cx-onscreen').getBoundingClientRect();
                const l = document.querySelector('.cx-preview').getBoundingClientRect();
                return {left: a.right <= l.left, sticky: getComputedStyle(
                  document.querySelector('.cx-onscreen')).position === 'sticky'}; }"""
        )
        # On a phone the two stack, the preview first.
        page.set_viewport_size({"width": 420, "height": 900})
        stacked = page.evaluate(
            """() => document.querySelector('.cx-onscreen').getBoundingClientRect().bottom
                <= document.querySelector('.cx-preview').getBoundingClientRect().top"""
        )
        browser.close()
    assert label == "32-inch portrait (1440×2560)" and first == ["0"]
    assert after == ["2"] and stayed
    assert aside == {"left": True, "sticky": True}
    assert stacked
