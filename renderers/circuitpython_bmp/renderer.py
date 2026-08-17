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

Wire size: the frame is packed at the smallest standard BMP bit depth
that fits its palette (:func:`app.bmp_writer.pack_indexed_bmp`), 1 bpp for
mono and 4 bpp for the tri-colour / 4-grey / Spectra 6 / 7-colour gamuts,
so it's 2-8x smaller than a naive 8-bit BMP while still decoding on the
same ``adafruit_imageload`` path (its unpacker is generic over bit depth).

Same per-device settings as ``circuitpython_png``: a dither-mode select
and a pre-dither contrast slider. It shares that renderer's palette
handling too, nominal palette per gamut, with the profile's edge knobs
applied (discussion #227).
"""

from __future__ import annotations

from typing import Any

from app.bmp_writer import pack_indexed_bmp
from app.quantizer import circuitpython_indexed_image
from app.state.page_store import Panel


def _declared_native_size(panel: Panel) -> tuple[int, int] | None:
    """The client's framebuffer dims, but only when the device declared
    them (a manifest ``native_w``/``native_h`` block, or a ``rotation``
    sent at registration).

    Preset-inferred native dims are deliberately ignored here: a device
    already painting composition-shaped frames correctly, with the
    rotation handled on-device, would otherwise start receiving them
    turned 90° after an upgrade (issue #200)."""
    if panel.native_declared and panel.native_w is not None and panel.native_h is not None:
        return (panel.native_w, panel.native_h)
    return None


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Fit, contrast-adjust, quantize, and save the composition as an
    uncompressed indexed BMP at the panel's exact dims, or at the
    client's declared framebuffer dims when it sent a ``rotation``.

    Shares the pixel pipeline (fit, contrast, palette-quantise) with
    ``circuitpython_png`` via
    :func:`app.quantizer.circuitpython_indexed_image`; this renderer saves
    the result as an uncompressed BMP so the client never runs
    ``zlib.decompress``. :func:`app.bmp_writer.pack_indexed_bmp` packs the
    indexed image at the smallest standard bit depth that fits its palette
    (1 bpp for mono, 4 bpp for tri-colour / 4-grey / Spectra 6 / 7-colour),
    so the wire size is 2-8x smaller than Pillow's fixed 8-bit BMP. Output is
    BI_RGB (uncompressed) bottom-up, the shape ``adafruit_imageload`` reads;
    the full-colour rgb24/rgb16 passthrough falls back to a 24-bit BMP.
    """
    img = circuitpython_indexed_image(
        png_bytes,
        width=panel.w,
        height=panel.h,
        gamut=panel.gamut,
        flip=panel.flip,
        underscan=panel.underscan,
        settings=settings,
        native_size=_declared_native_size(panel),
    )
    return pack_indexed_bmp(img)


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    """Indexed-BMP output is self-contained; no rotation / scale / bg
    knobs in the wire payload since the renderer applied them already."""
    del settings
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.bmp"}
