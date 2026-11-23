"""kaleido_png renderer.

Composition PNG -> Kaleido 3 colour PNG at the e-reader's screen dims:
24-bit RGB with every channel dithered to 16 levels (4096 colours), the
colour space of an E Ink Kaleido 3 panel (a colour filter array over
16-level greyscale glass: Kobo Libra Colour, Clara Colour, and other
colour readers KOReader drives).

Why a separate renderer from the KOReader kind's grey packers:

* ``esp32_gray_bin`` / ``esp32_gray2_bin`` / ``esp32_bw_bin`` pack a
  grey level per pixel into a raw buffer the plugin unpacks on the
  device. A colour reader could paint that, in grey, and throw away the
  one thing it was bought for.
* Kaleido has no short ink list to match against, so the indexed-PNG
  path the colour TRMNL renderer takes does not fit either: the panel
  reproduces any triple of 16-level channels, and the honest output is
  per-channel quantisation with error diffusion (see
  :func:`app.quantizer.quantize_kaleido3`), served as an ordinary RGB
  PNG. The KOReader plugin already handles ``format: "png"`` frames: it
  decodes them with the reader's image stack and blits the colour.

A device reaches this renderer by pairing with ``gamut: "kaleido3"`` (the
plugin announces that when KOReader reports a colour screen with colour
rendering on); ``app.device_service.renderer_id_for_gamut`` reads the
``gamuts`` list on the manifest and pins the instance here. The Kobo
colour hardware entries also list it first.

Orientation follows ``trmnl_png_gray16``: the finished composition is
turned onto the buffer the client reported (``native_w`` / ``native_h``
from a declared rotation or a hardware entry; the plugin reports its
screen as ``panel_w`` / ``panel_h``), 90 degrees CW when the aspects
disagree, 180 more for ``panel.flip``. Panels with no reported buffer
keep the composition dims.

The dither + contrast settings are flagged ``device_setting: true`` so
they live on the device card (Settings -> Devices -> Picture quality) and
each panel can be tuned independently.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageEnhance

from app.dither_regions import buffer_flat_mask, paste_flat
from app.quantizer import (
    fit_to_panel,
    orient_onto_buffer,
    quantize_kaleido3,
    underscan_image,
)
from app.state.page_store import Panel

DEFAULTS: dict[str, Any] = {
    "dither": "floyd-steinberg",
    "contrast": 1.0,
}


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Fit, orient, contrast-adjust and quantise the composition PNG to a
    Kaleido 3 colour PNG at the device's buffer dims.

    The composition usually arrives at ``panel.w x panel.h`` already (the
    composer sizes pages to the panel), so the first fit is a no-op
    except on Send-page image pushes, which carry their own ``image_fit``.
    """
    img: Image.Image = Image.open(io.BytesIO(png_bytes))
    fit = str(settings.get("image_fit") or "fit")

    if img.size != (panel.w, panel.h):
        img = fit_to_panel(img, target_w=panel.w, target_h=panel.h, scale=fit, bg="white")

    # Only a buffer the device itself reported counts (same gate as the
    # TRMNL and CircuitPython PNG renderers, issue #200): a preset guess
    # would reshape frames for a reader already painting correctly.
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
        img = underscan_image(img, underscan=panel.underscan)

    # Pre-dither contrast push, applied in colour (the grey renderers
    # collapse to L first; here that would discard the chroma).
    contrast = float(_setting(settings, "contrast"))
    if abs(contrast - 1.0) > 1e-6:
        img = ImageEnhance.Contrast(img.convert("RGB")).enhance(contrast)

    out = quantize_kaleido3(img, dither=_setting(settings, "dither"))
    if flat_mask is not None:
        out = paste_flat(out, quantize_kaleido3(img, dither="none"), flat_mask)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    """The artefact URL the reader fetches. The RGB PNG is self-contained:
    orientation, fit and quantisation were applied here."""
    del settings
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.png"}
