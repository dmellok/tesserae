"""Render the Tesserae brand SVG into PNGs.

HA add-ons want a 128×128 ``icon.png`` and an optional ``logo.png``;
browser tabs read ``<link rel="icon" type="image/svg+xml">`` directly but
some Apple devices still want a PNG fallback. This script bakes both
from the same canonical shape definition the SVG file describes so the
nav mark, the favicon, and the add-on sidebar all stay in sync.

Run when you change ``static/brand/icon.svg``, or when the brand colour
moves. Commits the PNGs alongside the SVG.

  python scripts/render_brand.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "static" / "brand"

# Mirrors static/brand/icon.svg.
ACCENT = (0x0D, 0x8C, 0x7E)
ACCENT_HOVER = (0x0A, 0x6F, 0x63)
QUADRANT_COLOR = (255, 255, 255)
QUADRANT_ALPHA = int(0.85 * 255)

# 256 base; downscale to 128 for ``icon.png`` so the PIL antialiasing
# kicks in at the final size and corners stay clean.
BASE_SIZE = 256
INNER_INSET = 55
INNER_RADIUS = 27
OUTER_RADIUS = 72
QUADRANT = (BASE_SIZE - 2 * INNER_INSET) // 2  # 73


def _diagonal_gradient(size: int) -> np.ndarray:
    """135° gradient from ACCENT (top-left) to ACCENT_HOVER (bottom-right)."""
    y, x = np.indices((size, size), dtype=np.float32)
    t = (x + y) / (2 * (size - 1))
    r = (ACCENT[0] + (ACCENT_HOVER[0] - ACCENT[0]) * t).astype(np.uint8)
    g = (ACCENT[1] + (ACCENT_HOVER[1] - ACCENT[1]) * t).astype(np.uint8)
    b = (ACCENT[2] + (ACCENT_HOVER[2] - ACCENT[2]) * t).astype(np.uint8)
    a = np.full_like(r, 255, dtype=np.uint8)
    return np.stack([r, g, b, a], axis=-1)


def render(size: int = BASE_SIZE, *, maskable: bool = False) -> Image.Image:
    """Draw the brand mark at ``size``×``size``. Use BASE_SIZE for max
    fidelity then resample down if you need a smaller deliverable.

    ``maskable=True`` skips the outer rounded-rectangle so the gradient
    fills the full square. Android adaptive icons mask the canvas to
    whatever shape the launcher uses (circle, squircle, rounded square);
    a pre-rounded source would have its corners double-clipped.
    """
    bg = _diagonal_gradient(size)

    if not maskable:
        # Outer rounded-square mask carves the gradient into the brand shape.
        outer_mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(outer_mask).rounded_rectangle(
            (0, 0, size - 1, size - 1),
            radius=int(OUTER_RADIUS * size / BASE_SIZE),
            fill=255,
        )
        bg[..., 3] = np.array(outer_mask)
    canvas = Image.fromarray(bg)

    # Inner quadrant overlay, two filled white squares clipped to the
    # rounded inner rect (matches the conic-gradient pattern in shell.css).
    scale = size / BASE_SIZE
    inset = int(INNER_INSET * scale)
    quad = int(QUADRANT * scale)
    inner_radius = int(INNER_RADIUS * scale)
    inner_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(inner_mask).rounded_rectangle(
        (inset, inset, size - inset - 1, size - inset - 1),
        radius=inner_radius,
        fill=255,
    )
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    quad_fill = (*QUADRANT_COLOR, QUADRANT_ALPHA)
    # Top-right quadrant.
    draw.rectangle((inset + quad, inset, inset + 2 * quad, inset + quad), fill=quad_fill)
    # Bottom-left quadrant.
    draw.rectangle((inset, inset + quad, inset + quad, inset + 2 * quad), fill=quad_fill)
    overlay_arr = np.array(overlay)
    overlay_arr[..., 3] = np.minimum(overlay_arr[..., 3], np.array(inner_mask))
    overlay = Image.fromarray(overlay_arr)

    return Image.alpha_composite(canvas, overlay)


# Firmware splash bakes, square PNGs with a transparent backdrop the
# client can composite over whatever paper-white / dithered background
# the panel uses. The brand mark itself stays opaque (the rounded
# square + white tessellation pattern); only the corners outside the
# rounded square are transparent. Picked to cover the panel range
# from Inky pHAT (~212px wide) through to the Inky 13.3" (1600×1200);
# the client builder picks the size closest to its target dim.
FIRMWARE_SIZES: tuple[int, ...] = (64, 96, 128, 192, 256, 384, 512, 768, 1024)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # 128×128, HA add-on standard sidebar icon.
    render(BASE_SIZE).resize((128, 128), Image.LANCZOS).save(OUT_DIR / "icon.png", optimize=True)
    # 512×512, social cards, README, og:image.
    render(BASE_SIZE).resize((512, 512), Image.LANCZOS).save(
        OUT_DIR / "icon-512.png", optimize=True
    )
    # 32×32, PNG favicon fallback for Safari + older browsers.
    render(BASE_SIZE).resize((32, 32), Image.LANCZOS).save(
        OUT_DIR / "favicon-32.png", optimize=True
    )
    # 180×180, the canonical Apple "Add to Home Screen" icon (iOS,
    # iPadOS, macOS Safari → Add to Dock). iOS upscales smaller PNGs
    # and the result looks blurry on Retina.
    render(BASE_SIZE).resize((180, 180), Image.LANCZOS).save(
        OUT_DIR / "apple-touch-icon.png", optimize=True
    )
    # 192×192, Android Chrome's preferred home-screen icon size + the
    # smaller of the two web-manifest entries.
    render(BASE_SIZE).resize((192, 192), Image.LANCZOS).save(
        OUT_DIR / "icon-192.png", optimize=True
    )
    # 512×512 maskable variant for Android adaptive icons. Renders the
    # mark on a square canvas (no outer rounding) so the launcher can
    # mask it into whatever shape it wants without double-clipping the
    # corners. The white quadrants are inset ~21%, well inside the
    # ~10% safe zone Android requires.
    render(BASE_SIZE, maskable=True).resize((512, 512), Image.LANCZOS).save(
        OUT_DIR / "icon-maskable-512.png", optimize=True
    )
    print(f"Wrote PNGs into {OUT_DIR.relative_to(REPO_ROOT)}")

    # Firmware splash bakes, keep them in their own subdir so the
    # `find / | xargs` workflows that pick up brand assets for the
    # admin UI don't accidentally hoover up the splash sizes.
    fw_dir = OUT_DIR / "firmware"
    fw_dir.mkdir(parents=True, exist_ok=True)
    # Render fresh at each target size when small enough to avoid one
    # ResizeStep aliasing; for sizes above BASE_SIZE, render at the
    # target size directly so the rounded-rectangle stays sharp.
    for size in FIRMWARE_SIZES:
        if size <= BASE_SIZE:
            img = render(BASE_SIZE).resize((size, size), Image.LANCZOS)
        else:
            img = render(size)
        img.save(fw_dir / f"tesserae-splash-{size}.png", optimize=True)
    print(f"Wrote {len(FIRMWARE_SIZES)} firmware splash PNGs into {fw_dir.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
