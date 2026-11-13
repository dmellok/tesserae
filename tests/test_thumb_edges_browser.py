"""No thumbnail, on any page, leaves a strip of its own background along an edge.

A thumbnail is a box in the shape of the display, holding a picture of that
shape. When the border sat on the box, it took 1-2 px off both sides of the
content, the picture no longer fitted what was left, and ``object-fit: contain``
showed the box's light background as a thin line next to the border. That is
visible on a dark picture, or a light picture in the dark theme, at device scale
factor 2. Every thumbnail now draws its border over the picture
(``base.css``, "Thumbnail frames"), so the picture's box is the frame's box.

Each surface is checked two ways, at scale factor 2, in both themes and both
designs, with a picture of the opposite tone to the box's background:

* the layout: the picture, as ``object-fit`` lays it out, covers the frame's
  inside to within 0.5 css px, and the frame has the display's own ratio;
* the pixels: the first picture pixel inside the border on each side is the
  picture's colour, not the box's."""

from __future__ import annotations

import io
import os
import re
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from tests.device_page_kinds import _register, build_app
from tests.test_lineups_browser import PAGE as LINEUPS_PAGE
from tests.test_lineups_browser import _Api
from tests.test_lineups_browser import live as lineups_live  # noqa: F401

_EXE = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None


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
    # The browser may close while the server is still sending a picture.
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

#: The tone of the picture on each theme: the opposite of the box's background,
#: so any sliver of the box shows up against it.
_DARK_PICTURE = (16, 16, 16)
_LIGHT_PICTURE = (240, 240, 240)
_SCALE = 2


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    from app.state.page_store import Page

    app = build_app(tmp_path_factory.mktemp("thumb-edges"), live=True)
    client = app.test_client()
    # A 4K television, and a dashboard it shares with a portrait display.
    _register(app, client, "tv", "esp32_client", panel_w=3840, panel_h=2160)
    app.config["PAGE_STORE"].save(
        Page(id="icloud", name="icloud photos", device_ids=["tv", "reader"])
    )
    app.config["EVENT_LOG"].record(
        type="push",
        source="deck",
        target="icloud",
        status="sent",
        digest="ab" * 8,
        extra={"device_ids": ["tv"], "composition_digest": "cd" * 8},
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


#: Surface -> (path, the frames, the picture inside a frame, the display's shape
#: when the frame does not carry it, what to do first).
SURFACES: dict[str, dict[str, Any]] = {
    "dashboards-list": {
        "path": "/pages?view=groups",
        "frame": ".dx-dash-thumb",
    },
    "dashboards-opened-row": {
        "path": "/pages?view=groups&opened=agenda",
        "frame": ".dx-dashquick-preview",
        "ar": (800, 480),
    },
    "dashboards-hover": {
        "path": "/pages?view=groups",
        "frame": ".dx-dash-preview",
        "ar": (800, 480),
        "hover": ".dx-dash-thumb",
    },
    "lineup-editor-cards": {
        "path": "/decks/night/edit",
        "frame": ".lu.dxe .lu-thumb",
    },
    "history": {
        "path": "/history",
        "frame": ".dx-hist-thumb",
        "ar": (3840, 2160),
    },
    "events": {
        "path": "/events",
        "frame": ".dx-event-thumb-link",
        "ar": (4, 3),
    },
    "devices-list": {
        "path": "/settings/devices",
        "frame": ".dx-thumb",
    },
    "devices-list-queued": {
        "path": "/settings/devices",
        "frame": ".dx-thumb-q-frame",
    },
    "device-page-now": {
        "path": "/settings/devices/door",
        "frame": ".dx-now-thumb",
    },
    "device-page-next": {
        "path": "/settings/devices/door",
        "frame": ".dx-next-thumb",
    },
    "send-preview": {
        "path": "/send",
        "frame": ".send-pair-preview .preview-frame",
        "img": "img.preview-image",
    },
    "page-editor-preview": {
        "path": "/pages/agenda",
        "frame": ".preview-frame",
        "img": "img.preview-panel-img",
    },
    "lineups": {
        "lineups": True,
        "frame": ".lu-thumb",
    },
}

# Paints every picture inside the frames as a solid colour of the display's
# ratio, as the server's thumbnails are, and reports the layout.
_MEASURE = """
async ({frame, img, rgb, ar, hover}) => {
  if (hover) {
    const t = document.querySelector(hover);
    t.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
    await new Promise((r) => setTimeout(r, 700));
  }
  const frames = [...document.querySelectorAll(frame)];
  const shape = (el) => {
    const cs = getComputedStyle(el);
    let m = (cs.getPropertyValue('--thumb-ar') || '').match(/([\\d.]+)\\s*\\/\\s*([\\d.]+)/);
    if (m) return [Number(m[1]), Number(m[2])];
    const w = Number(cs.getPropertyValue('--arw')), h = Number(cs.getPropertyValue('--arh'));
    if (w && h) return [w, h];
    m = (cs.aspectRatio || '').match(/([\\d.]+)\\s*\\/\\s*([\\d.]+)/);
    if (m && ar === null) return [Number(m[1]), Number(m[2])];
    return ar;
  };
  const waits = [];
  const kept = [];
  for (const f of frames) {
    for (let p = f.parentElement; p; p = p.parentElement) {
      if (p.hidden) p.hidden = false;
    }
    const im = f.querySelector(img || ':scope > img') || f.querySelector('img');
    if (!im) continue;
    const s = shape(f);
    if (!s) continue;
    const k = 352 / Math.max(s[0], s[1]);
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(s[0] * k));
    c.height = Math.max(1, Math.round(s[1] * k));
    const g = c.getContext('2d');
    g.fillStyle = `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
    g.fillRect(0, 0, c.width, c.height);
    im.removeAttribute('loading');
    im.removeAttribute('srcset');
    im.hidden = false;
    waits.push(new Promise((res) => {
      im.addEventListener('load', res, {once: true});
      im.addEventListener('error', res, {once: true});
    }));
    im.src = c.toDataURL();
    kept.push([f, im, s]);
  }
  await Promise.all(waits);
  await new Promise((r) => setTimeout(r, 150));
  return kept.map(([f, im, s]) => {
    const fr = f.getBoundingClientRect();
    const ir = im.getBoundingClientRect();
    const cs = getComputedStyle(f);
    const edge = Math.max(
      parseFloat(cs.borderTopWidth) || 0,
      parseFloat(getComputedStyle(f, '::before').borderTopWidth) || 0,
      parseFloat(getComputedStyle(f, '::after').borderTopWidth) || 0,
    );
    // The picture as object-fit lays it out, inside the img box.
    const fit = getComputedStyle(im).objectFit;
    const nat = im.naturalWidth / im.naturalHeight;
    let pw = ir.width, ph = ir.height;
    if (fit === 'contain' || fit === 'scale-down') {
      pw = Math.min(ir.width, ir.height * nat);
      ph = Math.min(ir.height, ir.width / nat);
    }
    const left = ir.left + (ir.width - pw) / 2;
    const top = ir.top + (ir.height - ph) / 2;
    // What the border leaves of the frame (a border on the box itself).
    const bl = parseFloat(cs.borderLeftWidth) || 0, bt = parseFloat(cs.borderTopWidth) || 0;
    const br = parseFloat(cs.borderRightWidth) || 0, bb = parseFloat(cs.borderBottomWidth) || 0;
    const inner = [fr.left + bl, fr.top + bt, fr.right - br, fr.bottom - bb];
    // Hidden picture box (an empty placeholder) is not a thumbnail to check.
    const shown = ir.width > 0 && ir.height > 0 && fr.width > 0 && fr.height > 0;
    return {
      shown,
      cls: f.className,
      fit,
      frame: [fr.left, fr.top, fr.right, fr.bottom],
      edge,
      ownBorder: [bl, bt, br, bb],
      // A picture shown faded (a card still drawing, a past step) is not the picture's colour.
      dim: (() => {
        let o = 1;
        for (let e = im; e; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity);
        return o < 0.99;
      })(),
      ratio: fr.width / fr.height,
      want: s[0] / s[1],
      gap: [
        Math.max(0, left - inner[0]),
        Math.max(0, top - inner[1]),
        Math.max(0, inner[2] - (left + pw)),
        Math.max(0, inner[3] - (top + ph)),
      ],
    };
  });
}
"""


def _close(a: tuple[int, int, int], b: tuple[int, int, int], tolerance: int = 40) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b, strict=True))


def _uncovered_px(page: Any, row: dict[str, Any], rgb: tuple[int, int, int]) -> dict[str, int]:
    """Per side, the device pixels between the border and the picture's first
    pixel: from the middle of the frame outwards, how far the picture's colour
    reaches, against where the frame ends."""
    import math

    from PIL import Image

    left, top, right, bottom = row["frame"]
    # A clip on whole css pixels, so the shot's pixels line up with the layout's.
    cx, cy = max(0, math.floor(left) - 2), max(0, math.floor(top) - 2)
    clip = {
        "x": cx,
        "y": cy,
        "width": math.ceil(right) - cx + 2,
        "height": math.ceil(bottom) - cy + 2,
    }
    with Image.open(io.BytesIO(page.screenshot(clip=clip))) as shot:
        im = shot.convert("RGB")
    # The frame in the shot; the browser snaps a box to whole device pixels.
    x0, x1 = round((left - cx) * _SCALE), round((right - cx) * _SCALE)
    y0, y1 = round((top - cy) * _SCALE), round((bottom - cy) * _SCALE)
    border = math.ceil(row["edge"] * _SCALE)

    def reach(start: tuple[int, int], step: tuple[int, int]) -> int:
        """How many pixels of the picture's colour from ``start`` towards ``step``."""
        x, y = start
        n = 0
        while 0 <= x < im.width and 0 <= y < im.height and _close(im.getpixel((x, y)), rgb):
            x, y, n = x + step[0], y + step[1], n + 1
        return n

    out: dict[str, int] = {}
    w, h = x1 - x0, y1 - y0
    for side, lines in {
        "top": [((x0 + f * w // 4, y0 + h // 2), (0, -1), y0) for f in (1, 2, 3)],
        "bottom": [((x0 + f * w // 4, y0 + h // 2), (0, 1), y1) for f in (1, 2, 3)],
        "left": [((x0 + w // 2, y0 + f * h // 4), (-1, 0), x0) for f in (1, 2, 3)],
        "right": [((x0 + w // 2, y0 + f * h // 4), (1, 0), x1) for f in (1, 2, 3)],
    }.items():
        gaps = []
        for start, step, edge in lines:
            n = reach(start, step)
            reached = (start[1] if step[0] == 0 else start[0]) + step[0 if step[1] == 0 else 1] * n
            gaps.append(abs(reached - edge) - border)
        out[side] = min(gaps)
    return out


@pytest.mark.parametrize("ui", ["paper", "classic"])
@pytest.mark.parametrize(
    ("scheme", "rgb"), [("light", _DARK_PICTURE), ("dark", _LIGHT_PICTURE)], ids=["light", "dark"]
)
@pytest.mark.parametrize("surface", list(SURFACES))
def test_no_thumbnail_leaves_an_uncovered_edge(
    request: pytest.FixtureRequest,
    live: tuple[str, Flask],
    surface: str,
    scheme: str,
    rgb: tuple[int, int, int],
    ui: str,
) -> None:
    from playwright.sync_api import sync_playwright

    spec = SURFACES[surface]
    base, app = live
    if spec.get("lineups"):
        base = request.getfixturevalue("lineups_live")
    else:
        app.config["SETTINGS_STORE"].patch_section("app", {"ui_design": ui})
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()
        try:
            ctx = browser.new_context(
                # Tall, so a frame as tall as the Send preview is on screen whole.
                viewport={"width": 1440, "height": 2400},
                device_scale_factor=_SCALE,
                color_scheme=scheme,  # type: ignore[arg-type]
            )
            page = ctx.new_page()
            if spec.get("lineups"):
                ctx.request.get(f"{base}/_set_ui/{ui}")
                _Api(pics=True).install(page)
                page.goto(f"{base}{LINEUPS_PAGE}?pics=1", wait_until="load")
                page.wait_for_function(
                    "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
                )
            else:
                # Answer every preview image, so no render is queued.
                page.route(
                    re.compile(r".*/compose/(thumbs|[^/]+/preview\.png).*"),
                    lambda r: r.fulfill(status=200, content_type="image/png", body=_PNG),
                )
                page.goto(f"{base}{spec['path']}", wait_until="load")
            rows = page.evaluate(
                _MEASURE,
                {
                    "frame": spec["frame"],
                    "img": spec.get("img"),
                    "rgb": list(rgb),
                    "ar": spec.get("ar"),
                    "hover": spec.get("hover"),
                },
            )
            rows = [r for r in rows if r["shown"]]
            assert rows, f"no {spec['frame']} thumbnail on {surface}"
            for row in rows:
                # The picture covers what is inside the border, to a hair.
                assert max(row["gap"]) < 0.5, (surface, row)
                # The box is the display's shape, whatever the border.
                assert abs(row["ratio"] - row["want"]) < 0.01 * row["want"], (surface, row)
            for row in [r for r in rows if not r["dim"]][:6]:
                if not spec.get("hover"):  # a scroll would close the hover preview
                    page.evaluate("(y) => window.scrollTo(0, y - 120)", row["frame"][1])
                    page.wait_for_timeout(50)
                moved = page.evaluate(
                    _RECT,
                    {"frame": spec["frame"], "img": spec.get("img"), "index": rows.index(row)},
                )
                assert moved is not None, (surface, row["cls"])
                gaps = _uncovered_px(page, {**row, "frame": moved}, rgb)
                # The frame's edge is snapped to a device pixel, so a pixel of leeway.
                assert max(gaps.values()) <= 1, (surface, scheme, ui, row["cls"], gaps)
        finally:
            browser.close()


# The frame's rectangle now, after a scroll.
_RECT = """
({frame, img, index}) => {
  const pic = (f) => f.querySelector(img || ':scope > img') || f.querySelector('img');
  const visible = [...document.querySelectorAll(frame)].filter((f) => {
    const r = f.getBoundingClientRect();
    const i = pic(f);
    return r.width > 0 && r.height > 0 && i && !i.hidden;
  });
  const f = visible[index];
  if (!f) return null;
  const r = f.getBoundingClientRect();
  return [r.left, r.top, r.right, r.bottom];
}
"""


def _tiny_png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 9), (128, 128, 128)).save(buf, format="PNG")
    return buf.getvalue()


_PNG = _tiny_png()
