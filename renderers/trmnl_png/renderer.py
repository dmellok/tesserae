"""trmnl_png renderer.

Composition PNG → 1-bit dithered PNG at the device's reported buffer
dims. Output is what the KOReader trmnl-display plugin (and TRMNL
devices) paint directly, MuPDF on the Kindle side decodes a
1-bit greyscale PNG cleanly when it's well-formed; we use Pillow's
``Image.save(format="PNG", optimize=True)`` so the encoded bytes meet
the spec without any of the stride-padding traps a stdlib hand-rolled
encoder can hit.

Why a rotation step, rather than just "fit at the panel dims": the
composer produces the page at the *composition* dims
(``panel.w × panel.h``), but the client paints its own buffer — the
e-reader's physical screen for a Kindle. ``app.trmnl_api`` persists
the buffer the client declares via ``png-width`` / ``png-height``
onto the panel block as ``native_w × native_h``. The two disagree
on aspect exactly when a landscape dashboard is mounted on a
portrait screen: composed at 1024×758, the Rotation control says 90°,
so the frame has to be turned before the client's scaler stretches it
into the squashed portrait. So ``transform()`` rotates the finished
composition onto the native buffer (90° CW when the aspects differ,
180° more for ``panel.flip``) and fits + dithers at that exact size.
Panels with no native block (legacy / custom, never reported) fall
back to the composition dims and behave exactly as before.

The dither + contrast settings are flagged ``device_setting: true``
so they live on the device card (Settings → Devices → Picture quality)
- each panel can be tuned independently. The defaults (Floyd-Steinberg,
contrast 1.0) work well on a Kindle Paperwhite; an older e-paper with
slower refresh might prefer Atkinson + a slight contrast bump.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageEnhance

from app.dither_regions import buffer_flat_mask, paste_flat
from app.quantizer import fit_to_panel, orient_onto_buffer, quantize, underscan_image
from app.state.page_store import Panel

# Black + white. ``quantize`` expects an RGB-tuple palette and
# does the rest, Pillow's ``Image.quantize(palette=...)`` projects
# the input onto those two colours with the selected dither algorithm.
_MONO_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),
    (255, 255, 255),
)

DEFAULTS: dict[str, Any] = {
    "dither": "floyd-steinberg",
    "contrast": 1.0,
}


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Fit + dither the composition PNG onto the device's buffer.

    The composition arrives at the composition dims already (the
    composer pre-sizes pages to ``panel.w × panel.h``), so the first
    ``fit_to_panel`` call is usually a no-op. It only does real work on
    Send-page image pushes where the user's input PNG isn't
    panel-sized, same path that the other renderers use, with the same
    per-push ``image_fit`` override (fit / fill / stretch / centre /
    blur).

    Then the finished composition is mapped onto the native buffer the
    client paints: 90° CW when the composition aspect disagrees with
    the buffer aspect (the Rotation control's degrees are the turn
    from that buffer), 180° more when ``panel.flip``, and a final fit
    to the native dims. Same mapping ``esp32_bin`` uses for a
    user-calibrated orientation that disagrees with the firmware row
    stride.
    """
    img = Image.open(io.BytesIO(png_bytes))
    fit = str(settings.get("image_fit") or "fit")

    if img.size != (panel.w, panel.h):
        img = fit_to_panel(img, target_w=panel.w, target_h=panel.h, scale=fit, bg="white")

    # Only a buffer the device itself reported counts. ``device_panel``
    # also guesses native dims from the preset table when a panel block
    # has none (800×480 matches three presets), and rotating onto a
    # guess would reshape frames for clients that were already painting
    # the composition correctly (same gate the CircuitPython renderers
    # use, issue #200 / #275).
    native_w, native_h = panel.declared_native or (panel.w, panel.h)
    # A Flat cell is drawn in the nearest ink, with no dither (#601).
    flat_mask = buffer_flat_mask(
        settings,
        comp_w=img.width,
        comp_h=img.height,
        buffer_w=native_w,
        buffer_h=native_h,
        flip=panel.flip,
        underscan=panel.underscan,
    )
    img = orient_onto_buffer(img, buffer_w=native_w, buffer_h=native_h, flip=panel.flip)

    if panel.underscan:
        # Per-device underscan: inset the rendered content so it clears
        # a physical bezel/mat covering the panel edge. ``underscan_image``
        # fills the borders with white.
        img = underscan_image(img, underscan=panel.underscan)

    # Pre-dither contrast push. Useful for photo-heavy dashboards where
    # the grey midtones would otherwise dither to a busy speckle -
    # bumping contrast forces more pixels to definite black or definite
    # white before the dither pass runs.
    contrast = float(_setting(settings, "contrast"))
    if abs(contrast - 1.0) > 1e-6:
        img = ImageEnhance.Contrast(img.convert("L")).enhance(contrast).convert("RGB")

    dither = str(_setting(settings, "dither"))
    dithered_rgb = quantize(img, dither=dither, palette=_MONO_PALETTE)
    if flat_mask is not None:
        flat = quantize(img, dither="none", palette=_MONO_PALETTE)
        dithered_rgb = paste_flat(dithered_rgb, flat, flat_mask)

    # Convert to 1-bit mode for the smallest possible PNG. Pillow's
    # ``convert("1")`` re-thresholds since we're already a 2-colour
    # palette, that's a no-op, the bytes on disk are just the packed
    # bit buffer with PNG's 1-bit IHDR.
    mono = dithered_rgb.convert("1")
    buf = io.BytesIO()
    mono.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    """The artifact URL the device will fetch.

    For TRMNL there's no separate JSON envelope on a topic, ``/api/display``
    serves this URL inside its own response. The payload here is what
    PushManager records in the event log and what HA discovery surfaces;
    keeping the ``{url}`` shape matches what the other renderers do."""
    del settings  # mono-PNG TRMNL output is self-contained
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.png"}
