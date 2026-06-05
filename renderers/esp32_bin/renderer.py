"""esp32_bin renderer.

Published retained to ``tesserae/esp32/frame/bin`` so a freshly-woken
ESP32 client sees the current frame on first wake.

Wire contract:

* Exactly ``width * height / 2`` bytes — 960000 for the 13.3" Waveshare,
  192000 for the 7.3" PhotoPainter.
* Buffer is packed at the **panel's native hardware orientation** —
  ``panel.w × panel.h`` directly. The firmware streams the bytes
  straight to SPI with no resize / rotate, so the device record's
  panel.w / panel.h must match the firmware's hardware-configured row
  stride. Two shipped cases:

  * Waveshare 13.3" Spectra 6 — portrait native: ``panel.w = 1200,
    panel.h = 1600``. The bin is 1200 wide × 1600 tall.
  * Waveshare 7.3" PhotoPainter (ESP32-S3) — landscape native:
    ``panel.w = 800, panel.h = 480``. The bin is 800 wide × 480 tall.

  Packing at min/max-swapped dims (the old "force portrait" path)
  paints garbled vertical ghosts on the 7.3" PhotoPainter because the
  firmware feeds it a 400-byte/row stride and the renderer was
  emitting 240-byte rows.

* 4-bpp packed, scanline order, no row padding. High nibble = even
  column, low nibble = odd column.

Pipeline:

1. If the input composition's orientation doesn't match the panel's
   (i.e. input landscape vs panel portrait, or vice versa), rotate
   90° CW so the input's left edge lands at the panel's top edge. On
   matching orientations the input passes through.
2. Apply ``panel.flip`` (180° rotation for upside-down mounts).
3. Letterbox (white) to fit ``panel.w × panel.h`` exactly.
4. Apply per-device underscan if set.
5. Pack at ``panel.w × panel.h``.
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


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    img = Image.open(io.BytesIO(png_bytes))
    # Pack at the panel's native dims — see module docstring for why
    # the old min/max swap was wrong for landscape-native devices.
    native_w = panel.w
    native_h = panel.h
    panel_landscape = native_w > native_h
    img_landscape = img.size[0] > img.size[1]
    if panel_landscape != img_landscape:
        # Orientation mismatch — rotate 90° CW so the input's left edge
        # lands at the panel's top edge. PIL ``rotate(angle)`` is
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
