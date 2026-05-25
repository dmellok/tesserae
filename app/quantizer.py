"""Pure image ops used by renderers.

Kept renderer-agnostic so any renderer can compose them: M2 needs only
``rotate_png``; M4 adds Spectra 6 packing + dither modes (lifted from
inky-dash's ``app.quantizer``).
"""

from __future__ import annotations

import io

from PIL import Image


def rotate_png(png_bytes: bytes, *, quarters: int) -> bytes:
    """Rotate a PNG by N 90° clockwise turns. ``quarters=0`` is a no-op pass-through."""
    n = quarters % 4
    if n == 0:
        return png_bytes
    img = Image.open(io.BytesIO(png_bytes))
    # PIL rotates counter-clockwise; negate to get clockwise.
    rotated = img.rotate(-90 * n, expand=True)
    out = io.BytesIO()
    rotated.save(out, format="PNG", optimize=True)
    return out.getvalue()
