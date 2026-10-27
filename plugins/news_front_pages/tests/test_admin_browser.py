"""The Newspapers admin page in a live Chromium: paper rows open, switch
off, edit, move, get added and removed, Verify in place, fit a phone, and
do all of it without JavaScript too."""

from __future__ import annotations

import io
import json
import threading
import urllib.error
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from flask import Flask
from PIL import Image

from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]


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

    app = create_app(testing=False, data_root=tmp_path / "data", plugins_dir=REPO_ROOT / "plugins")
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


def _config(app: Flask) -> dict[str, Any]:
    plugin = app.config["PLUGIN_REGISTRY"].get("news_front_pages")
    data: dict[str, Any] = json.loads((Path(plugin.data_dir) / "config.json").read_text())
    return data


def _codes(page: Any) -> list[str]:
    rows = page.locator("[data-nx-row]")
    return [rows.nth(i).locator("[data-nx-code]").input_value() for i in range(rows.count())]


def _open(page: Any) -> list[str]:
    rows = page.locator("[data-nx-row]")
    return [
        rows.nth(i).locator("[data-nx-code]").input_value()
        for i in range(rows.count())
        if rows.nth(i).locator("details").evaluate("el => el.open")
    ]


def test_rows_open_switch_edit_move_and_save(live: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(f"{base}/plugins/news_front_pages/", wait_until="networkidle")
        first = page.locator("[data-nx-row]").first
        code = first.locator("[data-nx-code]")
        assert code.is_hidden()
        # The switch sits on the row's head: it never opens the row, and dims it when off.
        toggle = first.locator("[data-nx-enabled]")
        assert toggle.get_attribute("aria-label") == "NY_NYT: In rotation"
        first.locator(".nx-switch").click()
        assert not toggle.is_checked()
        assert "is-off" in (first.get_attribute("class") or "")
        assert code.is_hidden()
        # The head opens the row from the keyboard; the title follows the code.
        first.locator("summary").focus()
        page.keyboard.press("Enter")
        assert code.is_visible()
        code.fill("wsj2")
        assert first.locator("[data-nx-title]").inner_text() == "WSJ2"
        assert toggle.get_attribute("aria-label") == "WSJ2: In rotation"
        first.locator("[data-nx-css]").fill("filter:   contrast(1.1)")
        assert first.locator("[data-nx-meta]").inner_text() == "filter: contrast(1.1)"
        # Moving the open row saves the edits, and it stays open where it lands.
        with page.expect_navigation():
            first.locator('button[value="down:0"]').click()
        assert _codes(page) == ["WSJ", "WSJ2", "USAT", "AZ_ADS"]
        assert _open(page) == ["WSJ2"]
        assert "Moved WSJ2 down." in page.locator("[data-flash]").first.inner_text()
        assert page.evaluate("location.hash") == "#paper-1"
        # Enter in a field saves (not a row's move or remove).
        page.locator("#new-code").fill("tx_hc")
        with page.expect_navigation():
            page.locator("#new-code").press("Enter")
        assert _codes(page)[-1] == "TX_HC"
        browser.close()
    assert errors == []
    config = _config(app)
    assert config["papers"][1] == {
        "enabled": False,
        "code": "WSJ2",
        "css": "filter:   contrast(1.1)",
    }
    assert [r["code"] for r in config["papers"]] == ["WSJ", "WSJ2", "USAT", "AZ_ADS", "TX_HC"]


def test_a_bad_code_is_marked_and_nothing_saves(live: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{base}/plugins/news_front_pages/", wait_until="networkidle")
        before = _config(app)
        row = page.locator("[data-nx-row]").nth(2)
        row.locator("summary").click()
        row.locator("[data-nx-code]").fill("NY_NYT")
        with page.expect_navigation():
            page.locator("[data-nx-save]").click()
        assert "is in the list already (row 1)" in row.locator(".nx-error").inner_text()
        assert _open(page) == ["NY_NYT"]  # the row with the problem is open
        assert row.locator("[data-nx-code]").input_value() == "NY_NYT"
        assert _config(app) == before
        # Fixed, it saves.
        row.locator("[data-nx-code]").fill("CA_LAT")
        with page.expect_navigation():
            page.locator("[data-nx-save]").click()
        assert _codes(page)[2] == "CA_LAT"
        browser.close()


def test_verify_in_place_and_remove(live: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = live
    buf = io.BytesIO()
    Image.new("L", (700, 1342), 255).save(buf, format="JPEG")
    jpeg = buf.getvalue()

    class Resp(io.BytesIO):
        headers: ClassVar[dict[str, str]] = {}

        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def urlopen(req: Any, timeout: float | None = None) -> Resp:
        url = req if isinstance(req, str) else req.full_url
        if "/lg/USAT.jpg" in url:
            return Resp(jpeg)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))  # type: ignore[arg-type]

    module = app.config["PLUGIN_REGISTRY"].get("news_front_pages").server_module
    with (
        patch("urllib.request.urlopen", urlopen),
        patch.object(module, "rasteriser_available", lambda: False),
        patch.object(module, "directory", lambda _d: {"USAT": "USA Today"}),
        sync_playwright() as p,
    ):
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(f"{base}/plugins/news_front_pages/", wait_until="networkidle")
        row = page.locator("[data-nx-row]").nth(2)
        row.locator("summary").click()
        url = page.url
        row.locator("[data-nx-verify-btn]").click()
        result = row.locator("[data-nx-verify-result]")
        result.locator(".nx-chip").wait_for()
        assert page.url == url  # no reload
        assert result.locator(".nx-chip").inner_text() == "Works"
        assert "USA Today (USAT): downloaded" in result.inner_text()
        assert result.locator("img.nx-thumb").is_visible()
        with page.expect_navigation():
            row.locator('button[value="remove:2"]').click()
        assert _codes(page) == ["NY_NYT", "WSJ", "AZ_ADS"]
        assert "Removed USAT." in page.locator("[data-flash]").first.inner_text()
        browser.close()
    assert errors == []


def test_rows_fit_desktop_and_mobile(live: tuple[str, Flask]) -> None:
    import os

    from playwright.sync_api import sync_playwright

    base, _ = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for width, theme in ((1280, "light"), (390, "light"), (390, "dark")):
            page.emulate_media(color_scheme=theme)
            page.set_viewport_size({"width": width, "height": 950})
            page.goto(f"{base}/plugins/news_front_pages/", wait_until="networkidle")
            rows = page.locator("[data-nx-row]")
            card = page.locator("#papers .dx-section-body").bounding_box()
            first = rows.first
            first.locator("summary").click()
            box = first.bounding_box()
            assert box is not None and card is not None
            assert box["width"] >= card["width"] - 2
            # The head's title and the tools beside it don't overlap.
            title = first.locator("[data-nx-title]").bounding_box()
            tools = first.locator(".nx-row-tools").bounding_box()
            assert title is not None and tools is not None
            assert title["x"] + title["width"] <= tools["x"]
            # An open row's fields use the row's width, under the tools.
            field = first.locator("[data-nx-css]").bounding_box()
            assert field is not None and field["width"] > box["width"] * (
                0.5 if width > 640 else 0.8
            )
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            artifacts = os.environ.get("TESSERAE_UI_ARTIFACT_DIR")
            if artifacts:
                Path(artifacts).mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(Path(artifacts) / f"news_front_pages-{width}-{theme}.png"),
                    full_page=True,
                )
        browser.close()


def test_the_page_works_without_javascript(live: tuple[str, Flask]) -> None:
    """No scripts: rows still open (details), switch off, edit, move, get
    added and removed, and Verify answers on a reloaded page."""
    from playwright.sync_api import sync_playwright

    base, app = live
    module = app.config["PLUGIN_REGISTRY"].get("news_front_pages").server_module

    def verify_code(data_dir: Path, code: str) -> dict[str, Any]:
        return {"ok": False, "tone": "bad", "chip": "Not found", "message": f"No paper {code}."}

    with patch.object(module, "verify_code", verify_code), sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(java_script_enabled=False)
        page = context.new_page()
        # networkidle: leaving a page mid-download of a font leaves the
        # served file to the garbage collector, a ResourceWarning in pytest.
        page.goto(f"{base}/plugins/news_front_pages/", wait_until="networkidle")
        rows = page.locator("[data-nx-row]")
        assert rows.count() == 4
        first = rows.first
        first.locator("summary").click()
        first.locator("[data-nx-code]").fill("ca_lat")
        first.locator(".nx-switch").click()
        with page.expect_navigation():
            page.locator("[data-nx-save]").click()
        page.wait_for_load_state("networkidle")
        assert _codes(page)[0] == "CA_LAT"
        assert not rows.first.locator("[data-nx-enabled]").is_checked()
        # Move: the whole form posts, the order changes.
        with page.expect_navigation():
            rows.nth(1).locator('button[value="up:1"]').click()
        page.wait_for_load_state("networkidle")
        assert _codes(page) == ["WSJ", "CA_LAT", "USAT", "AZ_ADS"]
        # Add, then remove.
        page.locator("#new-code").fill("TX_HC")
        with page.expect_navigation():
            page.locator("[data-nx-add]").click()
        page.wait_for_load_state("networkidle")
        assert _codes(page)[-1] == "TX_HC"
        rows.nth(4).locator("summary").click()
        with page.expect_navigation():
            rows.nth(4).locator('button[value="remove:4"]').click()
        page.wait_for_load_state("networkidle")
        assert _codes(page) == ["WSJ", "CA_LAT", "USAT", "AZ_ADS"]
        # Verify posts the form; the page comes back with the answer, unsaved edits kept.
        rows.nth(2).locator("summary").click()
        rows.nth(2).locator("[data-nx-code]").fill("ZZ_NOPE")
        with page.expect_navigation():
            rows.nth(2).locator("[data-nx-verify-btn]").click()
        page.wait_for_load_state("networkidle")
        assert "No paper ZZ_NOPE." in rows.nth(2).locator("[data-nx-verify-result]").inner_text()
        assert rows.nth(2).locator("[data-nx-code]").input_value() == "ZZ_NOPE"
        assert "Not saved yet" in page.locator("[data-flash]").first.inner_text()
        context.close()
        browser.close()
    assert [r["code"] for r in _config(app)["papers"]] == ["WSJ", "CA_LAT", "USAT", "AZ_ADS"]
