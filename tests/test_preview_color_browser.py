"""Thumbnail color filters in a live browser (Playwright Chromium).

display_color.py decides the mode and the templates stamp it on the media;
this checks the other half, static/style/preview-color.css and the shared
SVG threshold filter: grey and dithered-mono media go greyscale, only
dither-none mono thresholds to black and white, color and already-quantised
panel images are left alone, and the lightbox copy keeps its thumbnail's mode.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.main import REPO_ROOT


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

# Left half red, right half white: a threshold turns red black, a greyscale
# keeps it mid-grey, and color leaves it red.
_SRC = (
    "data:image/svg+xml,"
    '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
    '<path fill="red" d="M0 0h10v20H0z"/><path fill="white" d="M10 0h10v20H10z"/></svg>'
)


@pytest.fixture
def page(tmp_path: Path) -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    css = (REPO_ROOT / "static" / "style" / "preview-color.css").read_text(encoding="utf-8")
    svg = (REPO_ROOT / "templates" / "_preview_filters.html").read_text(encoding="utf-8")
    script = (REPO_ROOT / "static" / "components.js").read_text(encoding="utf-8")
    cells = "".join(
        f'<div id="{name}" {attrs}><img width="20" height="20" src=\'{_SRC}\' {img}></div>'
        for name, attrs, img in [
            ("grey", 'data-gamut="grey16" data-grey-levels="16"', ""),
            ("mono-dither", 'data-gamut="mono-dither" data-grey-levels="2"', ""),
            ("mono", 'data-gamut="mono" data-grey-levels="2"', ""),
            ("color", 'data-gamut="color" data-grey-levels="0"', ""),
            ("quantised", 'data-gamut="mono" data-grey-levels="2"', "data-device-quantised"),
        ]
    )
    lightbox = (
        f'<a id="lb" href=\'{_SRC}\' data-gamut="mono" data-grey-levels="2" data-lightbox>'
        f"<img width=20 height=20 src='{_SRC}'></a>"
    )
    path = tmp_path / "preview.html"
    path.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{css}</style></head><body>{svg}{cells}{lightbox}"
        f"<script>{script}</script></body></html>",
        encoding="utf-8",
    )
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(path.as_uri())
        pg.wait_for_function("[...document.images].every((i) => i.complete)")
        yield pg
        b.close()


def test_filters_follow_the_mode_and_skip_quantised_media(page: Any) -> None:
    filters = page.evaluate(
        "() => Object.fromEntries([...document.querySelectorAll('body > div')]"
        ".map((d) => [d.id, getComputedStyle(d.querySelector('img')).filter]))"
    )
    assert filters["grey"] == "grayscale(1)"
    assert filters["mono-dither"] == "grayscale(1)"
    assert filters["mono"] == 'url("#tess-mono")'
    assert filters["color"] == "none"
    assert filters["quantised"] == "none"


def _pixels(page: Any, selector: str) -> Any:
    from PIL import Image

    return Image.open(io.BytesIO(page.locator(selector).screenshot())).convert("RGB")


def test_mono_threshold_and_dithered_mono_grey_paint_as_expected(page: Any) -> None:
    mono = _pixels(page, "#mono img")
    assert mono.getpixel((5, 10)) == (0, 0, 0)
    assert mono.getpixel((15, 10)) == (255, 255, 255)
    # A dithered 1-bit frame previews as grey, not as hard black and white.
    r, g, b = _pixels(page, "#mono-dither img").getpixel((5, 10))
    assert r == g == b and 0 < r < 255


def test_lightbox_keeps_the_thumbnail_mode(page: Any) -> None:
    page.click("#lb")
    img = page.locator(".lightbox-img")
    assert img.get_attribute("data-gamut") == "mono"
    assert img.get_attribute("data-grey-levels") == "2"
