"""Webpage widget: a scaled page fills its cell, and can be shown whole (#609).

The iframe is made larger than the cell and scaled down. The widget wrapper is
a column flexbox, which squeezed the iframe back to the cell's height before
the scale was applied: at a scale of 0.64 the page only covered the top 64% of
the cell and lost everything below that.

``Fit`` gives the page its render width and scales that to the cell's width;
its height is whatever is left. With a render height the page is scaled until
that fits as well, so a page taller than the cell is shown whole.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CLIENT = _ROOT / "plugins" / "webpage" / "client.js"
_CSS = _ROOT / "static" / "style" / "spectra-widgets.css"


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


requires_chromium = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium is not installed"
)

# A 320x192 cell, as in the report, holding a snippet 600 px tall whose last
# line sits at the very bottom.
_PAGE = """<!doctype html><html><body style="margin:0">
<div id="cell" style="width:320px;height:192px;overflow:hidden;position:relative"></div>
<script type="module">
  import render, { frameGeometry } from "/client.js";
  window.frameGeometry = frameGeometry;
  window.mount = async (options) => {
    const cell = document.getElementById("cell");
    cell.innerHTML = "";
    const host = document.createElement("div");
    host.style.cssText = "width:100%;height:100%";
    cell.append(host);
    const shadow = host.attachShadow({ mode: "open" });
    await render(shadow, { cell: { w: 320, h: 192, options } });
    // The widget stylesheet gives the wrapper its height; measure once it is in.
    await Promise.all([...shadow.querySelectorAll("link")].map((l) =>
      l.sheet ? null : new Promise((r) => { l.onload = r; l.onerror = r; })));
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const frame = shadow.querySelector("iframe").getBoundingClientRect();
    return { left: frame.left, top: frame.top, width: frame.width, height: frame.height };
  };
  window.ready = true;
</script></body></html>"""

_SNIPPET = (
    '<div style="height:600px;position:relative">'
    '<p id="last" style="position:absolute;bottom:0;margin:0">last line</p></div>'
)


def _mount(options: dict[str, object]) -> dict[str, float]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route(
                "**/client.js",
                lambda route: route.fulfill(
                    body=_CLIENT.read_text(encoding="utf-8"),
                    content_type="text/javascript",
                ),
            )
            # The widget stylesheet gives the cell's wrapper its height.
            page.route(
                "**/static/style/spectra-widgets.css",
                lambda route: route.fulfill(
                    body=_CSS.read_text(encoding="utf-8"), content_type="text/css"
                ),
            )
            page.route(
                "**/page.html", lambda route: route.fulfill(body=_PAGE, content_type="text/html")
            )
            page.goto("http://webpage.test/page.html")
            page.wait_for_function("window.ready === true")
            base = {"source": "html", "html": _SNIPPET, "settle_seconds": 0}
            box = page.evaluate("(o) => window.mount(o)", {**base, **options})
            # Where the snippet's last line lands, in the outer page's pixels.
            inner = page.frames[-1].evaluate(
                "() => document.getElementById('last').getBoundingClientRect().bottom"
            )
            scale = box["height"] / page.frames[-1].evaluate("() => window.innerHeight")
            box["last_bottom"] = box["top"] + inner * scale
            return box
        finally:
            browser.close()


@requires_chromium
def test_a_scaled_page_covers_the_whole_cell() -> None:
    box = _mount({"scale": "fit", "viewport_w": 500})
    assert box["width"] == pytest.approx(320, abs=1)
    assert box["height"] == pytest.approx(192, abs=1)  # was 123: squeezed, then scaled
    assert box["last_bottom"] > 192  # 600 px at 0.64 is still taller than the cell


@requires_chromium
def test_a_percentage_scale_covers_the_whole_cell() -> None:
    box = _mount({"scale": "50"})
    assert box["width"] == pytest.approx(320, abs=1)
    assert box["height"] == pytest.approx(192, abs=1)


@requires_chromium
def test_a_render_height_shows_the_whole_page() -> None:
    box = _mount({"scale": "fit", "viewport_w": 500, "viewport_h": 600})
    assert box["height"] == pytest.approx(192, abs=1)
    assert box["last_bottom"] <= 192.5
    # 500 px at the height's scale (0.32) is 160 wide, centred in the 320 cell
    assert box["width"] == pytest.approx(160, abs=1)
    assert box["left"] == pytest.approx(80, abs=1)


@requires_chromium
def test_a_render_height_shorter_than_the_cell_still_fills_it() -> None:
    box = _mount({"scale": "fit", "viewport_w": 500, "viewport_h": 100})
    assert box["width"] == pytest.approx(320, abs=1)
    assert box["height"] == pytest.approx(192, abs=1)
