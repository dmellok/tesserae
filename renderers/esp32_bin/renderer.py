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
2. Fit uploaded images into the panel's composition dimensions
   (``panel.w × panel.h``). A landscape photo sent to a portrait display
   stays landscape content; Fit/Fill decides how it is letterboxed/cropped.
3. If the *composition* orientation doesn't match the firmware-native
   stride, rotate the completed composition 90° CW.
4. Apply ``panel.flip`` (180° for upside-down mounts).
5. Fit to the firmware-native dims if a non-standard panel declaration
   requires it.
6. Apply per-device underscan.
7. Apply ``panel.vflip`` (row reverse for panels whose hardware scans
   bottom-to-top, e.g. the PicPak 4-colour BWRY).
8. Pack at the firmware-native dims.
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
    # Per-cell dither map (issue #86). Rasterise at composition dims, then
    # transform in lockstep with the image (same rotate/flip/fit/underscan/
    # vflip) so it stays aligned with the packed bytes. None for Send-page
    # uploads and all-diffuse dashboards, which keeps the pre-#86 path.
    regions = settings.get("_dither_regions")
    mask_img = None
    if regions:
        mask_img = rasterize_region_mask(regions, img.width, img.height)
    native_w, native_h = _firmware_native_dims(panel)
    fit = str(settings.get("image_fit") or "fit")

    # A Send-page / Companion upload is arbitrary source media, not a
    # pre-composed panel frame. Fit it into the display's *composition*
    # dimensions before considering the firmware-native row stride.
    #
    # Comparing the raw photo's aspect to the firmware aspect here used
    # to rotate every landscape photo sent to a portrait-native E1004 by
    # 90° CW. History stayed correct because it stores the source image;
    # only the packed device artifact was wrong.
    if img.size != (panel.w, panel.h):
        img = fit_to_panel(
            img,
            target_w=panel.w,
            target_h=panel.h,
            scale=fit,
            bg="white",
        )

    firmware_landscape = native_w > native_h
    composition_landscape = panel.w > panel.h
    orientation_mismatch = firmware_landscape != composition_landscape
    if orientation_mismatch:
        # The user-calibrated composition orientation differs from the
        # hardware row stride. Rotate the completed composition 90° CW
        # so its left edge lands at the panel's top edge. PIL ``rotate`` is
        # counter-clockwise; ``-90`` gives CW.
        img = img.rotate(-90, expand=True)
    if panel.flip:
        # Upside-down physical mount, turn 180° so it reads upright.
        img = img.rotate(180, expand=True)
    if img.size != (native_w, native_h):
        # Normally the composition dims equal the native dims (or are the
        # exact swapped pair after rotation). Keep a bounded fallback for
        # custom/legacy manifests whose declared sizes differ.
        img = fit_to_panel(img, target_w=native_w, target_h=native_h, scale=fit, bg="white")
    # Per-device underscan: inset content so it clears a physical mat/bezel.
    if panel.underscan:
        img = underscan_image(img, underscan=panel.underscan)
    # v0.69.16: panels whose hardware scans bottom-to-top (PicPak
    # 4-colour BWRY, and any successor with the same UC81xx-class
    # driver quirk) need the row order reversed before pack so the
    # top-of-image lands at the last-scanned byte. Applied after
    # underscan so the mat inset ends up on the correct edge. Cheap
    # (PIL's FLIP_TOP_BOTTOM is a strided copy).
    if panel.vflip:
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
    if mask_img is not None:
        mask_img = transform_mask(
            mask_img,
            native_w=native_w,
            native_h=native_h,
            rotate=-90 if orientation_mismatch else 0,
            flip=bool(panel.flip),
            underscan=int(panel.underscan or 0),
            vflip=bool(panel.vflip),
        )
    # Profile tone / dither knobs (v0.67.1). Populated by app.push
    # only when the device has a Calibration-tab profile applied;
    # missing keys keep the pre-v0.67 defaults so no-profile devices
    # render byte-identical to before.
    tone = settings.get("_profile_tone") or {}
    dither_extras = settings.get("_profile_dither") or {}
    edges = settings.get("_profile_edges") or {}
    return pack_to_panel_bin(
        img,
        width=native_w,
        height=native_h,
        # v0.69.5: route ``panel.gamut`` through so non-Spectra-6 ESP32
        # panels get the right wire format (native 2-bpp for BWRY,
        # etc.). Pre-v0.69.5 this defaulted to ``waveshare_e6`` because
        # every fleet ESP32 was Spectra 6; adding BWRY support in
        # v0.69.4 exposed the missing hookup.
        gamut=panel.gamut,
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
        smoothing_radius=int(edges.get("smoothing_radius", 0)),
        preserve_line_art=bool(edges.get("preserve_line_art", False)),
        protect_native_colours=int(edges.get("protect_native_colours", 0)),
        lab_compress_min=int(tone.get("lab_compress_min", 0)),
        lab_compress_max=int(tone.get("lab_compress_max", 100)),
        color_match=str(dither_extras.get("color_match", "rgb")),
        region_nearest_mask=mask_img,
    )


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    del settings  # not part of the on-the-wire payload
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.bin"}
