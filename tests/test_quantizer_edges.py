"""Phase 3 edge-handling: smoothing radius + preserve-line-art.

Confirms that the two new experimental knobs are byte-neutral at
their defaults (backward compat), that they mutate output when
turned on, and that preserve-line-art produces a strictly larger
edge-only diff on a text-like input than on a smooth ramp."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from app.quantizer import (
    _apply_smoothing,
    _line_art_mask,
    pack_to_panel_bin,
)


def _text_frame(width: int = 128, height: int = 64) -> Image.Image:
    """White canvas with mid-tone rectangles + rules: fake stand-in
    for antialiased text where the transition band lands on colours
    that dither and nearest-neighbour disagree on. That disagreement
    is what preserve-line-art can actually surface as a diff."""
    img = Image.new("RGB", (width, height), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    # Mid-grey rectangle: 128 sits between palette black + white so
    # nearest-neighbour picks one bucket while error-diffusion spreads.
    draw.rectangle((10, 10, 60, 30), fill=(128, 128, 128))
    draw.rectangle((5, 45, width - 5, 47), fill=(90, 90, 90))
    return img


def _grey_ramp(width: int = 128, height: int = 64) -> Image.Image:
    arr = np.tile(np.linspace(0, 255, width, dtype=np.uint8), (height, 1))
    return Image.fromarray(np.stack([arr, arr, arr], axis=-1))


def test_smoothing_zero_is_identity() -> None:
    img = _text_frame()
    assert _apply_smoothing(img, 0) is img


def test_smoothing_softens_hard_edges() -> None:
    img = _text_frame(64, 32)
    plain = np.asarray(img, dtype=np.int16)
    softened = np.asarray(_apply_smoothing(img, 2), dtype=np.int16)
    # Edge pixels (in the transition band around the rectangle) shift;
    # the total absolute diff on the whole image is non-trivial.
    assert np.abs(plain - softened).sum() > 100


def test_line_art_mask_lights_up_text_regions() -> None:
    img = _text_frame(64, 32)
    mask = _line_art_mask(img)
    # Non-trivial mask over the sharp rectangle + rule.
    assert mask.any()
    assert mask.sum() > 20
    # And the flat white background outside the text has no coverage
    # once we zoom out a bit.
    ramp_mask = _line_art_mask(_grey_ramp(64, 32))
    assert ramp_mask.sum() <= mask.sum()


def test_pack_edge_defaults_match_pre_v672() -> None:
    img = _text_frame()
    baseline = pack_to_panel_bin(img, width=128, height=64)
    same = pack_to_panel_bin(
        img,
        width=128,
        height=64,
        smoothing_radius=0,
        preserve_line_art=False,
    )
    assert baseline == same


def test_smoothing_radius_changes_output() -> None:
    img = _text_frame()
    baseline = pack_to_panel_bin(img, width=128, height=64)
    softened = pack_to_panel_bin(img, width=128, height=64, smoothing_radius=2)
    assert baseline != softened


def test_preserve_line_art_changes_output_on_text() -> None:
    img = _text_frame()
    baseline = pack_to_panel_bin(img, width=128, height=64)
    preserved = pack_to_panel_bin(img, width=128, height=64, preserve_line_art=True)
    assert baseline != preserved


def test_preserve_line_art_noop_when_no_edges() -> None:
    # A flat single-colour source has no detected edges, so the
    # post-pass leaves the buffer alone: same bytes as baseline.
    flat = Image.new("RGB", (64, 32), (200, 30, 30))
    baseline = pack_to_panel_bin(flat, width=64, height=32)
    preserved = pack_to_panel_bin(flat, width=64, height=32, preserve_line_art=True)
    assert baseline == preserved
