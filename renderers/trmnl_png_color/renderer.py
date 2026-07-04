"""trmnl_png_color renderer.

Composition PNG -> palette-quantised indexed PNG at the panel's exact
dims, delivered over the TRMNL BYOS /api/display path. Targets colour
ePaper panels that ship with TRMNL-compatible firmware, primarily the
Seeed reTerminal E1002 (7.3-inch Spectra 6) and future colour SKUs
that speak the same wire protocol.

Why a separate renderer from ``trmnl_png``:

* ``trmnl_png`` outputs 1-bit greyscale for TRMNL OG / X and the
  reTerminal E1001 / E1003 (monochrome panels), with 8 dither modes
  sized for text-first dashboards.
* Colour panels want an *indexed* PNG whose palette matches the
  panel's ink primaries. The TRMNL firmware for these boards decodes
  indexed PNG directly (``PNG_PIXEL_INDEXED`` branch in
  ``src/display.cpp``) and maps each palette entry to the closest of
  6 Spectra anchor colours via a precomputed lookup table. Sending
  1-bit output to a Spectra 6 panel wastes the panel's colour
  capability; sending 24-bit RGB works but doubles file size and hands
  the dither quality decision to firmware code that has no dither.
* Server-side Floyd-Steinberg against the target palette gives the
  smoothest gradients the panel can render; the device just paints
  what arrives.

Output palette selected from the bound panel's gamut. Aliases follow
``circuitpython_png`` conventions:

* ``mono`` -> 1-bit black + white (rendered here only for
  completeness; a mono TRMNL panel should route through ``trmnl_png``
  which has a richer dither palette).
* ``waveshare_e6`` / ``spectra_6`` / ``e6`` -> 6-colour Waveshare E6
  nominal (matches the E1002 firmware's Spectra 6 palette anchors).
* ``acep_7colour`` / ``inky_7colour`` -> 7-colour ACeP.
* Unknown / custom gamut -> Waveshare E6 nominal fallback.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageEnhance

from app.quantizer import (
    INKY_7COLOUR_PALETTE,
    WAVESHARE_E6_PALETTE,
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
# its ``dither`` kwarg. The manifest narrows the choice list to match
# what Pillow can actually do here; multi-mode dither (Atkinson,
# halftone, etc.) lives in the numpy-backed bin-packer feed path.
_PIL_DITHER_MAP: dict[str, Image.Dither] = {
    "floyd-steinberg": Image.Dither.FLOYDSTEINBERG,
    "none": Image.Dither.NONE,
}


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def _palette_for(gamut: str | None) -> tuple[tuple[int, int, int], ...]:
    """Map a panel's declared gamut to an RGB palette tuple. Colour
    TRMNL panels (E1002 today) live on Spectra 6; the fallback is
    Waveshare E6 nominal rather than mono so an unlabelled colour SKU
    still produces indexed colour rather than accidentally 1-bit."""
    g = (gamut or "").lower()
    if g == "mono":
        return _MONO_PALETTE
    if g in ("acep_7colour", "acep_7color", "inky_7colour"):
        return INKY_7COLOUR_PALETTE
    return WAVESHARE_E6_PALETTE


def _make_palette_image(palette: tuple[tuple[int, int, int], ...]) -> Image.Image:
    """Build the Pillow palette image that ``Image.quantize`` expects.
    Pads the unused entries with black so Pillow's quantiser sees a
    full 256-entry table without those entries ever being selected."""
    pal_img = Image.new("P", (16, 16))
    flat: list[int] = []
    for r, g, b in palette:
        flat.extend((r, g, b))
    flat.extend([0, 0, 0] * (256 - len(palette)))
    pal_img.putpalette(flat)
    return pal_img


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Fit, contrast-adjust, quantise, and save the composition PNG as
    an indexed PNG at the panel's exact dims.

    Output is palette-mode PNG. The TRMNL firmware's PNG decoder
    (``PNG_PIXEL_INDEXED`` case in src/display.cpp) reads the palette
    verbatim and maps each entry to the closest Spectra 6 anchor via
    ``GetSpectraPixel``; using standard-primary RGB values (255, 0, 0)
    etc. lands each entry on its intended Spectra anchor (red, blue,
    yellow, green, black, white).
    """
    img = Image.open(io.BytesIO(png_bytes))
    target_w, target_h = panel.w, panel.h

    if panel.flip:
        img = img.rotate(180, expand=True)

    if img.size != (target_w, target_h):
        fit = str(settings.get("image_fit") or "fit")
        img = fit_to_panel(img, target_w=target_w, target_h=target_h, scale=fit, bg="white")

    if panel.underscan:
        img = underscan_image(img, underscan=panel.underscan)

    contrast = float(_setting(settings, "contrast"))
    if abs(contrast - 1.0) > 1e-6:
        # Pre-dither contrast push. Same rationale as trmnl_png /
        # circuitpython_png: bumping contrast forces more pixels to
        # definite bucket colours before the error-diffusion pass,
        # which reads better on text-heavy dashboards.
        img = ImageEnhance.Contrast(img.convert("L")).enhance(contrast).convert("RGB")

    # Calibration-tab palette profile: when the app.push layer resolves
    # a device profile it injects RGB tuples under ``_palette_override``.
    # Snap to that palette instead of the built-in gamut default so
    # server-side dither aims at the panel's measured colours; on-wire
    # PNG-palette entries just shift RGB values, the firmware's Spectra
    # anchor mapping still recognises them by nearest primary.
    override = settings.get("_palette_override")
    palette = override or _palette_for(panel.gamut)
    pal_img = _make_palette_image(palette)
    dither_mode = _PIL_DITHER_MAP.get(
        str(_setting(settings, "dither")), Image.Dither.FLOYDSTEINBERG
    )
    indexed = img.convert("RGB").quantize(palette=pal_img, dither=dither_mode)

    buf = io.BytesIO()
    indexed.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    """Indexed-PNG output is self-contained; no rotation / scale / bg
    knobs in the wire payload since the renderer applied them already."""
    del settings
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.png"}
