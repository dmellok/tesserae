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
    BWRY_4_CALIBRATED_PALETTE,
    BWRY_4_PALETTE,
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
# 4-ink BWRY. Order matches BWRY_4_PALETTE.
_COLOUR_LABELS_BWRY: tuple[str, ...] = (
    "black",
    "white",
    "yellow",
    "red",
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
    if gamut == "bwry_4":
        # Without this the E6 fallback below hands a 4-ink BWRY panel a
        # six-colour deck, and three things go wrong at once: the swatch
        # pattern paints blue and green blocks the panel cannot
        # reproduce; a 4-entry profile palette is silently discarded by
        # the ``len(palette_override) >= len(palette)`` guard in
        # :func:`build_pattern`, so the user's chosen profile never
        # reaches the pattern; and error diffusion carries the
        # out-of-gamut columns' residue sideways into neighbouring
        # swatches, speckling them.
        pal = BWRY_4_CALIBRATED_PALETTE if calibrated else BWRY_4_PALETTE
        return pal, _COLOUR_LABELS_BWRY
    pal = WAVESHARE_E6_CALIBRATED_PALETTE if calibrated else WAVESHARE_E6_PALETTE
    return pal, _COLOUR_LABELS_E6


def _is_mono_gamut(gamut: str) -> bool:
    """True for panels whose gamut is black + white only. The
    palette-swatch and solid-fill patterns are noise on such panels
    (both would render as two blocks / one flat fill), so the picker
    hides them. Grayscale ramp still shows: it exercises dither which
    is the interesting axis on a mono panel."""
    return gamut == "mono"


def list_patterns(has_custom_image: bool = False, gamut: str = "") -> list[dict[str, Any]]:
    """The pattern picker. Kept in sync with the template.

    ``needs_color`` marks patterns that take a colour argument (right
    now just the solid fill); the UI reveals a colour picker when set.

    ``has_custom_image`` (v0.68) surfaces the "Your uploaded image"
    entry only when a file is on disk for the device the picker's
    scoped to; the route reads it from
    ``data/calibration_images/<device_id>.png``.

    ``gamut`` (v0.69.14) filters out patterns that don't make sense on
    the panel. Mono panels only see grayscale / text / grid / custom;
    the palette-swatch and solid-fill patterns are colour-only.
    """
    is_mono = _is_mono_gamut(gamut)
    patterns: list[dict[str, Any]] = []
    if not is_mono:
        patterns.append(
            {
                "id": "palette_swatches",
                "label": "Palette swatches",
                "description": "Every colour in the panel's palette as a labelled block.",
            }
        )
    patterns.append(
        {
            "id": "grayscale_ramp",
            "label": "Grayscale ramp",
            "description": "Black to white in 16 steps, the panel dithers between them.",
        }
    )
    if not is_mono:
        patterns.append(
            {
                "id": "solid_fill",
                "label": "Solid fill",
                "description": "One palette colour across the whole panel.",
                "needs_color": True,
            }
        )
    patterns.extend(
        [
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
    )
    if has_custom_image:
        patterns.append(
            {
                "id": "custom_image",
                "label": "Your uploaded image",
                "description": (
                    "The image you uploaded for this device, fit to the panel with white padding."
                ),
            }
        )
    return patterns


# ``PATTERN_IDS`` is the full unfiltered id set the route validates
# against. Filtering by gamut only affects what the picker offers;
# any id in this tuple remains a legal POST value (so a pre-selected
# gamut change doesn't 400 an in-flight submit).
PATTERN_IDS: tuple[str, ...] = tuple(p["id"] for p in list_patterns(has_custom_image=True))


def build_pattern(
    pattern_id: str,
    w: int,
    h: int,
    *,
    gamut: str = "waveshare_e6",
    calibrated: bool = False,
    color_index: int | None = None,
    palette_override: tuple[tuple[int, int, int], ...] | None = None,
    exposure: int = 0,
    s_curve: int = 0,
    lab_compress_min: int = 0,
    lab_compress_max: int = 100,
    smoothing_radius: int = 0,
    custom_image_path: str | None = None,
) -> bytes:
    """Return PNG bytes for ``pattern_id`` at ``w × h``.

    ``gamut`` picks which palette the pattern's colours snap to;
    ``calibrated`` picks the epdoptimize-derived measured palette when
    true and the nominal palette otherwise. ``color_index`` is the
    palette entry to use for the ``solid_fill`` pattern and is ignored
    for every other id.

    ``palette_override`` (v0.67.5) shows the currently-applied profile
    palette in the Calibration-tab preview so users see the actual
    hues their profile picks rather than the built-in default gamut.
    Snaps to the same shape (6-7 RGB tuples) as the built-in tables.

    ``exposure`` / ``s_curve`` / ``lab_compress_*`` / ``smoothing_radius``
    apply the same tone / edge pipeline the renderer uses so slider
    movement in the Calibration tab produces a byte-for-byte match of
    what the panel would paint (skipping dither, which is a per-pixel
    lookup that this preview surface doesn't recreate).

    Unknown ``pattern_id`` values raise :class:`ValueError` rather than
    silently producing white bytes; the route handler validates against
    :data:`PATTERN_IDS` first."""
    if pattern_id not in PATTERN_IDS:
        raise ValueError(f"unknown test pattern {pattern_id!r}")
    w = max(2, int(w))
    h = max(2, int(h))
    palette, labels = _palette_for(gamut, calibrated)
    if palette_override is not None and len(palette_override) >= len(palette):
        palette = tuple(palette_override[: len(palette)])

    if pattern_id == "palette_swatches":
        img = _swatches(w, h, palette, labels)
    elif pattern_id == "grayscale_ramp":
        img = _grayscale_ramp(w, h)
    elif pattern_id == "solid_fill":
        idx = 0 if color_index is None else max(0, min(len(palette) - 1, int(color_index)))
        img = Image.new("RGB", (w, h), palette[idx])
    elif pattern_id == "text_sample":
        img = _text_sample(w, h)
    elif pattern_id == "custom_image":
        img = _custom_image(w, h, custom_image_path)
    else:  # registration_grid
        img = _registration_grid(w, h)

    # v0.67.5: apply the profile's tone / edge pipeline on top of the
    # generated pattern so slider movement in the Calibration tab
    # shows what the panel would paint (short of the dither step,
    # which is a per-pixel lookup this preview surface doesn't
    # replay). Skip work when the caller left every knob neutral.
    if exposure or s_curve or lab_compress_min > 0 or lab_compress_max < 100 or smoothing_radius:
        from app.quantizer import (
            _apply_exposure,
            _apply_s_curve,
            _apply_smoothing,
            _compress_lab_range,
        )

        if smoothing_radius:
            img = _apply_smoothing(img, smoothing_radius)
        if lab_compress_min > 0 or lab_compress_max < 100:
            img = _compress_lab_range(img, lab_compress_min, lab_compress_max)
        if exposure:
            img = _apply_exposure(img, exposure)
        if s_curve:
            img = _apply_s_curve(img, s_curve)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _custom_image(w: int, h: int, path: str | None) -> Image.Image:
    """User-uploaded reference image, fit to panel dims with white
    padding (matches the Send-file pipeline). Returns a plain white
    canvas when the path is missing / unreadable so the picker doesn't
    error out with a confusing 500."""
    if not path:
        return Image.new("RGB", (w, h), (255, 255, 255))
    try:
        src = Image.open(path).convert("RGB")
    except (OSError, FileNotFoundError):
        return Image.new("RGB", (w, h), (255, 255, 255))
    from app.quantizer import fit_to_panel

    return fit_to_panel(src, target_w=w, target_h=h, scale="fit", bg="white")


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
