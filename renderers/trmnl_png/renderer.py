"""trmnl_png renderer.

Composition PNG → 1-bit dithered PNG at the device's panel native
dims. Output is what the KOReader trmnl-display plugin (and native
TRMNL hardware) paints directly — MuPDF on the Kindle side decodes a
1-bit greyscale PNG cleanly when it's well-formed; we use Pillow's
``Image.save(format="PNG", optimize=True)`` so the encoded bytes meet
the spec without any of the stride-padding traps a stdlib hand-rolled
encoder can hit.

Why no rotation logic like ``pi_bin`` / ``pi_png``: TRMNL clients
tell us exactly what dims they want via ``png-width`` / ``png-height``
headers, which app.trmnl_api persists onto the device's panel block.
The composer already produces the page at the panel's dims, so we
just fit + dither at that exact size. ``panel.flip`` still applies
for an upside-down mount.

The dither + contrast settings are flagged ``device_setting: true``
so they live on the device card (Settings → Devices → Picture quality)
— each panel can be tuned independently. The defaults (Floyd-Steinberg,
contrast 1.0) work well on a Kindle Paperwhite; an older e-paper with
slower refresh might prefer Atkinson + a slight contrast bump.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageEnhance

from app.quantizer import fit_to_panel, quantize, underscan_image
from app.state.page_store import Panel

# Black + white. ``quantize`` expects an RGB-tuple palette and
# does the rest — Pillow's ``Image.quantize(palette=...)`` projects
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
    """Fit + dither the composition PNG to the panel's exact dims.

    The composition arrives at panel size already (the composer pre-
    sizes pages to ``panel.w × panel.h``), so the ``fit_to_panel`` call
    is usually a no-op. It only does real work on Send-page image
    pushes where the user's input PNG isn't panel-sized — same path
    that the other renderers use, with the same per-push ``image_fit``
    override (fit / fill / stretch / centre / blur).
    """
    img = Image.open(io.BytesIO(png_bytes))
    target_w, target_h = panel.w, panel.h

    if panel.flip:
        # Upside-down physical mount — turn the whole thing 180° so it
        # reads upright on the wall.
        img = img.rotate(180, expand=True)

    if img.size != (target_w, target_h):
        fit = str(settings.get("image_fit") or "fit")
        img = fit_to_panel(img, target_w=target_w, target_h=target_h, scale=fit, bg="white")

    if panel.underscan:
        # Per-device underscan: inset the rendered content so it clears
        # a physical bezel/mat covering the panel edge. ``underscan_image``
        # fills the borders with white.
        img = underscan_image(img, underscan=panel.underscan)

    # Pre-dither contrast push. Useful for photo-heavy dashboards where
    # the grey midtones would otherwise dither to a busy speckle —
    # bumping contrast forces more pixels to definite black or definite
    # white before the dither pass runs.
    contrast = float(_setting(settings, "contrast"))
    if abs(contrast - 1.0) > 1e-6:
        img = ImageEnhance.Contrast(img.convert("L")).enhance(contrast).convert("RGB")

    dither = str(_setting(settings, "dither"))
    dithered_rgb = quantize(img, dither=dither, palette=_MONO_PALETTE)

    # Convert to 1-bit mode for the smallest possible PNG. Pillow's
    # ``convert("1")`` re-thresholds since we're already a 2-colour
    # palette, that's a no-op — the bytes on disk are just the packed
    # bit buffer with PNG's 1-bit IHDR.
    mono = dithered_rgb.convert("1")
    buf = io.BytesIO()
    mono.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    """The artifact URL the device will fetch.

    For TRMNL there's no separate JSON envelope on a topic — ``/api/display``
    serves this URL inside its own response. The payload here is what
    PushManager records in the event log and what HA discovery surfaces;
    keeping the ``{url}`` shape matches what the other renderers do."""
    del settings  # mono-PNG TRMNL output is self-contained
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.png"}
