"""esp32_bin renderer.

Published retained to ``tesserae/esp32/frame/bin`` so a freshly-woken
ESP32 client sees the current frame on first wake.

Wire contract:

* Exactly ``width * height / 2`` bytes, 960000 for the 13.3" Waveshare,
  192000 for the 7.3" PhotoPainter.
* Buffer is packed at the panel's firmware-native row stride
  (``panel.native_w × panel.native_h``), NOT at the composition
  orientation the user picked during calibration. The firmware streams
  the bytes straight to SPI with no resize / rotate, so the bin's row
  stride must match the panel hardware regardless of how the user
  mounts the screen.

  ``native_w / native_h`` are populated by ``app.panel``'s
  ``resolve_settings_panel`` / ``_device_panel`` from the panel preset
  or the device manifest. Custom / unknown panels leave them as None;
  the renderer then falls back to packing at ``(panel.w, panel.h)``
  directly, matching pre-v0.19.19 behaviour for those cases.

* 4-bpp packed, scanline order, no row padding. High nibble = even
  column, low nibble = odd column.

Pipeline:

1. Use ``panel.native_w / panel.native_h`` if present, else fall back
   to ``(panel.w, panel.h)`` for custom panels.
2. If the input image's orientation doesn't match the firmware's,
   rotate 90° CW so its left edge lands at the panel's top edge.
3. Apply ``panel.flip`` (180° for upside-down mounts).
4. Letterbox (white) to fit the firmware-native dims exactly.
5. Apply per-device underscan.
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
    "calibrated": False,
}


def _firmware_native_dims(panel: Panel) -> tuple[int, int]:
    """Return the firmware-native (w, h) for ``panel``.

    Prefers ``panel.native_w / panel.native_h`` (populated upstream
    from PANEL_PRESETS / device manifest). Custom panels with no
    preset hit and no manifest hint fall back to the composition
    dims, matching pre-v0.19.19 behaviour: those users had to mount
    the panel in the firmware-native orientation to avoid stride
    drift, and they still do."""
    if panel.native_w is not None and panel.native_h is not None:
        return (panel.native_w, panel.native_h)
    return (panel.w, panel.h)


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    img = Image.open(io.BytesIO(png_bytes))
    native_w, native_h = _firmware_native_dims(panel)
    firmware_landscape = native_w > native_h
    img_landscape = img.size[0] > img.size[1]
    if firmware_landscape != img_landscape:
        # Orientation mismatch (either input PNG or user-calibrated
        # composition), rotate 90° CW so the input's left edge lands
        # at the panel's top edge. PIL ``rotate(angle)`` is
        # counter-clockwise; ``-90`` gives CW.
        img = img.rotate(-90, expand=True)
    if panel.flip:
        # Upside-down physical mount, turn 180° so it reads upright.
        img = img.rotate(180, expand=True)
    if img.size != (native_w, native_h):
        # Send-page per-push fit mode (fit/fill/stretch/center/blur).
        fit = str(settings.get("image_fit") or "fit")
        img = fit_to_panel(img, target_w=native_w, target_h=native_h, scale=fit, bg="white")
    # Per-device underscan: inset content so it clears a physical mat/bezel.
    if panel.underscan:
        img = underscan_image(img, underscan=panel.underscan)
    # Profile tone / dither knobs (v0.67.1). Populated by app.push
    # only when the device has a Calibration-tab profile applied;
    # missing keys keep the pre-v0.67 defaults so no-profile devices
    # render byte-identical to before.
    tone = settings.get("_profile_tone") or {}
    dither_extras = settings.get("_profile_dither") or {}
    return pack_to_panel_bin(
        img,
        width=native_w,
        height=native_h,
        dither=_setting(settings, "dither"),
        saturation=float(_setting(settings, "saturation")),
        contrast=float(_setting(settings, "contrast")),
        calibrated=bool(_setting(settings, "calibrated")),
        # Populated by :mod:`app.push` from the device's active
        # Calibration-tab palette profile. When ``None`` (no profile
        # applied), the quantizer falls back to the built-in calibrated
        # palette for the gamut, same as pre-v0.67 behaviour.
        palette_override=settings.get("_palette_override"),
        exposure=int(tone.get("exposure", 0)),
        s_curve=int(tone.get("s_curve", 0)),
        serpentine=bool(dither_extras.get("serpentine", False)),
        diffusion_strength=int(dither_extras.get("diffusion_strength", 100)),
    )


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    del settings  # not part of the on-the-wire payload
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.bin"}
