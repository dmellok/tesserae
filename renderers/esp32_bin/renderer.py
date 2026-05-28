"""esp32_bin renderer.

Published retained to ``tesserae/esp32/frame/bin`` so a freshly-woken
ESP32 client sees the current frame on first wake.

Wire contract (Waveshare 13.3" Spectra 6 firmware, strict mode):

* Exactly ``width * height / 2`` bytes — 960000 for the 13.3" panel.
* **Portrait orientation always**: 1200 wide x 1600 tall pixel grid.
* 4-bpp packed, scanline order, no row padding. High nibble = even
  column, low nibble = odd column.

The firmware does **no** rotation, resize, or decode — it streams the
buffer straight to SPI. If we hand it a landscape-stride buffer the
panel paints a wrong-stride tile artefact (3x2 tiles of the source
visible on the panel). So:

1. If the input composition is landscape (W > H), rotate 90° CW so its
   left edge ends up at the panel's top edge. For typical dashboards
   text then reads top-to-bottom — sideways, but unavoidable on a
   portrait panel.
2. Letterbox (white) to fit whatever portrait dims the firmware wants.
3. Pack at the panel's native portrait orientation (smaller dim = width,
   larger = height) regardless of how the user has the panel oriented
   in app settings. The firmware grid is fixed; the user setting only
   affects the composition rendered upstream.

Portrait input passes through with no rotation — only the fit + pack
steps run, exactly like before.
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
    # Force portrait output regardless of how the user has panel.w / panel.h
    # configured — the Waveshare 13.3" firmware reads the buffer as
    # 1200x1600 portrait, period.
    native_w = min(panel.w, panel.h)
    native_h = max(panel.w, panel.h)
    if img.size[0] > img.size[1]:
        # Landscape composition → rotate 90° CW so it fills the portrait
        # panel (original left edge → new top edge). PIL ``rotate(angle)``
        # is counter-clockwise; ``-90`` gives CW.
        img = img.rotate(-90, expand=True)
    if panel.flip:
        # Upside-down physical mount — turn 180° so it reads upright.
        img = img.rotate(180, expand=True)
    if img.size != (native_w, native_h):
        img = fit_to_panel(img, target_w=native_w, target_h=native_h, scale="fit", bg="white")
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
