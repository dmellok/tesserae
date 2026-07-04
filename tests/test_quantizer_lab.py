"""Phase 5: LAB compression + colour-match modes.

Checks:
* sRGB<->LAB conversion round-trips within a small delta,
* LAB compression at neutral values is a no-op,
* LAB compression at a narrow window rescales the source lightness,
* colour_match variants change the output relative to RGB nearest,
* pack neutral defaults (color_match=rgb, lab min=0 max=100) match
  the pre-v0.67.4 output byte-for-byte."""

from __future__ import annotations

import numpy as np
from PIL import Image

from app.quantizer import (
    WAVESHARE_E6_PALETTE,
    _compress_lab_range,
    _lab_to_srgb,
    _srgb_to_lab,
    pack_to_panel_bin,
)


def _grey_ramp(width: int = 128, height: int = 32) -> Image.Image:
    arr = np.tile(np.linspace(0, 255, width, dtype=np.uint8), (height, 1))
    return Image.fromarray(np.stack([arr, arr, arr], axis=-1))


def test_srgb_lab_round_trip_is_lossless_within_a_delta() -> None:
    rng = np.random.default_rng(0)
    src = rng.integers(0, 256, size=(32, 32, 3), dtype=np.uint8)
    lab = _srgb_to_lab(src)
    back = _lab_to_srgb(lab)
    assert back.shape == src.shape
    # sRGB round-trip is not bit-exact (piecewise gamma + LAB approximation),
    # but should stay well within 4 levels per channel.
    delta = np.abs(back.astype(np.int32) - src.astype(np.int32))
    assert delta.max() <= 4


def test_compress_lab_neutral_is_identity() -> None:
    img = _grey_ramp()
    same = _compress_lab_range(img, 0, 100)
    assert same is img


def test_compress_lab_narrow_window_darkens_top_end() -> None:
    img = _grey_ramp(128, 8)
    squeezed = np.asarray(_compress_lab_range(img, 10, 60))
    plain = np.asarray(img.convert("RGB"))
    # The white end (255) is compressed into the ~60% lightness band,
    # so it should be strictly darker after compression.
    assert squeezed[:, -1, 0].mean() < plain[:, -1, 0].mean()


def test_compress_lab_max_below_min_is_identity() -> None:
    # Bad ordering (max <= min) falls through untouched rather than
    # producing garbage; the schema clamp on save prevents this in
    # practice, but the quantizer stays defensive.
    img = _grey_ramp()
    same = _compress_lab_range(img, 80, 20)
    assert same is img


def test_pack_neutral_lab_and_match_defaults_match_pre_v674() -> None:
    img = _grey_ramp(64, 32)
    baseline = pack_to_panel_bin(img, width=64, height=32)
    same = pack_to_panel_bin(
        img,
        width=64,
        height=32,
        lab_compress_min=0,
        lab_compress_max=100,
        color_match="rgb",
    )
    assert baseline == same


def test_color_match_lab_changes_output_on_error_diffusion() -> None:
    # Atkinson dither, which routes through the numpy path so the
    # color_match kwarg takes effect immediately.
    img = _grey_ramp(64, 32)
    base = pack_to_panel_bin(img, width=64, height=32, dither="atkinson")
    lab = pack_to_panel_bin(img, width=64, height=32, dither="atkinson", color_match="lab")
    assert base != lab


def test_color_match_chroma_aware_differs_from_plain_lab() -> None:
    # Colour ramp: chroma-weighting only matters when the source has
    # chroma; a grey ramp collapses the chroma-aware and plain-LAB
    # metrics to the same distance calc (a*=b*=0).
    ramp = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (32, 1))
    tint = np.stack([ramp, np.full_like(ramp, 128), 255 - ramp], axis=-1)
    img = Image.fromarray(tint)
    lab = pack_to_panel_bin(img, width=64, height=32, dither="atkinson", color_match="lab")
    chroma = pack_to_panel_bin(
        img, width=64, height=32, dither="atkinson", color_match="chroma-aware"
    )
    assert lab != chroma


def test_color_match_lab_routes_floyd_steinberg_through_numpy() -> None:
    # Pillow's built-in FS is RGB-only. When the profile asks for LAB,
    # the packer detours through the numpy FS path. Both produce a
    # valid palette-index buffer (nibbles fall in 0..len(palette)-1
    # via the LUT).
    img = _grey_ramp(64, 32)
    pillow_fs = pack_to_panel_bin(img, width=64, height=32, dither="floyd-steinberg")
    numpy_fs_lab = pack_to_panel_bin(
        img,
        width=64,
        height=32,
        dither="floyd-steinberg",
        color_match="lab",
    )
    # Different outputs (different distance metric) at similar byte length.
    assert pillow_fs != numpy_fs_lab
    assert len(pillow_fs) == len(numpy_fs_lab)


def test_lab_compress_takes_effect_end_to_end() -> None:
    img = _grey_ramp(64, 32)
    baseline = pack_to_panel_bin(img, width=64, height=32, calibrated=False)
    compressed = pack_to_panel_bin(
        img,
        width=64,
        height=32,
        calibrated=False,
        lab_compress_min=15,
        lab_compress_max=70,
    )
    assert baseline != compressed


def test_lab_palette_conversion_is_stable() -> None:
    # Regression guard: sanity-check the palette itself round-trips
    # through LAB with < 5 per-channel drift so nearest-palette calls
    # aren't mis-labelling entries under the new metric.
    palette_arr = np.array(WAVESHARE_E6_PALETTE, dtype=np.uint8)
    lab = _srgb_to_lab(palette_arr)
    back = _lab_to_srgb(lab)
    delta = np.abs(back.astype(np.int32) - palette_arr.astype(np.int32))
    assert delta.max() <= 5
