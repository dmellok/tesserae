"""Dragging an edge in the custom layout editor, in a live Chromium (#434).

One tall cell on the left and two stacked cells on the right share one line.
Dragging a stacked cell's left edge used to move only that cell, which then
grew over the tall cell; the tall cell kept its old width underneath, so a
calendar in it drew events past the edge that now covered it. The drag has to
move the whole line: the tall cell narrows and both stacked cells widen.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Cell, Page, Panel

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

PANEL_W, PANEL_H = 800, 480


SHARED_LINE = [
    Cell(id="tall", x=0, y=0, w=480, h=480),
    Cell(id="top", x=480, y=0, w=320, h=240),
    Cell(id="bottom", x=480, y=240, w=320, h=240),
]
# The same, with a gap between the tall cell and the stacked ones.
GAP = [
    Cell(id="tall", x=0, y=0, w=400, h=480),
    Cell(id="top", x=480, y=0, w=320, h=240),
    Cell(id="bottom", x=480, y=240, w=320, h=240),
]


@pytest.fixture
def real(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[tuple[str, Flask]]:
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
    app.config["PAGE_STORE"].save(
        Page(
            id="split",
            name="Split",
            panel=Panel(w=PANEL_W, h=PANEL_H),
            cells=[c.model_copy() for c in request.param],
        )
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


def _geometry(app: Flask) -> dict[str, tuple[int, int, int, int]]:
    page = app.config["PAGE_STORE"].get("split")
    return {c.id: (c.x, c.y, c.w, c.h) for c in page.cells}


def _overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _drag_left_edge_of_top_cell(base: str, to_panel_x: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/pages/split", wait_until="domcontentloaded")
        page.evaluate("document.querySelector('details.custom-layout').open = true")
        board = page.locator("[data-layout-board]")
        board.wait_for(state="visible")
        board.scroll_into_view_if_needed()
        # The top-right cell's left handle: the left-side handle nearest
        # the top of the board.
        handle = page.evaluate_handle(
            """() => [...document.querySelectorAll('[data-layout-board] .le-edge--left')]
                .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)[0]"""
        ).as_element()
        assert handle is not None
        hb = handle.bounding_box()
        bb = board.bounding_box()
        assert hb and bb
        start_x = hb["x"] + hb["width"] / 2
        y = hb["y"] + hb["height"] / 4
        target_x = bb["x"] + bb["width"] * to_panel_x / PANEL_W
        with page.expect_response(lambda r: r.url.endswith("/cells/batch")):
            page.mouse.move(start_x, y)
            page.mouse.down()
            for k in range(1, 11):
                page.mouse.move(start_x + (target_x - start_x) * k / 10, y)
            page.mouse.up()
        browser.close()
    assert errors == []


def _assert_no_overlaps(geo: dict[str, tuple[int, int, int, int]]) -> None:
    ids = list(geo)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            assert not _overlaps(geo[a], geo[b]), f"{a} overlaps {b}: {geo}"


@pytest.mark.parametrize("real", [SHARED_LINE], indirect=True)
def test_dragging_a_stacked_cell_edge_narrows_the_tall_cell_beside_it(
    real: tuple[str, Flask],
) -> None:
    base, app = real
    _drag_left_edge_of_top_cell(base, 320)

    geo = _geometry(app)
    assert geo["tall"] == (0, 0, 320, 480)
    assert geo["top"] == (320, 0, 480, 240)
    assert geo["bottom"] == (320, 240, 480, 240)
    _assert_no_overlaps(geo)


@pytest.mark.parametrize("real", [GAP], indirect=True)
def test_a_cell_growing_into_a_gap_stops_at_the_next_cell(real: tuple[str, Flask]) -> None:
    base, app = real
    _drag_left_edge_of_top_cell(base, 320)

    geo = _geometry(app)
    assert geo["tall"] == (0, 0, 400, 480)
    assert geo["top"] == (400, 0, 400, 240)
    assert geo["bottom"] == (480, 240, 320, 240)
    _assert_no_overlaps(geo)
