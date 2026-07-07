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

from PIL import Image, ImageEnhance

from app.quantizer import (
    BWR_3_PALETTE,
    BWRY_4_PALETTE,
    GRAY_4_PALETTE,
    INKY_7COLOUR_PALETTE,
    SPECTRA_6_PALETTE,
    fit_to_panel,
    underscan_image,
)
from app.state.page_store import Panel

_MONO_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),
    (255, 255, 255),
)

DEFAULTS: dict[str, Any] = {
    "dither": "floyd-steinberg",
    "contrast": 1.0,
}

# Pillow's ``Image.quantize`` only accepts Floyd-Steinberg or None via
# its ``dither`` kwarg. The numpy-backed dither modes (Atkinson, Jarvis,
# halftone, etc.) are bin-packer-only, so this renderer's manifest
# narrows the choice list to match what Pillow can actually do here.
_PIL_DITHER_MAP: dict[str, Image.Dither] = {
    "floyd-steinberg": Image.Dither.FLOYDSTEINBERG,
    "none": Image.Dither.NONE,
}


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def _palette_for(gamut: str | None) -> tuple[tuple[int, int, int], ...] | None:
    """Map a panel's declared gamut to an RGB palette tuple, or ``None``
    when the gamut wants a full-colour (unquantised) output. Unknown
    or empty gamuts fall through to Spectra 6 nominal so the renderer
    is forgiving of custom panels that haven't named their gamut.

    Aliases handled:
      * ``mono`` -> black + white
      * ``spectra_6`` / ``waveshare_e6`` / ``e6`` -> Spectra 6
      * ``acep_7colour`` / ``acep_7color`` / ``inky_7colour`` -> 7-colour
      * ``bwry_4`` -> 4-colour BWRY (v0.69.3 for PicPak-class panels)
      * ``bwr_3`` -> 3-colour black/white/red tri-colour e-ink
      * ``gray_4`` -> 4-level greyscale ramp (2-bit, no highlight)
      * ``rgb24`` / ``rgb16`` -> None (24-bit RGB PNG passthrough,
        v0.69.1 per issue #41)
    """
    g = (gamut or "").lower()
    if g == "mono":
        return _MONO_PALETTE
    if g == "bwry_4":
        return BWRY_4_PALETTE
    if g == "bwr_3":
        return BWR_3_PALETTE
    if g == "gray_4":
        return GRAY_4_PALETTE
    if g in ("acep_7colour", "acep_7color", "inky_7colour"):
        return INKY_7COLOUR_PALETTE
    if g in ("rgb24", "rgb16"):
        return None
    # Spectra 6, Waveshare E6, e6, or anything else: 6-colour nominal.
    return SPECTRA_6_PALETTE


def _make_palette_image(palette: tuple[tuple[int, int, int], ...]) -> Image.Image:
    """Build the Pillow palette image that ``Image.quantize`` expects.
    Pads the unused entries with black so Pillow's quantiser sees a
    full 256-entry table without those entries ever being selected
    (the palette tuples themselves are short, 2 to 7 entries)."""
    pal_img = Image.new("P", (16, 16))
    flat: list[int] = []
    for r, g, b in palette:
        flat.extend((r, g, b))
    flat.extend([0, 0, 0] * (256 - len(palette)))
    pal_img.putpalette(flat)
    return pal_img


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Fit, contrast-adjust, quantize, and save the composition PNG as
    an indexed PNG at the panel's exact dims.

    Output is palette-mode PNG so ``adafruit_imageload`` (and any other
    paletted-PNG loader) mounts it with minimal RAM. The device paints
    what arrives, no on-device quantize, no dither, no nibble unpack.
    """
    img = Image.open(io.BytesIO(png_bytes))
    target_w, target_h = panel.w, panel.h

    if panel.flip:
        # Upside-down physical mount: turn the whole image 180° so it
        # reads upright on the wall. Same shape as the other PNG
        # renderers.
        img = img.rotate(180, expand=True)

    if img.size != (target_w, target_h):
        # Composer pre-sizes pages to ``panel.w x panel.h`` so this is
        # usually a no-op. It only does real work on Send-page image
        # pushes where the user's input PNG isn't panel-sized.
        fit = str(settings.get("image_fit") or "fit")
        img = fit_to_panel(img, target_w=target_w, target_h=target_h, scale=fit, bg="white")

    if panel.underscan:
        # Per-device underscan: inset rendered content so it clears a
        # physical bezel or mat covering the panel edge.
        img = underscan_image(img, underscan=panel.underscan)

    contrast = float(_setting(settings, "contrast"))
    if abs(contrast - 1.0) > 1e-6:
        # Pre-dither contrast push, same idea as trmnl_png: bumping
        # contrast forces more pixels to definite black or definite
        # white before the dither pass runs, which tends to read
        # better on text-heavy dashboards.
        img = ImageEnhance.Contrast(img.convert("L")).enhance(contrast).convert("RGB")

    palette = _palette_for(panel.gamut)
    buf = io.BytesIO()
    if palette is None:
        # rgb24 / rgb16 (v0.69.1): emit a plain 24-bit RGB PNG. Skips
        # both palette-quantise and dither, so the client gets the
        # composition's full colour range on the wire. rgb16 panels
        # pack down to RGB565 in firmware (a Python one-liner:
        # ``((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)`` per
        # pixel); a raw RGB565 wire format is a bandwidth-only
        # follow-up.
        img.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    pal_img = _make_palette_image(palette)
    dither_mode = _PIL_DITHER_MAP.get(
        str(_setting(settings, "dither")), Image.Dither.FLOYDSTEINBERG
    )
    # ``Image.quantize`` keeps palette mode ("P"), which is what we
    # want: the saved PNG carries an indexed pixel format that
    # adafruit_imageload reads natively. We deliberately do not
    # ``.convert("RGB")`` afterwards (that's what ``quantize_to_png``
    # in app/quantizer.py does, for the bin-packer feed path).
    indexed = img.convert("RGB").quantize(palette=pal_img, dither=dither_mode)

    indexed.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    """Indexed-PNG output is self-contained; no rotation / scale / bg
    knobs in the wire payload since the renderer applied them already."""
    del settings
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.png"}
