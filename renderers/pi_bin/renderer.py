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

from app.dither_regions import rasterize_region_mask, transform_mask
from app.quantizer import fit_to_panel, pack_to_panel_bin, underscan_image
from app.state.page_store import Panel

DEFAULTS: dict[str, Any] = {
    "dither": "floyd-steinberg",
    # Match renderer.json, Spectra 6's tiny palette needs a boost
    # before quantise to avoid washed-out output.
    "saturation": 1.4,
    "contrast": 1.0,
    "calibrated": False,
}


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Pack a composition PNG into the panel's native landscape 4-bpp buffer.

    The Inky / Waveshare E6 panels are always landscape-native (the
    pixel grid is W>H). Even when the user wants their dashboard
    displayed portrait, the buffer the firmware reads back has to be
    laid out in landscape, same byte count either way, but a portrait
    layout has the wrong row stride and the panel prints rotated +
    ghosted scanlines.

    So: take the composition (whatever orientation it arrived in),
    rotate 90° CW if it's portrait, and always pack at the panel's
    native landscape dimensions.
    """
    img = Image.open(io.BytesIO(png_bytes))
    # Per-cell dither map (issue #86). The composer rasterises each cell's
    # ``render.dither`` hint into a mask at composition dims; we transform it
    # in lockstep with the image below so it stays pixel-aligned through the
    # portrait/flip/fit/underscan pipeline, then hand it to the packer. None
    # (Send-page uploads, all-diffuse dashboards) keeps the pre-#86 path.
    regions = settings.get("_dither_regions")
    mask_img = None
    if regions:
        mask_img = rasterize_region_mask(regions, img.width, img.height)
    # Native landscape dims regardless of which way the user has the
    # panel oriented in settings.
    native_w = max(panel.w, panel.h)
    native_h = min(panel.w, panel.h)
    if panel.w < panel.h:
        # Panel mounted portrait, the composition (portrait or square)
        # needs a 90° CCW pre-rotation so its top maps to the left edge
        # of the landscape buffer the firmware reads.
        img = img.rotate(90, expand=True)
    if panel.flip:
        # Upside-down physical mount, turn the whole thing 180° so it
        # reads upright on the wall.
        img = img.rotate(180, expand=True)
    if img.size != (native_w, native_h):
        # Send-page uploads aren't panel-sized; fit before packing. The Send
        # page passes a per-push fit mode (fit/fill/stretch/center/blur).
        fit = str(settings.get("image_fit") or "fit")
        img = fit_to_panel(img, target_w=native_w, target_h=native_h, scale=fit, bg="white")
    # Per-device underscan: inset content so it clears a physical mat/bezel.
    if panel.underscan:
        img = underscan_image(img, underscan=panel.underscan)
    if mask_img is not None:
        mask_img = transform_mask(
            mask_img,
            native_w=native_w,
            native_h=native_h,
            rotate=90 if panel.w < panel.h else 0,
            flip=bool(panel.flip),
            underscan=int(panel.underscan or 0),
        )
    tone = settings.get("_profile_tone") or {}
    dither_extras = settings.get("_profile_dither") or {}
    edges = settings.get("_profile_edges") or {}
    return pack_to_panel_bin(
        img,
        width=native_w,
        height=native_h,
        dither=_setting(settings, "dither"),
        saturation=float(_setting(settings, "saturation")),
        contrast=float(_setting(settings, "contrast")),
        # Gamut is a per-device panel attribute (a 7-colour Inky vs a
        # Waveshare E6 differ in palette + index order), threaded through
        # the Panel so one shared renderer serves both.
        gamut=panel.gamut,
        calibrated=bool(_setting(settings, "calibrated")),
        # Calibration-tab palette profile (populated by app.push from
        # the device's active profile). None keeps pre-v0.67 behaviour.
        palette_override=settings.get("_palette_override"),
        exposure=int(tone.get("exposure", 0)),
        s_curve=int(tone.get("s_curve", 0)),
        serpentine=bool(dither_extras.get("serpentine", False)),
        diffusion_strength=int(dither_extras.get("diffusion_strength", 100)),
        smoothing_radius=int(edges.get("smoothing_radius", 0)),
        preserve_line_art=bool(edges.get("preserve_line_art", False)),
        lab_compress_min=int(tone.get("lab_compress_min", 0)),
        lab_compress_max=int(tone.get("lab_compress_max", 100)),
        color_match=str(dither_extras.get("color_match", "rgb")),
        region_nearest_mask=mask_img,
    )


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    del settings  # not part of the on-the-wire payload
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.bin"}
