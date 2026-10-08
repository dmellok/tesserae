"""Render the Tesserae server mark SVGs into PNGs.

HA Apps want a 128×128 ``icon.png``; browser tabs read
``<link rel="icon" type="image/svg+xml">`` directly but some Apple devices
and the web manifest still want PNGs. This script rasterises the canonical
SVGs in ``static/brand/`` with headless Chromium (Playwright, which the test
suite already uses) so the nav mark, the favicon, the home-screen icons and
the App sidebar all come from the same drawing.

Two sources:

* ``icon.svg``, the rounded ink tile for light grounds (no hairline: a
  raster favicon follows the light-ground rule). Rendered with a
  transparent background for the favicon, the generic icons and the
  firmware splash bakes.
* ``square.svg``, the same mark full bleed with no rounding, for the Apple
  touch icon and the maskable PWA icon, whose platforms apply their own
  mask.

Run when either SVG changes. Commits the PNGs alongside the SVGs.

  uv run python scripts/render_brand.py
"""

from __future__ import annotations

import io
from pathlib import Path
from urllib.parse import quote

from PIL import Image
from playwright.sync_api import Page, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "static" / "brand"
ROUNDED = OUT_DIR / "icon.svg"
SQUARE = OUT_DIR / "square.svg"

# Firmware splash bakes, square PNGs with a transparent backdrop the
# client can composite over whatever paper-white / dithered background
# the panel uses. The tile itself stays opaque; only the corners outside
# the rounded square are transparent. Picked to cover the panel range
# from Inky pHAT (~212px wide) through to the Inky 13.3" (1600×1200);
# the client builder picks the size closest to its target dim.
FIRMWARE_SIZES: tuple[int, ...] = (64, 96, 128, 192, 256, 384, 512, 768, 1024)


def rasterise(page: Page, svg: Path, size: int) -> Image.Image:
    """Draw ``svg`` at ``size``×``size`` CSS px (device scale 1) and return it
    with the page background left transparent. Chromium rasterises the
    vector at the target size, so small icons are hinted at their own size
    rather than resampled down from a large one."""
    uri = "data:image/svg+xml," + quote(svg.read_text(encoding="utf-8"), safe="")
    page.set_viewport_size({"width": size, "height": size})
    page.set_content(
        '<html><body style="margin:0;background:transparent">'
        f'<img src="{uri}" width="{size}" height="{size}" style="display:block">'
        "</body></html>"
    )
    page.wait_for_function("document.images[0].complete")
    png = page.locator("img").screenshot(omit_background=True)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fw_dir = OUT_DIR / "firmware"
    fw_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=1)

        def save(svg: Path, size: int, out: Path) -> None:
            rasterise(page, svg, size).save(out, optimize=True)

        # 128×128, the HA App's store and sidebar icon (copied by hand into
        # the add-on repo's tesserae/ and tesserae-edge/ directories).
        save(ROUNDED, 128, OUT_DIR / "icon.png")
        # 512×512, social cards, README, og:image, web manifest "any".
        save(ROUNDED, 512, OUT_DIR / "icon-512.png")
        # 32×32, PNG favicon fallback for Safari + older browsers.
        save(ROUNDED, 32, OUT_DIR / "favicon-32.png")
        # 192×192, Android Chrome's preferred home-screen icon size + the
        # smaller of the two web-manifest entries.
        save(ROUNDED, 192, OUT_DIR / "icon-192.png")
        # 180×180, the canonical Apple "Add to Home Screen" icon (iOS,
        # iPadOS, macOS Safari → Add to Dock). Full bleed: iOS applies its
        # own squircle mask, and a pre-rounded source leaves a thin band of
        # background colour at the corners wherever iOS's mask radius
        # doesn't match ours. Apple's HIG says "don't add a layer mask of
        # an icon's shape to your image; iOS automatically applies an icon
        # mask."
        save(SQUARE, 180, OUT_DIR / "apple-touch-icon.png")
        # 512×512 maskable variant for Android adaptive icons. The square
        # source has no outer rounding so the launcher can mask it into
        # whatever shape it wants; the squares sit at 21-79 %, inside the
        # maskable safe zone.
        save(SQUARE, 512, OUT_DIR / "icon-maskable-512.png")
        print(f"Wrote PNGs into {OUT_DIR.relative_to(REPO_ROOT)}")

        # Firmware splash bakes, kept in their own subdir so the
        # `find / | xargs` workflows that pick up brand assets for the
        # admin UI don't accidentally hoover up the splash sizes.
        for size in FIRMWARE_SIZES:
            save(ROUNDED, size, fw_dir / f"tesserae-splash-{size}.png")
        print(
            f"Wrote {len(FIRMWARE_SIZES)} firmware splash PNGs into {fw_dir.relative_to(REPO_ROOT)}"
        )

        browser.close()


if __name__ == "__main__":
    main()
