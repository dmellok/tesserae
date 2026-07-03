"""Colour test patterns for panel calibration.

Sends native-palette blocks straight to the push pipeline. Every pixel
is picked from the target panel's palette, so the renderer's dither
step has zero error to diffuse and the panel paints exactly what the
generator produced. This gives a per-device sanity check for gamut,
tone mapping (nominal vs calibrated), pixel alignment, and text
legibility without going through the composer / Chromium path.

Patterns are enumerated by :func:`list_patterns` (kept in sync with the
template picker) and built by :func:`build_pattern`, both consumed by
the settings-devices calibrate routes.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.quantizer import (
    INKY_7COLOUR_CALIBRATED_PALETTE,
    INKY_7COLOUR_PALETTE,
    WAVESHARE_E6_CALIBRATED_PALETTE,
    WAVESHARE_E6_PALETTE,
)

RGB = tuple[int, int, int]

_COLOUR_LABELS_E6: tuple[str, ...] = (
    "black",
    "white",
    "yellow",
    "red",
    "blue",
    "green",
)
_COLOUR_LABELS_INKY7: tuple[str, ...] = (
    "black",
    "white",
    "green",
    "blue",
    "red",
    "yellow",
    "orange",
)


def _palette_for(gamut: str, calibrated: bool) -> tuple[tuple[RGB, ...], tuple[str, ...]]:
    """Return the (palette, labels) pair for a given panel gamut.

    Falls back to the Waveshare E6 nominal deck for unknown gamuts so
    the pattern generator degrades gracefully on custom panels rather
    than raising."""
    if gamut == "inky_7colour":
        pal = INKY_7COLOUR_CALIBRATED_PALETTE if calibrated else INKY_7COLOUR_PALETTE
        return pal, _COLOUR_LABELS_INKY7
    pal = WAVESHARE_E6_CALIBRATED_PALETTE if calibrated else WAVESHARE_E6_PALETTE
    return pal, _COLOUR_LABELS_E6


def list_patterns() -> list[dict[str, Any]]:
    """The pattern picker. Kept in sync with the template.

    ``needs_color`` marks patterns that take a colour argument (right
    now just the solid fill); the UI reveals a colour picker when set.
    """
    return [
        {
            "id": "palette_swatches",
            "label": "Palette swatches",
            "description": "Every colour in the panel's palette as a labelled block.",
        },
        {
            "id": "grayscale_ramp",
            "label": "Grayscale ramp",
            "description": "Black to white in 16 steps, the panel dithers between them.",
        },
        {
            "id": "solid_fill",
            "label": "Solid fill",
            "description": "One palette colour across the whole panel.",
            "needs_color": True,
        },
        {
            "id": "text_sample",
            "label": "Text sample",
            "description": "Headline, body, and caption sizes on white.",
        },
        {
            "id": "registration_grid",
            "label": "Registration grid",
            "description": "1 px and 2 px lines plus corner marks.",
        },
    ]


PATTERN_IDS: tuple[str, ...] = tuple(p["id"] for p in list_patterns())


def build_pattern(
    pattern_id: str,
    w: int,
    h: int,
    *,
    gamut: str = "waveshare_e6",
    calibrated: bool = False,
    color_index: int | None = None,
) -> bytes:
    """Return PNG bytes for ``pattern_id`` at ``w × h``.

    ``gamut`` picks which palette the pattern's colours snap to;
    ``calibrated`` picks the epdoptimize-derived measured palette when
    true and the nominal palette otherwise. ``color_index`` is the
    palette entry to use for the ``solid_fill`` pattern and is ignored
    for every other id.

    Unknown ``pattern_id`` values raise :class:`ValueError` rather than
    silently producing white bytes; the route handler validates against
    :data:`PATTERN_IDS` first."""
    if pattern_id not in PATTERN_IDS:
        raise ValueError(f"unknown test pattern {pattern_id!r}")
    w = max(2, int(w))
    h = max(2, int(h))
    palette, labels = _palette_for(gamut, calibrated)

    if pattern_id == "palette_swatches":
        img = _swatches(w, h, palette, labels)
    elif pattern_id == "grayscale_ramp":
        img = _grayscale_ramp(w, h)
    elif pattern_id == "solid_fill":
        idx = 0 if color_index is None else max(0, min(len(palette) - 1, int(color_index)))
        img = Image.new("RGB", (w, h), palette[idx])
    elif pattern_id == "text_sample":
        img = _text_sample(w, h)
    else:  # registration_grid
        img = _registration_grid(w, h)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _swatches(w: int, h: int, palette: tuple[RGB, ...], labels: tuple[str, ...]) -> Image.Image:
    """Even-width columns of each palette colour with a legibility
    label. Label ink is chosen per-swatch (white on dark, black on
    light) via the standard sRGB luma test."""
    n = len(palette)
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    swatch_w = w // n
    font = _load_font(max(10, min(swatch_w // 6, h // 24)))
    for i in range(n):
        x0 = i * swatch_w
        x1 = w if i == n - 1 else x0 + swatch_w
        draw.rectangle((x0, 0, x1, h), fill=palette[i])
        label = labels[i] if i < len(labels) else f"c{i}"
        r, g, b = palette[i]
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        ink = (0, 0, 0) if luma > 140 else (255, 255, 255)
        _draw_centred_text(draw, label, (x0 + swatch_w // 2, h // 2), font, ink)
    return img


def _grayscale_ramp(w: int, h: int) -> Image.Image:
    """16-step horizontal grayscale ramp. Matches the 4-bit grayscale
    depth used by mono / 16-level panels; on 6-colour panels this shows
    the black/white dither transition."""
    steps = 16
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    step_w = w / steps
    for i in range(steps):
        v = round(i * 255 / (steps - 1))
        x0 = int(i * step_w)
        x1 = w if i == steps - 1 else int((i + 1) * step_w)
        draw.rectangle((x0, 0, x1, h), fill=(v, v, v))
    return img


def _text_sample(w: int, h: int) -> Image.Image:
    """Three text sizes on a white background: headline (~h/10),
    body (~h/24), caption (~h/40). Uses Pillow's default font since
    the app doesn't bundle TTFs (real dashboards render text through
    Chromium)."""
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    headline = _load_font(max(24, h // 10))
    body = _load_font(max(14, h // 24))
    caption = _load_font(max(10, h // 40))
    padding = max(16, w // 32)
    y = padding
    draw.text((padding, y), "Headline: the quick brown fox", font=headline, fill=(0, 0, 0))
    y += max(28, h // 8)
    for _ in range(3):
        draw.text(
            (padding, y),
            "Body: 1234567890 abcdefghijklmnopqrstuvwxyz",
            font=body,
            fill=(0, 0, 0),
        )
        y += max(20, h // 20)
    y += max(8, h // 60)
    draw.text(
        (padding, y),
        "Caption: e-ink dither legibility check at small sizes",
        font=caption,
        fill=(0, 0, 0),
    )
    return img


def _registration_grid(w: int, h: int) -> Image.Image:
    """1 px lines every ~64 px, 2 px lines every ~256 px, filled corner
    marks. Reveals sub-pixel drift, ghosting bands, and panel-edge
    dead pixels."""
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    black = (0, 0, 0)
    fine_step = max(16, min(w, h) // 24)
    heavy_step = fine_step * 4
    for x in range(0, w, fine_step):
        draw.line(((x, 0), (x, h - 1)), fill=black, width=1)
    for y in range(0, h, fine_step):
        draw.line(((0, y), (w - 1, y)), fill=black, width=1)
    for x in range(0, w, heavy_step):
        draw.line(((x, 0), (x, h - 1)), fill=black, width=2)
    for y in range(0, h, heavy_step):
        draw.line(((0, y), (w - 1, y)), fill=black, width=2)
    mark = max(20, min(w, h) // 20)
    draw.rectangle((0, 0, mark, mark), fill=black)
    draw.rectangle((w - mark, 0, w - 1, mark), fill=black)
    draw.rectangle((0, h - mark, mark, h - 1), fill=black)
    draw.rectangle((w - mark, h - mark, w - 1, h - 1), fill=black)
    draw.rectangle((0, 0, w - 1, h - 1), outline=black, width=2)
    return img


def _load_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Pillow's default font, scaled. Older Pillow builds don't accept
    the ``size`` kwarg on ``load_default``; fall back to the fixed-size
    default in that case."""
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_centred_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    centre: tuple[int, int],
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    ink: RGB,
) -> None:
    """Centre ``text`` on ``centre``. Uses ``textbbox`` since Pillow
    10 deprecated the older ``textsize`` API."""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except (AttributeError, TypeError):
        tw, th = font.getbbox(text)[2:] if hasattr(font, "getbbox") else (len(text) * 6, 12)
    x = centre[0] - tw // 2
    y = centre[1] - th // 2
    draw.text((x, y), text, font=font, fill=ink)
