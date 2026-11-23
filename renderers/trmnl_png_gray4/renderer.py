"""trmnl_png_gray4 renderer.

Composition PNG -> 4-level greyscale dithered PNG at the device's panel
native dims, encoded as a true 2-bit greyscale PNG (IHDR bit depth 2,
colour type 0). The 4-grey sibling of ``trmnl_png`` and
``trmnl_png_gray16``: same fit / orient / underscan / contrast / dither
pipeline, quantised against the 4-entry grey ramp.

Why a separate renderer:

* TRMNL firmware 1.6.0 and later decodes a 2-bit greyscale PNG and drives
  the TRMNL 7.5 inch (OG) class of UC8179 panels in their 4-grey mode, so
  shading and photos read as greys instead of 1-bit speckle.
* Firmware before 1.6.0 cannot decode a 2-bit PNG at all, so this cannot
  replace ``trmnl_png`` for those panels. A kind that knows the firmware
  threshold lists it under ``renderer_min_firmware`` in its catalog
  ``protocol_config``, and ``app.trmnl_api`` moves the device on and off
  this renderer from the ``FW-Version`` header it sends on every poll.
* Pillow cannot write a 2-bit greyscale PNG (its sub-byte PNG paths are
  palette-only), so the container comes from :mod:`app.png_gray`. Each
  sample is the level index 0..3, which a decoder scales to 0, 85, 170,
  255.

Grey ramp: the nominal even ramp (``GRAY_4_PALETTE``) by default, or the
device's calibration profile ramp under ``settings["_gray_ramp"]``
interpolated to 4 levels, same contract as ``esp32_gray2_bin``. The ramp
only steers which level each pixel gets; the PNG still carries indices.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageEnhance

from app.dither_regions import buffer_flat_mask, paste_flat
from app.png_gray import encode_gray_png, level_indices
from app.quantizer import (
    GRAY_4_PALETTE,
    fit_to_panel,
    orient_onto_buffer,
    quantize,
    underscan_image,
)
from app.state.page_store import Panel

DEFAULTS: dict[str, Any] = {
    "dither": "floyd-steinberg",
    "contrast": 1.0,
}

_LEVELS = 4


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def _grey_ramp(settings: dict[str, Any]) -> tuple[tuple[int, int, int], ...]:
    """The 4-entry grey palette to quantise against: the device's measured
    ramp when a greyscale calibration profile is applied, else nominal."""
    anchors = settings.get("_gray_ramp")
    if anchors:
        from app.palette_profiles.schema import GrayRamp

        resolved = GrayRamp(levels=tuple(str(v) for v in anchors)).as_tuples(_LEVELS)
        if resolved is not None:
            return resolved
    return GRAY_4_PALETTE


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Fit + dither the composition onto the device's buffer as a 2-bit
    greyscale PNG. Geometry handling matches ``trmnl_png``: fit to the
    composition dims, turn onto the buffer the client reported, flip,
    underscan."""
    img = Image.open(io.BytesIO(png_bytes))
    fit = str(settings.get("image_fit") or "fit")

    if img.size != (panel.w, panel.h):
        img = fit_to_panel(img, target_w=panel.w, target_h=panel.h, scale=fit, bg="white")

    # Only a buffer the device itself reported counts (see trmnl_png).
    native_w, native_h = panel.declared_native or (panel.w, panel.h)
    # A Flat cell is drawn in the nearest ink, with no dither (#601).
    flat_mask = buffer_flat_mask(
        settings,
        comp_w=img.width,
        comp_h=img.height,
        buffer_w=native_w,
        buffer_h=native_h,
        flip=panel.flip,
        underscan=panel.underscan,
    )
    img = orient_onto_buffer(img, buffer_w=native_w, buffer_h=native_h, flip=panel.flip)

    if panel.underscan:
        img = underscan_image(img, underscan=panel.underscan)

    contrast = float(_setting(settings, "contrast"))
    if abs(contrast - 1.0) > 1e-6:
        img = ImageEnhance.Contrast(img.convert("L")).enhance(contrast).convert("RGB")

    ramp = _grey_ramp(settings)
    grey_rgb = quantize(img, dither=str(_setting(settings, "dither")), palette=ramp)
    if flat_mask is not None:
        grey_rgb = paste_flat(grey_rgb, quantize(img, dither="none", palette=ramp), flat_mask)
    return encode_gray_png(level_indices(grey_rgb, ramp), bit_depth=2)


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    """The artifact URL the device will fetch; ``/api/display`` serves it
    inside its own envelope, same shape as the other TRMNL renderers."""
    del settings
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.png"}
