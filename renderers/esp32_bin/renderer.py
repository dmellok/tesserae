"""esp32_bin renderer.

Published retained to ``tesserae/esp32/frame/bin`` so a freshly-woken
ESP32 client sees the current frame on first wake.

Wire contract:

* Exactly ``width * height / 2`` bytes — 960000 for the 13.3" Waveshare,
  192000 for the 7.3" PhotoPainter.
* Buffer is packed at the **panel's firmware-native hardware
  orientation** — NOT at the composition orientation the user picked
  during calibration. The firmware streams the bytes straight to SPI
  with no resize / rotate, so the bin's row stride must match the
  panel hardware. Two shipped cases:

  * Waveshare 13.3" Spectra 6 — portrait native: 1200 wide × 1600
    tall.
  * Waveshare 7.3" PhotoPainter (ESP32-S3) — landscape native: 800
    wide × 480 tall.

  Packing at the user's composition orientation paints garbled
  vertical ghosts when calibration disagrees with the panel hardware
  (e.g. user picks portrait calibration on the landscape-native
  PhotoPainter).

* 4-bpp packed, scanline order, no row padding. High nibble = even
  column, low nibble = odd column.

Pipeline:

1. Resolve the firmware-native (w, h) from the panel's pixel count.
   Both supported ESP32 panels have unique pixel counts so the
   lookup is unambiguous. Unknown sizes fall back to packing at the
   panel arg unchanged, matching pre-v0.19.19 behaviour.
2. If the input image's orientation doesn't match the firmware's,
   rotate 90° CW so its left edge lands at the panel's top edge.
   This rotation covers both: (a) compositions rendered at the
   user's calibration orientation that doesn't match the firmware,
   and (b) ad-hoc input PNGs that arrive at a non-matching shape.
3. Apply ``panel.flip`` (180° rotation for upside-down mounts).
4. Letterbox (white) to fit the firmware-native dims exactly.
5. Apply per-device underscan if set.
6. Pack at the firmware-native dims.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from app.quantizer import fit_to_panel, pack_to_panel_bin, underscan_image
from app.state.page_store import Panel

DEFAULTS: dict[str, Any] = {
    "dither": "floyd-steinberg",
    "saturation": 1.0,
    "contrast": 1.0,
}

# Firmware-native (w, h) per supported ESP32 panel, keyed by pixel
# count. The panel hardware fixes its row stride; the user's
# calibration orientation only determines the composition canvas.
# Looking up by area lets us accept the panel arg in either
# orientation (e.g. (800, 480) or (480, 800) both resolve to the
# PhotoPainter's 800w × 480h native stride).
_PANEL_FIRMWARE_NATIVE: dict[int, tuple[int, int]] = {
    800 * 480: (800, 480),  # Waveshare 7.3" PhotoPainter — landscape
    1200 * 1600: (1200, 1600),  # Waveshare 13.3" Spectra 6 — portrait
}


def _firmware_native_dims(panel: Panel) -> tuple[int, int]:
    """Return the firmware-native (w, h) for ``panel``.

    Looked up by pixel count from ``_PANEL_FIRMWARE_NATIVE``. An
    unknown size (custom panel, future hardware) falls back to the
    panel arg unchanged — the caller assumes the firmware stride
    matches whatever orientation the user has configured."""
    return _PANEL_FIRMWARE_NATIVE.get(panel.w * panel.h, (panel.w, panel.h))


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    img = Image.open(io.BytesIO(png_bytes))
    native_w, native_h = _firmware_native_dims(panel)
    firmware_landscape = native_w > native_h
    img_landscape = img.size[0] > img.size[1]
    if firmware_landscape != img_landscape:
        # Orientation mismatch (either input PNG or user-calibrated
        # composition) — rotate 90° CW so the input's left edge lands
        # at the panel's top edge. PIL ``rotate(angle)`` is
        # counter-clockwise; ``-90`` gives CW.
        img = img.rotate(-90, expand=True)
    if panel.flip:
        # Upside-down physical mount — turn 180° so it reads upright.
        img = img.rotate(180, expand=True)
    if img.size != (native_w, native_h):
        # Send-page per-push fit mode (fit/fill/stretch/center/blur).
        fit = str(settings.get("image_fit") or "fit")
        img = fit_to_panel(img, target_w=native_w, target_h=native_h, scale=fit, bg="white")
    # Per-device underscan: inset content so it clears a physical mat/bezel.
    if panel.underscan:
        img = underscan_image(img, underscan=panel.underscan)
    return pack_to_panel_bin(
        img,
        width=native_w,
        height=native_h,
        dither=_setting(settings, "dither"),
        saturation=float(_setting(settings, "saturation")),
        contrast=float(_setting(settings, "contrast")),
    )


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    del settings  # not part of the on-the-wire payload
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.bin"}
