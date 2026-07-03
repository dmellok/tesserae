"""Colour test-pattern generator.

Covers the pattern dispatcher's public contract:
* every listed pattern renders at the requested size;
* solid-fill honours ``color_index`` and clamps out-of-range values;
* unknown ids raise :class:`ValueError` rather than silently
  producing white bytes;
* palette-locked patterns paint colours drawn from the target gamut,
  so the renderer's dither pass has zero error to diffuse.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app import test_patterns
from app.quantizer import (
    INKY_7COLOUR_PALETTE,
    WAVESHARE_E6_CALIBRATED_PALETTE,
    WAVESHARE_E6_PALETTE,
)


def _open(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png))


@pytest.mark.parametrize("pattern_id", [p["id"] for p in test_patterns.list_patterns()])
def test_every_pattern_renders_at_requested_size(pattern_id: str) -> None:
    png = test_patterns.build_pattern(pattern_id, 400, 200)
    img = _open(png)
    assert img.size == (400, 200)
    assert img.format == "PNG"


def test_list_patterns_matches_pattern_ids() -> None:
    assert set(test_patterns.PATTERN_IDS) == {p["id"] for p in test_patterns.list_patterns()}


def test_unknown_pattern_raises() -> None:
    with pytest.raises(ValueError):
        test_patterns.build_pattern("does-not-exist", 100, 100)


def test_solid_fill_paints_the_requested_palette_colour() -> None:
    # index 3 = red on the Waveshare E6 palette.
    png = test_patterns.build_pattern("solid_fill", 32, 32, color_index=3)
    img = _open(png).convert("RGB")
    assert set(img.getdata()) == {WAVESHARE_E6_PALETTE[3]}


def test_solid_fill_clamps_negative_color_index() -> None:
    png = test_patterns.build_pattern("solid_fill", 8, 8, color_index=-5)
    img = _open(png).convert("RGB")
    assert set(img.getdata()) == {WAVESHARE_E6_PALETTE[0]}


def test_solid_fill_clamps_out_of_range_color_index() -> None:
    png = test_patterns.build_pattern("solid_fill", 8, 8, color_index=999)
    img = _open(png).convert("RGB")
    last = WAVESHARE_E6_PALETTE[-1]
    assert set(img.getdata()) == {last}


def test_solid_fill_defaults_to_first_palette_entry() -> None:
    png = test_patterns.build_pattern("solid_fill", 8, 8)
    img = _open(png).convert("RGB")
    assert set(img.getdata()) == {WAVESHARE_E6_PALETTE[0]}


def test_calibrated_palette_paints_measured_black() -> None:
    # calibrated E6 black is a dark slate (~#1F2226), not pure #000.
    png = test_patterns.build_pattern("solid_fill", 8, 8, calibrated=True, color_index=0)
    img = _open(png).convert("RGB")
    assert set(img.getdata()) == {WAVESHARE_E6_CALIBRATED_PALETTE[0]}


def test_inky_gamut_uses_the_seven_colour_palette() -> None:
    # Inky's orange (index 6) doesn't exist in the E6 deck; picking it
    # against the inky_7colour gamut has to come from the Inky palette.
    png = test_patterns.build_pattern("solid_fill", 8, 8, gamut="inky_7colour", color_index=6)
    img = _open(png).convert("RGB")
    assert set(img.getdata()) == {INKY_7COLOUR_PALETTE[6]}


def test_unknown_gamut_falls_back_to_e6_deck() -> None:
    # A future gamut / custom panel shouldn't crash the picker; the
    # generator degrades to the safe E6 default.
    png = test_patterns.build_pattern("solid_fill", 8, 8, gamut="totally_new_panel")
    img = _open(png).convert("RGB")
    assert set(img.getdata()) == {WAVESHARE_E6_PALETTE[0]}


def test_palette_swatches_backgrounds_are_palette_locked() -> None:
    # The swatch backgrounds must be exact palette values so the
    # renderer's dither pass has no error to diffuse. Text labels
    # are antialiased and drift off-palette by design (the panel
    # dithers those back on paint), so we sample a top-row pixel
    # inside each swatch, well clear of the centred label ink.
    n = len(WAVESHARE_E6_PALETTE)
    swatch_w = 60
    png = test_patterns.build_pattern("palette_swatches", swatch_w * n, 80)
    img = _open(png).convert("RGB")
    for i, expected in enumerate(WAVESHARE_E6_PALETTE):
        sample_x = i * swatch_w + swatch_w // 4
        assert img.getpixel((sample_x, 2)) == expected, (
            f"swatch {i} sampled off-palette: got {img.getpixel((sample_x, 2))}, "
            f"expected {expected}"
        )


def test_grayscale_ramp_spans_black_to_white() -> None:
    png = test_patterns.build_pattern("grayscale_ramp", 320, 40)
    img = _open(png).convert("RGB")
    pixels = list(img.getdata())
    assert (0, 0, 0) in pixels
    assert (255, 255, 255) in pixels


def test_registration_grid_has_black_corner_marks() -> None:
    png = test_patterns.build_pattern("registration_grid", 200, 200)
    img = _open(png).convert("RGB")
    assert img.getpixel((0, 0)) == (0, 0, 0)
    assert img.getpixel((199, 0)) == (0, 0, 0)
    assert img.getpixel((0, 199)) == (0, 0, 0)
    assert img.getpixel((199, 199)) == (0, 0, 0)


def test_tiny_size_clamped_to_two() -> None:
    # Handful of composers push a device with panel dims not yet set;
    # the generator has to survive w=h=0 without a Pillow crash.
    png = test_patterns.build_pattern("palette_swatches", 0, 0)
    img = _open(png)
    assert img.size == (2, 2)
