"""Quantizer: packing layout, dither-mode dispatch, fit-to-panel scale modes.

We don't assert on visual quality — that's curated manually with the
/calibrate page (later milestone). These check the byte-level invariants
the firmware contract relies on."""

from __future__ import annotations

import pytest
from PIL import Image

from app.quantizer import (
    WAVESHARE_E6_PALETTE,
    apply_underscan,
    fit_to_panel,
    pack_to_panel_bin,
    rotate_png,
)


@pytest.fixture
def red_panel() -> Image.Image:
    return Image.new("RGB", (100, 80), (255, 0, 0))


def _png_bytes(img: Image.Image) -> bytes:
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_pack_byte_count_is_panel_div_two(red_panel: Image.Image) -> None:
    packed = pack_to_panel_bin(red_panel, width=100, height=80, dither="none")
    # Two pixels per byte across the full panel.
    assert len(packed) == 100 * 80 // 2


def test_pack_odd_width_rejected() -> None:
    img = Image.new("RGB", (101, 80), (0, 0, 0))
    with pytest.raises(ValueError, match="even"):
        pack_to_panel_bin(img, width=101, height=80)


def test_pack_size_mismatch_rejected(red_panel: Image.Image) -> None:
    with pytest.raises(ValueError, match="must be"):
        pack_to_panel_bin(red_panel, width=200, height=80)


def test_pack_red_panel_maps_to_palette_red_nibble(red_panel: Image.Image) -> None:
    # Pure red is the 4th entry of WAVESHARE_E6_PALETTE -> firmware nibble 0x3.
    packed = pack_to_panel_bin(red_panel, width=100, height=80, dither="none")
    # Every byte: (red_nibble << 4) | red_nibble = 0x33.
    assert all(b == 0x33 for b in packed)
    # Sanity check the palette entry we asserted on.
    assert WAVESHARE_E6_PALETTE[3] == (255, 0, 0)


@pytest.mark.parametrize(
    "dither",
    ["floyd-steinberg", "none", "atkinson", "bayer-8x8", "halftone", "crosshatch"],
)
def test_pack_dispatches_every_dither_mode(red_panel: Image.Image, dither: str) -> None:
    packed = pack_to_panel_bin(red_panel, width=100, height=80, dither=dither)
    assert len(packed) == 100 * 80 // 2


def test_pack_unknown_dither_raises(red_panel: Image.Image) -> None:
    with pytest.raises(ValueError, match="unknown dither"):
        pack_to_panel_bin(red_panel, width=100, height=80, dither="not-a-mode")  # type: ignore[arg-type]


def test_rotate_round_trip() -> None:
    img = Image.new("RGB", (40, 20), (0, 0, 255))
    png = _png_bytes(img)
    rotated = rotate_png(png, quarters=1)
    out = Image.open(__import__("io").BytesIO(rotated))
    assert out.size == (20, 40)
    # Four quarter turns returns to the original size (and is a fast
    # no-op via quarters % 4).
    assert rotate_png(png, quarters=4) == png


def test_underscan_preserves_outer_dims() -> None:
    img = Image.new("RGB", (40, 40), (0, 0, 0))
    out = apply_underscan(_png_bytes(img), underscan=4)
    out_img = Image.open(__import__("io").BytesIO(out))
    assert out_img.size == (40, 40)


def test_underscan_zero_is_passthrough() -> None:
    png = _png_bytes(Image.new("RGB", (10, 10), (0, 0, 0)))
    assert apply_underscan(png, underscan=0) == png


def test_fit_to_panel_already_correct_is_passthrough() -> None:
    img = Image.new("RGB", (100, 80), (0, 0, 0))
    out = fit_to_panel(img, target_w=100, target_h=80)
    assert out.size == (100, 80)


def test_fit_to_panel_stretch_squashes() -> None:
    img = Image.new("RGB", (40, 80), (0, 0, 0))
    out = fit_to_panel(img, target_w=100, target_h=80, scale="stretch")
    assert out.size == (100, 80)


def test_fit_to_panel_fit_letterboxes() -> None:
    img = Image.new("RGB", (50, 50), (0, 0, 0))
    out = fit_to_panel(img, target_w=100, target_h=80, scale="fit", bg="white")
    assert out.size == (100, 80)
    # Top-left pixel is letterbox (white), centre pixel is image (black).
    assert out.getpixel((0, 0)) == (255, 255, 255)
    assert out.getpixel((50, 40)) == (0, 0, 0)


def test_fit_to_panel_fill_crops() -> None:
    img = Image.new("RGB", (200, 100), (0, 0, 0))
    out = fit_to_panel(img, target_w=100, target_h=80, scale="fill")
    assert out.size == (100, 80)
