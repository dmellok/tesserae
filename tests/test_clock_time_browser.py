"""In a live Chromium: the browser's ``clockTime`` writes the same text as the
server's formatter (:mod:`app.clock_text`) in both clock styles."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.clock_text import clock_of
from app.main import REPO_ROOT, create_app

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
def served(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
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
    store.patch_section("app", {"onboarded": True, "timezone": "UTC"})
    store.patch_section("auth", {"disabled": True})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


@pytest.mark.parametrize(("locale", "cycle"), [("en-US", "h12"), ("en-GB", "h23")])
def test_browser_clock_matches_the_server_formatter(
    served: tuple[str, Flask], locale: str, cycle: str
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = served
    app.config["SETTINGS_STORE"].patch_section("app", {"locale": locale})
    moments = [
        datetime(2026, 10, 30, h, m, s)
        for h, m, s in [(0, 0, 0), (7, 0, 0), (12, 0, 30), (15, 48, 9), (23, 59, 59)]
    ]
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page()
        page.goto(f"{base}/settings/devices")
        assert page.evaluate("window.TESSERAE_TIME_CYCLE") == cycle
        got = page.evaluate(
            """(parts) => parts.map(([y, mo, d, h, mi, s]) => {
                 const date = new Date(y, mo - 1, d, h, mi, s);
                 const c = window.tesseraeComponents.clockTime;
                 return [c(date), c(date, { seconds: true }), c(date, { date: true })];
               })""",
            [[m.year, m.month, m.day, m.hour, m.minute, m.second] for m in moments],
        )
        browser.close()
    for moment, (plain, secs, dated) in zip(moments, got, strict=True):
        assert plain == clock_of(moment, cycle)
        assert secs == clock_of(moment, cycle, seconds=True)
        assert dated == f"Oct 30 {clock_of(moment, cycle)}"
