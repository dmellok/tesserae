"""History rows: a 12-hour time (``10:15:06 AM``) fits its column and never
runs under the thumbnail, in a live Chromium (layout only a browser can check)."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Page, Panel

_EXE = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(_EXE or p.chromium.executable_path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium not installed"
)


@pytest.fixture(params=["en-US", "en-GB"])
def live_server(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    from werkzeug.serving import make_server

    app: Flask = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True, "locale": request.param})
    store.patch_section("auth", {"disabled": True})
    app.config["PAGE_STORE"].save(Page(id="news", name="Newspaper", panel=Panel(w=800, h=480)))
    app.config["EVENT_LOG"].record(
        type="push",
        source="deck",
        target="news",
        status="sent",
        digest="ab" * 8,
        extra={"device_ids": []},
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


@pytest.mark.parametrize("paper", [False, True])
def test_the_time_ends_before_the_next_column(live_server: str, paper: bool) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        if paper:
            ctx.add_init_script("localStorage.setItem('tesserae-ui', 'paper')")
        pg = ctx.new_page()
        pg.goto(f"{live_server}/history")
        pg.wait_for_selector(".dx-hist-row .dx-hist-time")
        fits = pg.evaluate(
            """() => {
              const t = document.querySelector('.dx-hist-row .dx-hist-time');
              const next = t.nextElementSibling;
              const tr = t.getBoundingClientRect();
              // Text wider than its box would overflow into the next column.
              return {overflow: t.scrollWidth - t.clientWidth,
                      gap: next ? next.getBoundingClientRect().left - tr.right : 99,
                      text: t.textContent};
            }"""
        )
        browser.close()
    assert fits["overflow"] <= 0, fits
    assert fits["gap"] >= 4, fits
