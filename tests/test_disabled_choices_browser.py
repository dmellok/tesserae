"""Disabled choices in both dashboard editors, at desktop and phone width.

A cell on Printer A (no sliced files) shows Toolpaths greyed with its reason,
can't pick it, and warns that the saved value won't work. Switching the cell's
printer to the BETA keeps the current choices until save and reload.
Screenshots land in $DISABLED_CHOICES_SHOTS when set.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import create_app
from app.state.page_store import Cell, Page, Panel
from tests.disabled_choices_fixture import PLUGIN_ID, REASON, write_plugin

_EXE = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
_SHOTS = os.environ.get("DISABLED_CHOICES_SHOTS")


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
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

PAGE_ID = "printers"


@pytest.fixture(scope="module")
def pw() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    root = tmp_path_factory.mktemp("disabled-choices")
    plugins = root / "plugins"
    plugins.mkdir()
    write_plugin(plugins)
    app: Flask = create_app(testing=False, data_root=root / "data", plugins_dir=plugins)
    app.config["TESTING"] = True
    settings = app.config["SETTINGS_STORE"]
    settings.patch_section("app", {"onboarded": True})
    settings.patch_section("auth", {"disabled": True})
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}", app
    finally:
        srv.shutdown()
        thread.join(timeout=10)


def _reset(app: Flask, editor: str) -> None:
    app.config["SETTINGS_STORE"].update_section("ui", {"dashboard_editor": editor})
    app.config["PAGE_STORE"].save(
        Page(
            id=PAGE_ID,
            name="Printers",
            panel=Panel(w=800, h=480),
            cells=[
                Cell(
                    id="big",
                    plugin=PLUGIN_ID,
                    x=0,
                    y=0,
                    w=800,
                    h=480,
                    options={"printer": "alpha", "method": "toolpath", "size": "s"},
                )
            ],
        )
    )


def _open(pw: Any, base: str, editor: str, width: int) -> tuple[Any, Any, list[str], str]:
    browser = pw.chromium.launch(executable_path=_EXE) if _EXE else pw.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/pages/{PAGE_ID}", wait_until="domcontentloaded")
    if editor == "v2":
        page.wait_for_function("() => window.EV2 && EV2.widgets")
        page.evaluate(
            "() => { EV2.steps && EV2.steps.open('widgets'); EV2.widgets.openRow('big'); }"
        )
        prefix = "ev2"
    else:
        page.evaluate(
            "() => document.querySelectorAll('#cell-big details').forEach((d) => (d.open = true))"
        )
        prefix = "cell"
    return browser, page, errors, f"{prefix}-big-opt"


STATE_JS = """
(fid) => {
  const sel = document.getElementById(`${fid}-method`);
  const field = sel.closest('.field');
  const tile = field.querySelector('.pic-opt[data-pic-value="toolpath"]');
  const why = tile.querySelector('.pic-why');
  const warn = field.querySelector('[data-choice-warn]');
  const note = tile.getAttribute('aria-describedby') &&
    document.getElementById(tile.getAttribute('aria-describedby'));
  const shown = (el) => !!el && !el.hidden && el.getClientRects().length > 0;
  const r = why && why.getBoundingClientRect();
  const tr = tile.getBoundingClientRect();
  return {
    value: sel.value,
    disabled: tile.getAttribute('aria-disabled') === 'true',
    why: why ? why.textContent.trim() : null,
    note: note && shown(note) ? note.textContent.trim() : null,
    whyInside: !!why && shown(why) && r.right <= tr.right + 1 && r.bottom <= tr.bottom + 1,
    imgFilter: getComputedStyle(tile.querySelector('img')).filter,
    warn: shown(warn) ? warn.textContent.trim() : null,
    scrollsSideways: document.documentElement.scrollWidth > window.innerWidth + 1,
  };
}
"""


@pytest.mark.parametrize("editor", ["classic", "v2"])
@pytest.mark.parametrize("width", [1280, 500])
def test_a_disabled_choice_is_greyed_and_refreshes_after_save(
    pw: Any, server: Any, editor: str, width: int
) -> None:
    base, app = server
    _reset(app, editor)
    browser, page, errors, fid = _open(pw, base, editor, width)
    try:
        field = page.locator(f"#{fid}-method").locator(
            "xpath=ancestor::div[contains(@class,'field')][1]"
        )
        field.locator("[data-pic-trigger]").scroll_into_view_if_needed()
        field.locator("[data-pic-trigger]").click()
        state = page.evaluate(STATE_JS, fid)
        assert state["disabled"] and state["why"] == "Not available"
        assert state["whyInside"], "the tile says it's not available"
        assert state["note"] == f"Toolpaths. {REASON}", "the full reason shows under the grid"
        assert "grayscale" in state["imgFilter"]
        assert state["warn"] == REASON, "the saved value warns that it won't work"
        assert not state["scrollsSideways"]
        if _SHOTS:
            Path(_SHOTS).mkdir(parents=True, exist_ok=True)
            field.screenshot(path=f"{_SHOTS}/{editor}-{width}-printer-a.png")

        # Clicking the greyed tile picks nothing; a choice that works does.
        # (force: Playwright itself won't click an aria-disabled control)
        field.locator('.pic-opt[data-pic-value="toolpath"]').click(force=True)
        assert page.evaluate(STATE_JS, fid)["value"] == "toolpath"
        field.locator('.pic-opt[data-pic-value="preview"]').click()
        state = page.evaluate(STATE_JS, fid)
        assert state["value"] == "preview" and state["warn"] is None
        # ...and the native select can't go back to the disabled one.
        assert page.evaluate(
            "(fid) => document.querySelector(`#${fid}-method option[value=toolpath]`).disabled",
            fid,
        )

        # Changing the printer leaves the Picture field alone until a save
        # and reload. The user's new Picture selection must survive.
        page.select_option(f"#{fid}-printer", "beta")
        page.wait_for_load_state("networkidle")
        state = page.evaluate(STATE_JS, fid)
        assert state["disabled"], "availability refreshes only when fields render again"
        assert state["value"] == "preview"
        assert (
            "Availability depends on the printer. Save and reopen to refresh."
            in field.text_content()
        )
        if editor == "v2":
            page.evaluate("() => EV2.flush()")
        else:
            with page.expect_response(f"{base}/pages/{PAGE_ID}/cells/big"):
                page.locator(f"#{fid}-printer").evaluate("el => el.form.requestSubmit()")
        assert errors == []
        browser.close()
        browser, page, errors, fid = _open(pw, base, editor, width)
        state = page.evaluate(STATE_JS, fid)
        assert state["value"] == "preview"
        assert not state["disabled"]
        assert state["why"] is None and state["note"] is None and state["warn"] is None
        field = page.locator(f"#{fid}-method").locator(
            "xpath=ancestor::div[contains(@class,'field')][1]"
        )
        field.locator("[data-pic-trigger]").click()
        field.locator('.pic-opt[data-pic-value="toolpath"]').click()
        assert page.evaluate(STATE_JS, fid)["value"] == "toolpath"
        if _SHOTS:
            field.locator("[data-pic-trigger]").click()
            field.screenshot(path=f"{_SHOTS}/{editor}-{width}-beta.png")

        # Back to Printer A: the open choices and current value stay put.
        page.select_option(f"#{fid}-printer", "alpha")
        page.wait_for_load_state("networkidle")
        assert not page.evaluate(STATE_JS, fid)["disabled"]
        assert page.evaluate(STATE_JS, fid)["value"] == "toolpath"
        assert errors == []
    finally:
        browser.close()


@pytest.mark.parametrize("editor", ["classic", "v2"])
def test_a_plain_dropdown_lists_a_disabled_choice_with_its_reason(
    pw: Any, server: Any, editor: str
) -> None:
    base, app = server
    _reset(app, editor)
    browser, page, errors, fid = _open(pw, base, editor, 500)
    try:
        opt = page.evaluate(
            """(fid) => {
              const o = document.querySelector(`#${fid}-size option[value=l]`);
              return {disabled: o.disabled, text: o.textContent, title: o.title};
            }""",
            fid,
        )
        assert opt == {
            "disabled": True,
            "text": "Large (Too big for this display)",
            "title": "Too big for this display.",
        }
        assert errors == []
    finally:
        browser.close()
