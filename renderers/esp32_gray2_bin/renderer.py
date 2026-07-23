"""esp32_gray2_bin renderer.

Composition PNG -> 4-level grayscale dither -> raw 2-bpp packed buffer
for UC8179-class mono e-paper panels driven in their 4-gray waveform
mode (the Seeed reTerminal E1001's 7.5" 800x480 EP75 in particular).
Published retained to ``tesserae/<device_id>/frame/bin`` so a
freshly-woken client sees the current frame on first wake.

Wire contract (must match the firmware byte-for-byte, see
:func:`app.quantizer.pack_to_panel_bin_2bpp_gray`):

* Exactly ``width * height / 4`` bytes (96000 for the E1001's 800x480
  panel). No header, no padding, no checksum.
* Row-major, top-left origin, no mirror. The firmware handles any
  physical panel-side mirror itself.
* 2-bpp packed, scanline order, no row padding: ``width / 4`` bytes
  per row.
* **MSB first**: bits 7-6 = leftmost pixel of each 4-pixel group,
  bits 1-0 = rightmost.
* Gray value per 2-bit field: **0b00 = black, 0b11 = white**, linear.

Pipeline mirrors ``esp32_gray_bin`` (firmware-native orientation +
flip + underscan + pack), differing only in the palette (4-level vs
16-level gray) and packer.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from app.dither_regions import rasterize_region_mask, transform_mask
from app.quantizer import (
    fit_to_panel,
    pack_to_panel_bin_2bpp_gray,
    underscan_image,
)
from app.state.page_store import Panel

DEFAULTS: dict[str, Any] = {
    "dither": "floyd-steinberg",
    "contrast": 1.0,
}


def _firmware_native_dims(panel: Panel) -> tuple[int, int]:
    """Return the firmware-native (w, h) for ``panel``. Same fallback
    policy as the other esp32 packers: prefer the preset's
    ``native_w / native_h``, else fall back to ``(panel.w, panel.h)``
    for custom panels."""
    if panel.native_w is not None and panel.native_h is not None:
        return (panel.native_w, panel.native_h)
    return (panel.w, panel.h)


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    img = Image.open(io.BytesIO(png_bytes))
    # Per-cell dither map (issue #86): rasterise at composition dims and
    # transform in lockstep so flat-UI regions snap to a clean gray level
    # instead of picking up diffusion speckle. None keeps the pre-#86 path.
    regions = settings.get("_dither_regions")
    mask_img = None
    if regions:
        mask_img = rasterize_region_mask(regions, img.width, img.height)
    native_w, native_h = _firmware_native_dims(panel)
    firmware_landscape = native_w > native_h
    img_landscape = img.size[0] > img.size[1]
    orientation_mismatch = firmware_landscape != img_landscape
    if orientation_mismatch:
        # Orientation mismatch: rotate 90 deg CW so the input's left
        # edge lands at the panel's top edge (same convention as
        # esp32_bin / esp32_bw_bin / esp32_gray_bin).
        img = img.rotate(-90, expand=True)
    if panel.flip:
        # Upside-down physical mount; turn 180 deg so it reads upright.
        img = img.rotate(180, expand=True)
    if img.size != (native_w, native_h):
        fit = str(settings.get("image_fit") or "fit")
        img = fit_to_panel(img, target_w=native_w, target_h=native_h, scale=fit, bg="white")
    if panel.underscan:
        img = underscan_image(img, underscan=panel.underscan)
    if mask_img is not None:
        mask_img = transform_mask(
            mask_img,
            native_w=native_w,
            native_h=native_h,
            rotate=-90 if orientation_mismatch else 0,
            flip=bool(panel.flip),
            underscan=int(panel.underscan or 0),
        )
    return pack_to_panel_bin_2bpp_gray(
        img,
        width=native_w,
        height=native_h,
        dither=_setting(settings, "dither"),
        contrast=float(_setting(settings, "contrast")),
        region_nearest_mask=mask_img,
    )


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    del settings  # not part of the on-the-wire payload
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.bin"}
