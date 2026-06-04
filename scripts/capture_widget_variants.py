#!/usr/bin/env python3
"""Capture every widget's variants and stitch them into one composite PNG.

For widgets that expose a ``variant`` cell_option (with ``choices``) we
hit ``/_test/render`` once per variant, then tile the captures into a
single ``docs/screenshots/widgets/<id>--variants.png`` so the wiki
gallery can embed "here are the four directions" with one ``<img>``.

Layout: 2 columns × N rows, gap-separated, dropping the ``legacy``
variant (the quiet pre-handoff option) so the strip leads with the
new design system. Each cell renders at ``md`` (640×400) when the
widget supports it, then falls back through ``lg``/``sm``/``xs``.

Widgets without a variant picker are skipped — the existing
``capture_widget_shots.py`` already covers them with the single shot.

Usage (same as capture_widget_shots.py):
    python -m app.main --dev &
    TESSERAE_PASSWORD=... python scripts/capture_widget_variants.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"
OUT_DIR = REPO_ROOT / "docs" / "screenshots" / "widgets"
BASE_URL = os.environ.get("TESSERAE_URL", "http://127.0.0.1:8765").rstrip("/")

SIZE_PREFERENCE = [os.environ.get("SIZE", "md"), "md", "lg", "sm", "xs"]
SIZE_DIMS = {"xs": (180, 180), "sm": (380, 240), "md": (640, 400), "lg": (1200, 800)}

# Tile layout for the composite. Each row carries up to N variants; the
# script grows rows as needed for widgets with 5+ variants (s1–s6 etc).
COLUMNS = 2
GAP = 16
BG = (245, 245, 240)  # soft warm cream — close to most light themes' bg

# Skip ``legacy`` from the variant strip — it's the conservative fallback
# option, not part of the "four directions" story. Anything else listed in
# a widget's variant choices is captured.
SKIP_VARIANTS = {"legacy"}

_FONT_WAIT = """async () => {
    if (!document.fonts || !document.fonts.load) return;
    const families = new Set();
    document.querySelectorAll('.cell').forEach((cell) => {
        const ff = getComputedStyle(cell).fontFamily;
        if (!ff) return;
        const first = ff.split(',')[0].trim().replace(/^['"]|['"]$/g, '');
        if (first) families.add(first);
    });
    const loads = [];
    for (const family of families)
        for (const w of [400, 500, 600, 700])
            loads.push(document.fonts.load(w + ' 100px "' + family + '"').catch(() => {}));
    await Promise.all(loads);
    await document.fonts.ready;
}"""


def _login(base: str, password: str) -> str:
    s = requests.Session()
    s.get(f"{base}/login", timeout=10)
    resp = s.post(
        f"{base}/login",
        data={"password": password},
        allow_redirects=False,
        timeout=10,
    )
    if resp.status_code not in (302, 303):
        raise SystemExit(
            f"Login failed: HTTP {resp.status_code}. "
            "Check TESSERAE_PASSWORD / --password against the running instance."
        )
    cookies = s.cookies.get_dict()
    if "session" not in cookies:
        raise SystemExit(
            f"Login succeeded but no session cookie came back. Got: {list(cookies.keys())}"
        )
    return cookies["session"]


def _variant_targets() -> list[tuple[str, str, list[tuple[str, str]]]]:
    """(plugin_id, size, [(variant_value, variant_label), …]) per widget.

    Returns only widgets that ship a ``variant`` cell_option with two
    or more non-legacy choices — the single-style widgets are already
    covered by capture_widget_shots.py.
    """
    out: list[tuple[str, str, list[tuple[str, str]]]] = []
    for child in sorted(PLUGINS_DIR.iterdir()):
        manifest = child / "plugin.json"
        if not child.is_dir() or not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("kind") != "widget":
            continue

        variant_opt = next(
            (o for o in data.get("cell_options", []) if o.get("name") == "variant"),
            None,
        )
        if not variant_opt:
            continue
        choices = [
            (c.get("value"), c.get("label") or c.get("value"))
            for c in variant_opt.get("choices") or []
            if c.get("value") and c.get("value") not in SKIP_VARIANTS
        ]
        if len(choices) < 2:
            continue

        supported = data.get("supports", {}).get("sizes") or ["md"]
        size = next((s for s in SIZE_PREFERENCE if s in supported), supported[-1])
        out.append((child.name, size, choices))
    return out


def _composite(tiles: list[Image.Image]) -> Image.Image:
    """Tile ``tiles`` into a (COLUMNS)-wide grid with GAP padding."""
    if not tiles:
        raise ValueError("nothing to composite")
    w, h = tiles[0].size
    cols = COLUMNS
    rows = (len(tiles) + cols - 1) // cols
    canvas_w = cols * w + (cols - 1) * GAP
    canvas_h = rows * h + (rows - 1) * GAP
    canvas = Image.new("RGB", (canvas_w, canvas_h), BG)
    for i, img in enumerate(tiles):
        r, c = divmod(i, cols)
        x = c * (w + GAP)
        y = r * (h + GAP)
        canvas.paste(img, (x, y))
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--password",
        default=os.environ.get("TESSERAE_PASSWORD"),
        help="Admin password (env: TESSERAE_PASSWORD)",
    )
    parser.add_argument(
        "--only",
        help="Comma-separated plugin ids; capture only these (useful when re-running).",
    )
    args = parser.parse_args()
    if not args.password:
        print("Pass --password or set TESSERAE_PASSWORD.")
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed — run: .venv/bin/python -m pip install -e '.[dev]'")
        return 1

    session_cookie = _login(BASE_URL, args.password)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = _variant_targets()
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        targets = [t for t in targets if t[0] in wanted]

    print(
        f"capturing variants for {len(targets)} widgets from {BASE_URL} "
        f"-> {OUT_DIR.relative_to(REPO_ROOT)}/*--variants.png"
    )

    from urllib.parse import urlparse

    parsed = urlparse(BASE_URL)
    cookie = {
        "name": "session",
        "value": session_cookie,
        "domain": parsed.hostname or "127.0.0.1",
        "path": "/",
    }

    ok, failed = 0, 0
    tmp_dir = OUT_DIR / "_variant_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        context.add_cookies([cookie])

        for plugin_id, size, choices in targets:
            w, h = SIZE_DIMS[size]
            tiles: list[Image.Image] = []
            try:
                for variant_value, _label in choices:
                    page = context.new_page()
                    page.set_viewport_size({"width": w, "height": h})
                    url = (
                        f"{BASE_URL}/_test/render?plugin={plugin_id}"
                        f"&size={size}&variant={variant_value}"
                    )
                    try:
                        page.goto(url, wait_until="networkidle", timeout=20000)
                    except Exception:
                        page.goto(url, wait_until="load", timeout=20000)
                        page.wait_for_timeout(1500)
                    page.evaluate(_FONT_WAIT)
                    page.wait_for_timeout(400)
                    panel = page.query_selector(".panel") or page.query_selector("body")
                    tile_path = tmp_dir / f"{plugin_id}-{variant_value}.png"
                    panel.screenshot(path=str(tile_path), animations="disabled")
                    page.close()
                    tiles.append(Image.open(tile_path).convert("RGB"))

                composite = _composite(tiles)
                dest = OUT_DIR / f"{plugin_id}--variants.png"
                composite.save(dest, optimize=True)
                size_kb = dest.stat().st_size / 1024
                ok += 1
                print(f"  ✓ {plugin_id:30s} {len(tiles)} variants ({size_kb:.0f} KB)")
            except Exception as err:
                failed += 1
                print(f"  ✗ {plugin_id:30s} {type(err).__name__}: {err}")

        context.close()
        browser.close()

    # Clean up the per-variant temp files; the composites are what
    # the wiki embeds.
    for f in tmp_dir.glob("*.png"):
        f.unlink()
    tmp_dir.rmdir()

    print(f"\ndone: {ok} ok, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
