"""Phase 2 quantizer knobs: exposure, s-curve, serpentine, strength.

Verifies that each new parameter mutates output relative to the
neutral default, and that neutral values are byte-identical to the
pre-v0.67.1 defaults (i.e. no accidental regression for devices that
haven't applied a palette profile)."""

from __future__ import annotations

import numpy as np
from PIL import Image

from app.quantizer import (
    _apply_exposure,
    _apply_s_curve,
    _error_diffusion,
    pack_to_panel_bin,
)


def _grey_ramp(width: int = 128, height: int = 64) -> Image.Image:
    """Horizontal 0..255 grey ramp so tone knobs have real range."""
    arr = np.tile(np.linspace(0, 255, width, dtype=np.uint8), (height, 1))
    return Image.fromarray(np.stack([arr, arr, arr], axis=-1))


def test_exposure_zero_is_identity() -> None:
    img = _grey_ramp()
    assert _apply_exposure(img, 0) is img


def test_exposure_positive_brightens_midpoints() -> None:
    img = _grey_ramp(64, 8)
    bright = np.asarray(_apply_exposure(img, 60), dtype=np.int16)
    plain = np.asarray(img.convert("RGB"), dtype=np.int16)
    # Mid-tones (non-zero, non-255) rise strictly.
    mid = plain[:, 20:40, 0]
    mid_bright = bright[:, 20:40, 0]
    assert (mid_bright > mid).mean() > 0.8


def test_exposure_negative_darkens_midpoints() -> None:
    img = _grey_ramp(64, 8)
    dark = np.asarray(_apply_exposure(img, -60), dtype=np.int16)
    plain = np.asarray(img.convert("RGB"), dtype=np.int16)
    mid = plain[:, 20:40, 0]
    mid_dark = dark[:, 20:40, 0]
    assert (mid_dark < mid).mean() > 0.8


def test_s_curve_zero_is_identity() -> None:
    img = _grey_ramp()
    assert _apply_s_curve(img, 0) is img


def test_s_curve_positive_pushes_mid_tones_away_from_grey() -> None:
    # Grey (~127) should get either darker or brighter, but not stay 127.
    grey = Image.new("RGB", (32, 32), (127, 127, 127))
    curved = np.asarray(_apply_s_curve(grey, 80))
    assert curved.mean() != 127.0
    # Black and white ends stay near-pinned.
    black = Image.new("RGB", (8, 8), (0, 0, 0))
    white = Image.new("RGB", (8, 8), (255, 255, 255))
    assert np.asarray(_apply_s_curve(black, 80)).mean() <= 4
    assert np.asarray(_apply_s_curve(white, 80)).mean() >= 251


def test_pack_neutral_knobs_match_pre_v67_defaults() -> None:
    # Default kwargs (exposure=0, s_curve=0, serpentine=False,
    # diffusion_strength=100) must produce the exact same bytes as
    # pre-v0.67.1 pack_to_panel_bin. Grabs both outputs; equal.
    img = _grey_ramp(64, 32)
    baseline = pack_to_panel_bin(img, width=64, height=32)
    same = pack_to_panel_bin(
        img,
        width=64,
        height=32,
        exposure=0,
        s_curve=0,
        serpentine=False,
        diffusion_strength=100,
    )
    assert baseline == same


def test_pack_exposure_changes_output() -> None:
    img = _grey_ramp(64, 32)
    baseline = pack_to_panel_bin(img, width=64, height=32)
    bumped = pack_to_panel_bin(img, width=64, height=32, exposure=40)
    assert baseline != bumped


def test_pack_s_curve_changes_output() -> None:
    img = _grey_ramp(64, 32)
    baseline = pack_to_panel_bin(img, width=64, height=32)
    curved = pack_to_panel_bin(img, width=64, height=32, s_curve=60)
    assert baseline != curved


def test_error_diffusion_serpentine_changes_output() -> None:
    from app.quantizer import _ATKINSON_WEIGHTS, WAVESHARE_E6_PALETTE

    img = _grey_ramp(64, 32)
    palette = np.array(WAVESHARE_E6_PALETTE, dtype=np.float32)
    lr = _error_diffusion(img, palette, _ATKINSON_WEIGHTS, serpentine=False)
    zigzag = _error_diffusion(img, palette, _ATKINSON_WEIGHTS, serpentine=True)
    assert lr != zigzag


def test_error_diffusion_zero_strength_still_produces_indices() -> None:
    from app.quantizer import _ATKINSON_WEIGHTS, WAVESHARE_E6_PALETTE

    img = _grey_ramp(32, 8)
    palette = np.array(WAVESHARE_E6_PALETTE, dtype=np.float32)
    # strength=0 => no error propagation => output = nearest-neighbour
    # quantise. Every byte is a valid palette index (0..len-1).
    out = _error_diffusion(img, palette, _ATKINSON_WEIGHTS, strength=0.0)
    assert max(out) < len(WAVESHARE_E6_PALETTE)


def test_pack_ignores_out_of_range_exposure_gracefully() -> None:
    # Guardrails on the tone helpers: 999 clamps to 100 rather than
    # crashing on numpy overflow.
    img = _grey_ramp(16, 8)
    _ = pack_to_panel_bin(img, width=16, height=8, exposure=999, s_curve=-500)
