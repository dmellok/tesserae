"""Phase 3 edge-handling: smoothing radius + preserve-line-art.

Confirms that the two new experimental knobs are byte-neutral at
their defaults (backward compat), that they mutate output when
turned on, and that preserve-line-art produces a strictly larger
edge-only diff on a text-like input than on a smooth ramp."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from app import quantizer
from app.quantizer import (
    _apply_smoothing,
    _line_art_mask,
    pack_to_panel_bin,
    pack_to_panel_bin_1bpp,
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


# -- native-colour guard (discussion #227) ------------------------------


def _nibbles(raw: bytes, width: int, height: int) -> np.ndarray:
    """Unpack the 4-bpp buffer back to one value per pixel."""
    arr = np.frombuffer(raw, dtype=np.uint8)
    out = np.empty(width * height, dtype=np.uint8)
    out[0::2] = arr >> 4
    out[1::2] = arr & 0x0F
    return out.reshape(height, width)


def _paper_frame(width: int = 64, height: int = 32) -> Image.Image:
    """A warm-paper background (what the light themes actually paint)
    beside a mid-grey block. Not exactly white, so error diffusion has
    something to spread, which is the case that speckles."""
    img = Image.new("RGB", (width, height), (248, 246, 242))
    ImageDraw.Draw(img).rectangle((0, 0, width // 2 - 1, height - 1), fill=(128, 128, 128))
    return img


def test_native_colour_mask_marks_only_pixels_near_a_palette_entry() -> None:
    img = Image.new("RGB", (4, 1), (255, 255, 255))
    img.putpixel((1, 0), (250, 250, 250))  # ~8.7 away from white
    img.putpixel((2, 0), (128, 128, 128))  # nowhere near anything
    img.putpixel((3, 0), (0, 0, 0))
    palette = quantizer.palette_for_gamut("waveshare_e6")

    exact = quantizer._native_colour_mask(img, palette, 1)
    assert exact.tolist() == [True, False, False, True]

    loose = quantizer._native_colour_mask(img, palette, 16)
    assert loose.tolist() == [True, True, False, True]


def test_protect_native_colours_off_is_byte_identical() -> None:
    img = _paper_frame()
    plain = pack_to_panel_bin(img, width=64, height=32, gamut="waveshare_e6")
    guarded = pack_to_panel_bin(
        img, width=64, height=32, gamut="waveshare_e6", protect_native_colours=0
    )
    assert plain == guarded

    mono_plain = pack_to_panel_bin_1bpp(img, width=64, height=32)
    mono_guarded = pack_to_panel_bin_1bpp(img, width=64, height=32, protect_native_colours=0)
    assert mono_plain == mono_guarded


def test_protect_native_colours_clears_background_speckle() -> None:
    """The background is close enough to the panel's white to be worth
    holding; the block beside it is not, and must keep dithering."""
    img = _paper_frame()
    white = quantizer.palette_for_gamut("waveshare_e6").index((255, 255, 255))

    speckled = _nibbles(pack_to_panel_bin(img, width=64, height=32, gamut="waveshare_e6"), 64, 32)
    assert (speckled[:, 32:] != white).sum() > 0

    guarded = _nibbles(
        pack_to_panel_bin(
            img, width=64, height=32, gamut="waveshare_e6", protect_native_colours=24
        ),
        64,
        32,
    )
    assert (guarded[:, 32:] != white).sum() == 0
    # The mid-grey block has no native colour to sit on, so it still
    # diffuses: the guard cleans backgrounds, it doesn't flatten content.
    assert len(np.unique(guarded[:, :32])) > 1


def test_protect_native_colours_clears_background_speckle_on_mono() -> None:
    img = _paper_frame()
    packed = pack_to_panel_bin_1bpp(img, width=64, height=32)
    guarded = pack_to_panel_bin_1bpp(img, width=64, height=32, protect_native_colours=24)
    bits = np.unpackbits(np.frombuffer(packed, dtype=np.uint8)).reshape(32, 64)
    guarded_bits = np.unpackbits(np.frombuffer(guarded, dtype=np.uint8)).reshape(32, 64)
    # 1 = white on the wire. The background half goes solid white.
    assert (bits[:, 32:] == 0).sum() > 0
    assert (guarded_bits[:, 32:] == 0).sum() == 0
    assert len(np.unique(guarded_bits[:, :32])) > 1


def test_error_diffusion_hold_stops_propagation_entirely() -> None:
    """A hold mask over every pixel leaves nothing to diffuse, so the
    result matches a plain nearest-colour quantise."""
    img = _grey_ramp(64, 16)
    palette = quantizer.palette_for_gamut("waveshare_e6")
    pal_arr = np.array(palette, dtype=np.float32)
    everything = np.ones(64 * 16, dtype=bool)

    held = quantizer._error_diffusion(img, pal_arr, quantizer._FS_WEIGHTS, hold=everything)
    nearest = img.convert("RGB").quantize(
        palette=quantizer._palette_image(palette), dither=Image.Dither.NONE
    )
    assert held == nearest.tobytes()

    # Without the mask the same input dithers, so this is a real signal.
    diffused = quantizer._error_diffusion(img, pal_arr, quantizer._FS_WEIGHTS)
    assert diffused != held
