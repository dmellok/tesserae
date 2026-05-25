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
    "saturation": 1.0,
    "contrast": 1.0,
}


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    img = Image.open(io.BytesIO(png_bytes))
    if img.size != (panel.w, panel.h):
        # The composer hands us panel-sized output; the Send page (M7) feeds
        # arbitrary uploads through here. Fit before packing — the firmware
        # rejects any buffer size that isn't exactly panel.w * panel.h * 4bpp.
        img = fit_to_panel(img, target_w=panel.w, target_h=panel.h, scale="fit", bg="white")
    return pack_to_panel_bin(
        img,
        width=panel.w,
        height=panel.h,
        dither=_setting(settings, "dither"),
        saturation=float(_setting(settings, "saturation")),
        contrast=float(_setting(settings, "contrast")),
    )


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    del settings  # not part of the on-the-wire payload
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.bin"}
