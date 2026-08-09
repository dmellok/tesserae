"""circuitpython_png renderer.

Composition PNG -> palette-quantized indexed PNG at the panel's exact
dims. Targets CircuitPython clients on memory-constrained
microcontrollers (Pico-W, ESP32-S3 / -C3 / -C6, nRF52840) where the
nibble-packed ``.bin`` format isn't viable (no general-purpose decoder
in the CircuitPython ecosystem, and the packed buffer plus a decode
scratch buffer would exhaust SRAM on a Pico-W). The output is an
indexed PNG that ``adafruit_imageload`` mounts directly with minimal
RAM, so the device just paints what arrives.

Output palette selected from the bound panel's gamut:

* ``mono``                             -> 1-bit black + white
* ``bwr_3``                            -> 3-colour black/white/red
* ``gray_4``                           -> 4-level greyscale ramp (2-bit)
* ``bwry_4``                           -> 4-colour black/white/red/yellow
* ``spectra_6`` / ``waveshare_e6``     -> 6-colour Spectra 6
* ``acep_7colour`` / ``inky_7colour``  -> 7-colour ACeP
* ``rgb24`` / ``rgb16``                -> plain 24-bit RGB PNG,
                                          no quantisation (v0.69.1
                                          per issue #41). rgb16
                                          panels pack the 24-bit
                                          RGB to RGB565 on-device;
                                          a raw RGB565 wire format
                                          is a bandwidth-only
                                          follow-up.

Unknown or custom gamuts fall back to Spectra 6 nominal so a panel
that just hasn't declared its gamut yet still produces a sensible
indexed image rather than 8-bit RGB.

Same per-device settings shape as ``trmnl_png``: a small select for
the dither mode and a slider for pre-dither contrast. Calibrated
palettes are deferred to a follow-up; v0.1.0 always uses the nominal
palette so the wire format is deterministic per gamut.
"""

from __future__ import annotations

import io
from typing import Any

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
    """Fit, contrast-adjust, quantize, and save the composition PNG as
    an indexed PNG at the panel's exact dims, or at the client's
    declared framebuffer dims when it sent a ``rotation``.

    Output is palette-mode PNG so ``adafruit_imageload`` (and any other
    paletted-PNG loader) mounts it with minimal RAM. The device paints
    what arrives, no on-device quantize, no dither, no nibble unpack.

    The pixel pipeline (fit, contrast, palette-quantise) is shared with
    the ``circuitpython_bmp`` renderer via
    :func:`app.quantizer.circuitpython_indexed_image`; this renderer
    just saves the result as an indexed PNG.
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
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    """Indexed-PNG output is self-contained; no rotation / scale / bg
    knobs in the wire payload since the renderer applied them already."""
    del settings
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.png"}
