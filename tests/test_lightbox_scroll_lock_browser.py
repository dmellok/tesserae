"""Lightbox: page scroll lock and fit / full-size toggle (live Chromium).

While the shared lightbox is open the page behind it must not scroll (wheel,
keyboard), the layout must not shift when the scrollbar disappears, and every
close path must release the lock. The image opens fit to the screen; a click
toggles a natural-size view that pans inside the overlay.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.main import REPO_ROOT

_EXE = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
_SHOTS = Path("/tmp/lightbox-lock")


def _launch(p: Any) -> Any:
    return p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()


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


@pytest.fixture
def page_url(tmp_path: Path) -> str:
    from PIL import Image

    Image.new("RGB", (2400, 1600), (30, 90, 160)).save(tmp_path / "big.png")
    Image.new("RGB", (120, 80), (160, 90, 30)).save(tmp_path / "small.png")
    style = REPO_ROOT / "static" / "style"
    html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<link rel="stylesheet" href="{(style / "base.css").as_uri()}">'
        f'<link rel="stylesheet" href="{(style / "shell.css").as_uri()}">'
        "<style>::-webkit-scrollbar{width:15px;background:#ccc}"
        "::-webkit-scrollbar-thumb{background:#666}</style></head><body>"
        '<main id="m" style="height:4000px;padding:20px">'
        '<a id="big" href="big.png" data-lightbox><span id="probe">open big</span></a> '
        '<a id="small" href="small.png" data-lightbox>open small</a></main>'
        f'<script src="{(REPO_ROOT / "static" / "components.js").as_uri()}"></script>'
        "</body></html>"
    )
    path = tmp_path / "page.html"
    path.write_text(html, encoding="utf-8")
    return path.as_uri()


@pytest.fixture
def page(page_url: str) -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        pg = browser.new_page(viewport={"width": 900, "height": 600})
        pg.goto(page_url)
        pg.evaluate("window.scrollTo(0, 300)")
        yield pg
        browser.close()


def _open(pg: Any, which: str = "big") -> None:
    # JS click: Playwright's own click would scroll the link back into view.
    pg.evaluate(f"document.querySelector('#{which}').click()")
    pg.wait_for_selector(".lightbox-img")
    pg.wait_for_function("document.querySelector('.lightbox-img').complete")
    pg.wait_for_timeout(150)


def _locked(pg: Any) -> bool:
    return pg.evaluate("getComputedStyle(document.documentElement).overflow") == "hidden"


def _rect(pg: Any, sel: str = ".lightbox-img") -> dict[str, float]:
    return pg.evaluate(f"document.querySelector('{sel}').getBoundingClientRect().toJSON()")


def _closed(pg: Any) -> None:
    pg.wait_for_function("!document.querySelector('.lightbox')")
    assert not _locked(pg)
    assert pg.evaluate("window.scrollY") == 300
    assert pg.evaluate("document.documentElement.style.paddingRight") == ""


def test_page_does_not_scroll_behind_and_layout_holds(page: Any) -> None:
    before = _rect(page, "#probe")
    _open(page)
    assert _locked(page)
    page.mouse.move(450, 300)
    page.mouse.wheel(0, 800)
    page.keyboard.press("Space")
    page.keyboard.press("PageDown")
    page.wait_for_timeout(150)
    assert page.evaluate("window.scrollY") == 300
    assert _rect(page, "#probe") == before


@pytest.mark.parametrize("how", ["escape", "backdrop", "button"])
def test_every_close_path_restores_page(page: Any, how: str) -> None:
    _open(page)
    if how == "escape":
        page.keyboard.press("Escape")
    elif how == "backdrop":
        page.mouse.click(4, 4)
    else:
        page.click(".lightbox-close")
    _closed(page)


def test_reopen_leaves_no_stuck_lock(page: Any) -> None:
    for _ in range(2):
        _open(page)
        page.keyboard.press("Escape")
        _closed(page)
    _open(page)
    _open(page)  # a second open while one is showing
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    _closed(page)
    assert page.evaluate("window.TesseraeScrollLock.unlock()") is None
    assert not _locked(page)


def test_fit_large_fit_cycle(page: Any) -> None:
    _SHOTS.mkdir(exist_ok=True)
    _open(page)
    vw, vh = 900, 600
    r = _rect(page)
    assert r["x"] >= 0 and r["y"] >= 0 and r["x"] + r["width"] <= vw and r["y"] + r["height"] <= vh
    assert r["width"] / r["height"] == pytest.approx(1.5, abs=0.02)
    assert (
        page.evaluate("getComputedStyle(document.querySelector('.lightbox-img')).cursor")
        == "zoom-in"
    )
    page.screenshot(path=str(_SHOTS / "fit.png"))

    page.click(".lightbox-img")
    big = _rect(page)
    assert big["width"] == pytest.approx(2400, abs=1) and big["height"] == pytest.approx(
        1600, abs=1
    )
    assert (
        page.evaluate("getComputedStyle(document.querySelector('.lightbox-img')).cursor")
        == "zoom-out"
    )
    assert page.evaluate(
        "(o => o.scrollWidth > o.clientWidth && o.scrollHeight > o.clientHeight)(document.querySelector('.lightbox'))"
    )
    assert _locked(page)
    page.mouse.move(450, 300)
    page.mouse.wheel(0, 400)
    page.wait_for_timeout(150)
    assert page.evaluate("document.querySelector('.lightbox').scrollTop") > 0
    assert page.evaluate("window.scrollY") == 300
    page.screenshot(path=str(_SHOTS / "large.png"))

    page.click(".lightbox-img")  # toggles back, does not close
    assert page.query_selector(".lightbox") is not None
    r2 = _rect(page)
    assert r2["width"] == pytest.approx(r["width"], abs=1)
    assert page.evaluate("document.querySelector('.lightbox').scrollTop") == 0


def test_keyboard_toggles_without_scrolling_page(page: Any) -> None:
    _open(page)
    page.keyboard.press("Enter")
    assert page.evaluate("document.querySelector('.lightbox').classList.contains('is-large')")
    page.keyboard.press(" ")
    assert not page.evaluate("document.querySelector('.lightbox').classList.contains('is-large')")
    assert page.evaluate("window.scrollY") == 300


def test_escape_closes_from_large(page: Any) -> None:
    _open(page)
    page.click(".lightbox-img")
    page.keyboard.press("Escape")
    _closed(page)


def test_backdrop_closes_only_in_fit_state(page: Any) -> None:
    _open(page)
    page.click(".lightbox-img")
    page.mouse.click(4, 4)  # padding area, large view: no close
    assert page.query_selector(".lightbox") is not None
    page.click(".lightbox-img")
    page.mouse.click(4, 4)
    _closed(page)


def test_reopen_resets_to_fit_and_small_image_has_no_zoom(page: Any) -> None:
    _open(page)
    page.click(".lightbox-img")
    page.keyboard.press("Escape")
    _closed(page)
    _open(page)
    assert not page.evaluate("document.querySelector('.lightbox').classList.contains('is-large')")
    page.keyboard.press("Escape")
    _closed(page)
    _open(page, "small")
    img = _rect(page)
    assert img["width"] == pytest.approx(120, abs=1)
    page.click(".lightbox-img")
    assert not page.evaluate("document.querySelector('.lightbox').classList.contains('is-large')")
    assert (
        page.evaluate("getComputedStyle(document.querySelector('.lightbox-img')).cursor")
        != "zoom-in"
    )
