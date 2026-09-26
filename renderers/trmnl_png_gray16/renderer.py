"""trmnl_png_gray16 renderer.

Composition PNG -> 16-level greyscale dithered PNG at the device's panel
native dims. The greyscale sibling of ``trmnl_png``: same fit / flip /
underscan / contrast / dither pipeline, but the quantise target is a
16-entry grey ramp instead of pure black + white, and the output is an
8-bit greyscale PNG (mode ``L``) carrying 16 distinct levels rather than
a 1-bit one.

Why a separate renderer from ``trmnl_png``:

* ``trmnl_png`` outputs 1-bit B/W, right for TRMNL OG / X mono panels and
  any client that can only blit 1bpp.
* A panel that paints real greys (a jailbroken e-reader running KOReader
  whose framebuffer is 8-bit greyscale, confirmed on a Kobo Clara HD; the
  Seeed reTerminal E1003 / TRMNL X 16-level waveform under TRMNL firmware
  is the other candidate, untested) throws away most of that
  capability on 1-bit output: a weather-chart area fill or a shaded
  forecast band becomes coarse error-diffusion speckle instead of a
  smooth mid-grey.
* Dithering server-side against the 16-level ramp gives the panel the
  smoothest gradient it can show; the device just paints what arrives,
  no on-device quantise.

Output is an 8-bit greyscale PNG, not 4-bit: Pillow's 4-bit PNG support
is patchy and several e-reader decoders (KOReader's MuPDF / lodepng
path included) handle 8-bit ``L`` cleanly while 4-bit is a coin toss.
The pixel data only spans 16 values so PNG's filter + deflate keep the
file close to a true 4-bit encode anyway.

Grey ramp: the nominal even 0..255/17 ramp (``GRAY_16_PALETTE``) by
default. When the device has a greyscale calibration profile applied,
``app.push`` injects its measured anchors under ``settings["_gray_ramp"]``
(same side channel ``esp32_gray_bin`` reads) and we interpolate those to
16 levels so error diffusion aims at greys the panel actually produces.

The dither + contrast settings are flagged ``device_setting: true`` so
they live on the device card (Settings -> Devices -> Picture quality) and
each panel can be tuned independently.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageEnhance

from app.quantizer import GRAY_16_PALETTE, fit_to_panel, quantize, underscan_image
from app.state.page_store import Panel

DEFAULTS: dict[str, Any] = {
    "dither": "floyd-steinberg",
    "contrast": 1.0,
}

_LEVELS = 16


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def _grey_ramp(settings: dict[str, Any]) -> tuple[tuple[int, int, int], ...]:
    """The 16-entry grey palette to quantise against: the device's
    measured ramp when a greyscale calibration profile is applied, else
    the nominal even ramp.

    ``_gray_ramp`` carries the profile's raw ``#rrggbb`` anchors (two or
    more, darkest first); only the renderer knows its level count, so it
    resolves them here. Same contract as ``esp32_gray_bin``.
    """
    anchors = settings.get("_gray_ramp")
    if anchors:
        from app.palette_profiles.schema import GrayRamp

        resolved = GrayRamp(levels=tuple(str(v) for v in anchors)).as_tuples(_LEVELS)
        if resolved is not None:
            return resolved
    return GRAY_16_PALETTE


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Fit + dither the composition PNG onto the device's buffer, as a
    16-level greyscale PNG.

    The composition arrives at the composition dims already (the
    composer pre-sizes pages to ``panel.w × panel.h``), so the first
    ``fit_to_panel`` call is usually a no-op; it only does real work on
    Send-page image pushes where the input PNG isn't panel-sized, with
    the same per-push ``image_fit`` override the other renderers use.

    Then the finished composition is mapped onto the native buffer the
    client paints (a Kindle reports its physical screen via
    ``png-width`` / ``png-height``, which app.trmnl_api persists as
    ``native_w / native_h``): 90° CW when the composition aspect
    disagrees with the buffer aspect, 180° more when ``panel.flip``,
    and a final fit to the native dims. Without this a landscape
    dashboard mounted on a portrait Kindle is stretched into a
    squashed portrait by the client's scaler. Panels with no native
    block fall back to the composition dims and behave exactly as
    before.
    """
    img = Image.open(io.BytesIO(png_bytes))
    fit = str(settings.get("image_fit") or "fit")

    if img.size != (panel.w, panel.h):
        img = fit_to_panel(img, target_w=panel.w, target_h=panel.h, scale=fit, bg="white")

    native_w, native_h = panel.native_w, panel.native_h
    if native_w is None or native_h is None:
        native_w, native_h = panel.w, panel.h
    if (native_w > native_h) != (panel.w > panel.h):
        # Composition and client buffer disagree on aspect: turn the
        # finished composition 90° CW so its left edge lands on the
        # client's top edge. PIL ``rotate`` is counter-clockwise;
        # ``-90`` gives CW.
        img = img.rotate(-90, expand=True)
    if panel.flip:
        # Upside-down physical mount; turn 180 deg so it reads upright.
        img = img.rotate(180, expand=True)

    if img.size != (native_w, native_h):
        img = fit_to_panel(img, target_w=native_w, target_h=native_h, scale=fit, bg="white")

    if panel.underscan:
        img = underscan_image(img, underscan=panel.underscan)

    # Pre-dither contrast push. Same rationale as trmnl_png: forcing more
    # pixels to definite levels before error diffusion reads better on
    # text-heavy dashboards.
    contrast = float(_setting(settings, "contrast"))
    if abs(contrast - 1.0) > 1e-6:
        img = ImageEnhance.Contrast(img.convert("L")).enhance(contrast).convert("RGB")

    dither = str(_setting(settings, "dither"))
    grey_rgb = quantize(img, dither=dither, palette=_grey_ramp(settings))

    # ``quantize`` returns mode-RGB with every pixel on a grey (r == g == b)
    # ramp entry; collapse to 8-bit ``L`` for the greyscale PNG. The level
    # count is preserved (16 distinct values), the container just drops the
    # two redundant channels.
    grey = grey_rgb.convert("L")
    buf = io.BytesIO()
    grey.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    """The artifact URL the device will fetch.

    For TRMNL there's no separate JSON envelope on a topic; ``/api/display``
    serves this URL inside its own response. The payload here is what
    PushManager records in the event log and what HA discovery surfaces;
    keeping the ``{url}`` shape matches what the other renderers do."""
    del settings  # greyscale-PNG TRMNL output is self-contained
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.png"}
