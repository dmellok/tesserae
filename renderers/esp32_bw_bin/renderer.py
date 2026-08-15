"""esp32_bw_bin renderer.

Composition PNG -> mono-dithered 1-bpp raw buffer for B/W e-paper
panels driven by the generic ESP32 BW firmware (typical target: the
400x300 Waveshare 4.2" mono module). Published retained to
``tesserae/<device_id>/frame/bin`` so a freshly-woken client sees the
current frame on first wake.

Wire contract (must match the firmware byte-for-byte):

* Exactly ``width * height / 8`` bytes (15000 for 400x300). No header,
  no padding, no checksum.
* Scanline order, 8 pixels per byte, MSB = leftmost pixel.
* bit-set (1) = white, bit-clear (0) = black.

Pipeline mirrors ``trmnl_png`` (mono dither pre-pass) and
``esp32_bin`` (firmware-native orientation + flip + underscan + pack):

1. Use ``panel.native_w`` / ``panel.native_h`` if present, else fall
   back to ``(panel.w, panel.h)`` for custom panels (same fallback
   esp32_bin uses for unknown presets).
2. If the input image's orientation doesn't match the firmware's,
   rotate 90 deg CW so its left edge lands at the panel's top edge.
3. Apply ``panel.flip`` (180 deg for upside-down mounts).
4. Letterbox (white) to fit the firmware-native dims exactly.
5. Apply per-device underscan.
6. Dither + pack at the firmware-native dims via
   :func:`app.quantizer.pack_to_panel_bin_1bpp`.

The dither + contrast settings are flagged ``device_setting: true`` so
they live on the device card (Settings -> Devices -> Picture quality)
and each panel can be tuned independently.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from app.dither_regions import rasterize_region_mask, transform_mask
from app.quantizer import (
    fit_to_panel,
    pack_to_panel_bin_1bpp,
    underscan_image,
)
from app.state.page_store import Panel

DEFAULTS: dict[str, Any] = {
    "dither": "floyd-steinberg",
    "contrast": 1.0,
}


def _firmware_native_dims(panel: Panel) -> tuple[int, int]:
    """Return the firmware-native (w, h) for ``panel``.

    Same fallback policy as ``esp32_bin``: prefer the preset's
    ``native_w / native_h`` (populated upstream from PANEL_PRESETS or
    the device manifest); custom panels with neither hit fall back to
    ``(panel.w, panel.h)``."""
    if panel.native_w is not None and panel.native_h is not None:
        return (panel.native_w, panel.native_h)
    return (panel.w, panel.h)


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    img = Image.open(io.BytesIO(png_bytes))
    # Per-cell dither map (issue #86): rasterise at composition dims and
    # transform in lockstep so flat-UI regions snap to crisp mono instead of
    # a stippled grey. None keeps the pre-#86 path.
    regions = settings.get("_dither_regions")
    mask_img = None
    if regions:
        mask_img = rasterize_region_mask(regions, img.width, img.height)
    native_w, native_h = _firmware_native_dims(panel)
    firmware_landscape = native_w > native_h
    img_landscape = img.size[0] > img.size[1]
    orientation_mismatch = firmware_landscape != img_landscape
    if orientation_mismatch:
        # Orientation mismatch, rotate 90 deg CW so the input's left
        # edge lands at the panel's top edge. PIL ``rotate(angle)`` is
        # counter-clockwise, so -90 gives CW.
        img = img.rotate(-90, expand=True)
    if panel.flip:
        # Upside-down physical mount, turn 180 deg so it reads upright.
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
    # Palette-profile edge knobs. Only the native-colour guard applies on a
    # 2-colour panel: smoothing and line-art preservation are colour-packer
    # concerns, and a missing profile leaves this at 0 (off), so a device
    # without one renders byte-identical to before.
    edges = settings.get("_profile_edges") or {}
    return pack_to_panel_bin_1bpp(
        img,
        width=native_w,
        height=native_h,
        dither=_setting(settings, "dither"),
        contrast=float(_setting(settings, "contrast")),
        protect_native_colours=int(edges.get("protect_native_colours", 0)),
        region_nearest_mask=mask_img,
    )


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    del settings  # not part of the on-the-wire payload
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.bin"}
