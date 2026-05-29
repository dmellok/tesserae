#!/usr/bin/env python3
"""Capture one screenshot per widget for the docs gallery.

Drives a *running* Tesserae instance's ``/_test/render`` route with
Playwright — the same headless-Chromium path the renderer uses — and saves
``docs/screenshots/widgets/<id>.png`` for every ``kind == "widget"`` plugin.

The instance must be running in dev or testing mode (``/_test/render`` is
gated to ``app.debug or app.testing``) and reachable on loopback. Widgets
that need API keys (GitHub, Home Assistant, Spotify, …) render real data
only if those are configured on that instance; otherwise they capture
their empty / error state.

Usage:
    python -m app.main --dev &                 # or any dev instance
    python scripts/capture_widget_shots.py     # TESSERAE_URL overrides base
    SIZE=lg python scripts/capture_widget_shots.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"
OUT_DIR = REPO_ROOT / "docs" / "screenshots" / "widgets"
BASE_URL = os.environ.get("TESSERAE_URL", "http://127.0.0.1:8000").rstrip("/")

# Preferred capture size, then fallbacks. md (640×400) gives a tidy,
# uniform gallery thumbnail; fall back to whatever the widget supports.
SIZE_PREFERENCE = [os.environ.get("SIZE", "md"), "md", "lg", "sm", "xs"]
SIZE_DIMS = {"xs": (180, 180), "sm": (380, 240), "md": (640, 400), "lg": (1200, 800)}

# The same font-settle the renderer runs before screenshotting.
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


def _widget_sizes() -> list[tuple[str, str]]:
    """(plugin_id, size) pairs to capture, picking each widget's best size."""
    out: list[tuple[str, str]] = []
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
        supported = data.get("supports", {}).get("sizes") or ["md"]
        size = next((s for s in SIZE_PREFERENCE if s in supported), supported[-1])
        out.append((child.name, size))
    return out


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed — run: .venv/bin/python -m pip install -e '.[dev]'")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = _widget_sizes()
    print(f"capturing {len(targets)} widgets from {BASE_URL} -> {OUT_DIR.relative_to(REPO_ROOT)}")

    ok, blank, failed = 0, 0, 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for plugin_id, size in targets:
            w, h = SIZE_DIMS[size]
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
            url = f"{BASE_URL}/_test/render?plugin={plugin_id}&size={size}"
            try:
                try:
                    page.goto(url, wait_until="networkidle", timeout=20000)
                except Exception:
                    page.goto(url, wait_until="load", timeout=20000)
                    page.wait_for_timeout(1500)
                page.evaluate(_FONT_WAIT)
                page.wait_for_timeout(400)
                panel = page.query_selector(".panel") or page.query_selector("body")
                dest = OUT_DIR / f"{plugin_id}.png"
                panel.screenshot(path=str(dest), animations="disabled")
                size_kb = dest.stat().st_size / 1024
                if size_kb < 1.5:  # essentially empty render
                    blank += 1
                    print(f"  ~ {plugin_id} ({size}) — looks blank ({size_kb:.1f} KB)")
                else:
                    ok += 1
                    print(f"  ✓ {plugin_id} ({size})")
            except Exception as err:
                failed += 1
                print(f"  ✗ {plugin_id} ({size}) — {type(err).__name__}: {err}")
            finally:
                page.close()
        browser.close()

    print(f"\ndone: {ok} ok, {blank} blank, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
