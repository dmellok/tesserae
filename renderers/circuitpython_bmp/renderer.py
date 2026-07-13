"""circuitpython_bmp renderer.

Composition -> palette-quantized *uncompressed* indexed BMP at the
panel's exact dims. Sibling of ``circuitpython_png`` for the same
generic CircuitPython client, differing only in the wire container.

Why BMP as well as PNG: CircuitPython's ``zlib.decompress`` is
one-shot (no streaming inflate like MicroPython has), so decoding even
a small indexed PNG needs a contiguous buffer for the inflated image
data alongside the ``displayio.Bitmap``. On a Pico W (~110K free SRAM)
that either exhausts memory or fails on fragmentation. An uncompressed
BMP has no zlib in the path at all: ``adafruit_imageload`` walks it row
by row with ``file.read`` / ``seek``, so peak RAM is basically the
framebuffer plus a small row buffer. Boards with room to spare
(ESP32-S3 etc.) can keep using the smaller PNG; Pico W class boards
declare BMP and skip the decompress wall.

Output palette selected from the bound panel's gamut, identical to
``circuitpython_png``:

* ``mono``                             -> black + white
* ``bwr_3``                            -> 3-colour black/white/red
* ``gray_4``                           -> 4-level greyscale ramp (2-bit)
* ``bwry_4``                           -> 4-colour black/white/red/yellow
* ``spectra_6`` / ``waveshare_e6``     -> 6-colour Spectra 6
* ``acep_7colour`` / ``inky_7colour``  -> 7-colour ACeP
* ``rgb24`` / ``rgb16``                -> 24-bit RGB BMP, no quantise

Unknown or custom gamuts fall back to Spectra 6 nominal.

Note on wire size: Pillow writes palette-mode BMP at 8 bits per pixel,
so the 2-bit gamuts go out a few times larger than the equivalent PNG.
The client's constraint here is the decode buffer, not download size,
so that trade is deliberate; a sub-byte BMP writer is a follow-up if
bandwidth ever matters.

Same per-device settings as ``circuitpython_png``: a dither-mode select
and a pre-dither contrast slider.
"""

from __future__ import annotations

import io
from typing import Any

from app.quantizer import circuitpython_indexed_image
from app.state.page_store import Panel


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Fit, contrast-adjust, quantize, and save the composition as an
    uncompressed indexed BMP at the panel's exact dims.

    Shares the pixel pipeline (fit, contrast, palette-quantise) with
    ``circuitpython_png`` via
    :func:`app.quantizer.circuitpython_indexed_image`; this renderer
    saves the result as an uncompressed BMP so the client never runs
    ``zlib.decompress``. Pillow's BMP writer emits BI_RGB (uncompressed)
    bottom-up bitmaps, the format ``adafruit_imageload`` reads.
    """
    img = circuitpython_indexed_image(
        png_bytes,
        width=panel.w,
        height=panel.h,
        gamut=panel.gamut,
        flip=panel.flip,
        underscan=panel.underscan,
        settings=settings,
    )
    buf = io.BytesIO()
    # Uncompressed BMP: no ``compression`` kwarg means BI_RGB, which is
    # the only BMP form adafruit_imageload decodes (it rejects RLE).
    img.save(buf, format="BMP")
    return buf.getvalue()


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    """Indexed-BMP output is self-contained; no rotation / scale / bg
    knobs in the wire payload since the renderer applied them already."""
    del settings
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.bmp"}
