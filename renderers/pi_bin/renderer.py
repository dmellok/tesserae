"""pi_bin renderer.

Composition PNG packed into the Waveshare E6 4-bpp buffer for the
``.bin``-mode Pi client. Identical packed bytes to ``esp32_bin``;
content-addressed disk storage means both renderers share a single file
on disk when both targets are active.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from app.quantizer import fit_to_panel, pack_to_panel_bin
from app.state.page_store import Panel

DEFAULTS: dict[str, Any] = {
    "dither": "floyd-steinberg",
    # Match renderer.json — Spectra 6's tiny palette needs a boost
    # before quantise to avoid washed-out output.
    "saturation": 1.4,
    "contrast": 1.0,
}


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Pack a composition PNG into the panel's native landscape 4-bpp buffer.

    The Inky / Waveshare E6 panels are always landscape-native (the
    pixel grid is W>H). Even when the user wants their dashboard
    displayed portrait, the buffer the firmware reads back has to be
    laid out in landscape — same byte count either way, but a portrait
    layout has the wrong row stride and the panel prints rotated +
    ghosted scanlines.

    So: take the composition (whatever orientation it arrived in),
    rotate 90° CW if it's portrait, and always pack at the panel's
    native landscape dimensions.
    """
    img = Image.open(io.BytesIO(png_bytes))
    # Native landscape dims regardless of which way the user has the
    # panel oriented in settings.
    native_w = max(panel.w, panel.h)
    native_h = min(panel.w, panel.h)
    quarters = panel.rotation_quarters
    if quarters is not None:
        # Explicit per-device rotation (multi-head). rotation_quarters is
        # clockwise; PIL rotate() is counter-clockwise, so negate. q=3
        # (270° CW) reproduces the legacy portrait turn below.
        if quarters % 4:
            img = img.rotate(-90 * quarters, expand=True)
    elif panel.w < panel.h:
        # Auto (no explicit rotation): panel mounted portrait — every
        # composition (portrait OR square) needs a 90° CCW pre-rotation
        # so the top of the composition maps to the left edge of the
        # landscape buffer the firmware reads.
        img = img.rotate(90, expand=True)
    if img.size != (native_w, native_h):
        # Send-page uploads aren't panel-sized; fit before packing.
        img = fit_to_panel(img, target_w=native_w, target_h=native_h, scale="fit", bg="white")
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
