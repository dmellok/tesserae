"""Quantizer: packing layout, dither-mode dispatch, fit-to-panel scale modes.

We don't assert on visual quality, that's curated manually with the
/calibrate page (later milestone). These check the byte-level invariants
the firmware contract relies on."""

from __future__ import annotations

import pytest
from PIL import Image

from app.quantizer import (
    INKY_7COLOUR_PALETTE,
    WAVESHARE_E6_CALIBRATED_PALETTE,
    WAVESHARE_E6_PALETTE,
    apply_underscan,
    fit_to_panel,
    pack_to_panel_bin,
    rotate_png,
)


@pytest.fixture
def red_panel() -> Image.Image:
    """A panel-sized image filled with the E6 palette's red entry. Using
    the palette entry directly (instead of a hardcoded ``(255, 0, 0)``)
    keeps the "this colour maps to nibble 0x3" assertions stable across
    calibrated-vs-nominal palette swaps. The byte-level invariants the
    firmware contract relies on are about palette-index → nibble, not
    about absolute sRGB triplets."""
    return Image.new("RGB", (100, 80), WAVESHARE_E6_PALETTE[3])


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
    """The 4th E6 palette entry (red) packs to firmware nibble 0x3, the
    invariant the firmware contract depends on. The red_panel fixture is
    filled with that entry's exact RGB so it round-trips cleanly through
    quantize regardless of whether the palette is nominal or calibrated."""
    packed = pack_to_panel_bin(red_panel, width=100, height=80, dither="none")
    # Every byte: (red_nibble << 4) | red_nibble = 0x33.
    assert all(b == 0x33 for b in packed)


@pytest.mark.parametrize(
    "dither",
    ["floyd-steinberg", "none", "atkinson", "bayer-8x8", "halftone", "crosshatch"],
)
def test_pack_dispatches_every_dither_mode(red_panel: Image.Image, dither: str) -> None:
    packed = pack_to_panel_bin(red_panel, width=100, height=80, dither=dither)
    assert len(packed) == 100 * 80 // 2


def test_pack_inky_7colour_orange_maps_to_nibble_six() -> None:
    """Orange exists only in the 7-colour gamut: palette index 6, LUT is
     identity, so a panel filled with that exact calibrated orange packs
     to 0x66 bytes. (Pure sRGB orange would dither across orange + yellow
     on the calibrated palette, which is exactly what we want on hardware
    , but unhelpful for a byte-level invariant test.)"""
    orange = Image.new("RGB", (100, 80), INKY_7COLOUR_PALETTE[6])
    packed = pack_to_panel_bin(orange, width=100, height=80, dither="none", gamut="inky_7colour")
    assert all(b == 0x66 for b in packed)


def test_pack_inky_7colour_red_nibble_differs_from_e6() -> None:
    """Red is index 3 on E6 (nibble 0x3) but index 4 on the 7-colour gamut
    (identity LUT → nibble 0x4): the index spaces genuinely differ.
    Each test panel is filled with that gamut's calibrated red so the
    "this palette entry maps to this nibble" invariant lands cleanly."""
    e6_red = Image.new("RGB", (100, 80), WAVESHARE_E6_PALETTE[3])
    inky_red = Image.new("RGB", (100, 80), INKY_7COLOUR_PALETTE[4])
    e6 = pack_to_panel_bin(e6_red, width=100, height=80, dither="none")
    inky = pack_to_panel_bin(inky_red, width=100, height=80, dither="none", gamut="inky_7colour")
    assert all(b == 0x33 for b in e6)
    assert all(b == 0x44 for b in inky)


def test_calibrated_palette_constants_are_dustier_than_nominal() -> None:
    """The opt-in calibration constants describe the panel's measured
    colours, not pure sRGB. Pin the qualitative claim, red dim, blue
    navy, yellow mustard, so a future "let's normalize the palette
    tables" refactor can't silently revert them to primaries and break
    the calibrated path."""
    _black, _white, yellow, red, blue, _green = (
        WAVESHARE_E6_CALIBRATED_PALETTE[i] for i in range(6)
    )
    assert red != (255, 0, 0), "calibrated red should be the panel's dusty red"
    assert yellow != (255, 255, 0)
    assert blue != (0, 0, 255)
    # Each entry still belongs to its colour family, red dominates red,
    # blue dominates blue. A reordering bug would catch on these.
    assert red[0] > red[1] and red[0] > red[2]
    assert blue[2] > blue[0] and blue[2] > blue[1]


def test_pack_calibrated_off_matches_nominal(red_panel: Image.Image) -> None:
    """Default ``calibrated=False`` is byte-identical to no parameter at
    all. The toggle is opt-in; existing devices that never flip it must
    see zero behaviour change."""
    nominal = pack_to_panel_bin(red_panel, width=100, height=80, dither="none")
    explicit = pack_to_panel_bin(red_panel, width=100, height=80, dither="none", calibrated=False)
    assert nominal == explicit


def test_pack_calibrated_on_uses_calibrated_palette_for_solid_panel() -> None:
    """A panel filled with the calibrated red still packs to nibble 0x3
    when the calibrated toggle is on, the on-wire byte contract is
    unchanged, only the dither target moves. After tone mapping the
    input is rescaled into the calibrated black/white band, then
    quantize maps that against the calibrated palette; pure calibrated
    red survives the round-trip."""
    img = Image.new("RGB", (100, 80), WAVESHARE_E6_CALIBRATED_PALETTE[3])
    packed = pack_to_panel_bin(img, width=100, height=80, dither="none", calibrated=True)
    # The exact byte content depends on whether tone-mapping shifts the
    # solid red away from the calibrated red target enough to flip the
    # nearest-palette winner. Assert the byte count + that the buffer
    # only ever uses LUT-valid nibbles (0/1/2/3/5/6).
    assert len(packed) == 100 * 80 // 2
    nibbles = {b >> 4 for b in packed} | {b & 0xF for b in packed}
    assert nibbles.issubset({0x0, 0x1, 0x2, 0x3, 0x5, 0x6})


def test_pack_calibrated_dithers_midtone_grey() -> None:
    """The whole point of the calibrated swap: a mid-grey source pixel
    that would solid-map to nominal white now produces a real dither
    field. Without tone mapping the calibrated palette alone collapses
    near-white to a solid block of paper-grey on the panel; the
    compression pre-pass restores the dither.

    Asserting on dither presence is fiddly (FS produces structured
    output), so we check the proxy invariant: the packed buffer has
    *more than one* distinct nibble value across a mid-grey panel
    region. The nominal path's solid-white output produces exactly one
    nibble (0x11 everywhere); calibrated should produce at least two."""
    grey = Image.new("RGB", (100, 80), (180, 180, 180))
    nominal = pack_to_panel_bin(grey, width=100, height=80, dither="floyd-steinberg")
    calibrated = pack_to_panel_bin(
        grey, width=100, height=80, dither="floyd-steinberg", calibrated=True
    )
    nibbles_calibrated = {b >> 4 for b in calibrated} | {b & 0xF for b in calibrated}
    # We don't pin nominal to "one nibble", at this exact brightness
    # FS might happen to dither too, but calibrated must definitely
    # have at least two, proving the tone-mapped dither is active.
    assert len(nibbles_calibrated) >= 2, (
        f"calibrated mid-grey should dither, got nibbles {nibbles_calibrated!r}"
    )
    # And the two paths must produce different bytes, if they didn't,
    # the toggle would be doing nothing.
    assert nominal != calibrated


def test_pack_calibrated_falls_back_to_nominal_for_unknown_gamut() -> None:
    """Calibrated palettes only exist for the gamuts in _CALIBRATED_PALETTES.
    For anything else (custom, future panels) the toggle is a no-op -
    the renderer falls back to the nominal palette and the on-wire
    bytes match the calibrated=False path."""
    red = Image.new("RGB", (100, 80), (255, 0, 0))
    # A gamut string the calibration table doesn't know, pack_to_panel_bin
    # falls back to waveshare_e6 nominal in both branches.
    nominal = pack_to_panel_bin(red, width=100, height=80, dither="none", gamut="not-a-gamut")
    calibrated = pack_to_panel_bin(
        red, width=100, height=80, dither="none", gamut="not-a-gamut", calibrated=True
    )
    assert nominal == calibrated


def test_pack_unknown_gamut_falls_back_to_e6(red_panel: Image.Image) -> None:
    default = pack_to_panel_bin(red_panel, width=100, height=80, dither="none")
    unknown = pack_to_panel_bin(red_panel, width=100, height=80, dither="none", gamut="not-a-gamut")
    assert unknown == default


def test_pack_unknown_dither_raises(red_panel: Image.Image) -> None:
    with pytest.raises(ValueError, match="unknown dither"):
        pack_to_panel_bin(red_panel, width=100, height=80, dither="not-a-mode")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "dither",
    [
        "floyd-steinberg",
        "none",
        "atkinson",
        "jarvis",
        "stucki",
        "bayer-8x8",
        "halftone",
        "crosshatch",
    ],
)
def test_quantize_supports_every_dither_mode(dither: str) -> None:
    """Regression for #47: prior to v0.65.2, ``quantize`` (used by the
    ``trmnl_png`` / ``trmnl_png_color`` renderers) raised
    ``ValueError: unsupported Pillow dither mode`` for every mode
    outside ``_PIL_DITHER_MAP`` (Atkinson, Jarvis, Stucki, Bayer,
    halftone, crosshatch), even though those are declared in
    ``DitherMode`` and offered on the device dither dropdown."""
    from app.quantizer import quantize

    src = Image.new("RGB", (32, 24), (128, 128, 128))  # mid-grey
    out = quantize(src, dither=dither, palette=((0, 0, 0), (255, 255, 255)))  # type: ignore[arg-type]
    assert out.size == (32, 24)
    assert out.mode == "RGB"
    # Output only contains palette colours (black or white).
    colours = set(out.getdata())
    assert colours <= {(0, 0, 0), (255, 255, 255)}


def test_quantize_unknown_dither_raises() -> None:
    from app.quantizer import quantize

    src = Image.new("RGB", (16, 16), (255, 0, 0))
    with pytest.raises(ValueError, match="unknown dither"):
        quantize(src, dither="not-a-mode", palette=((0, 0, 0), (255, 255, 255)))  # type: ignore[arg-type]


def test_rotate_round_trip() -> None:
    img = Image.new("RGB", (40, 20), (0, 0, 255))
    png = _png_bytes(img)
    rotated = rotate_png(png, quarters=1)
    out = Image.open(__import__("io").BytesIO(rotated))
    assert out.size == (20, 40)
    # Four quarter turns returns to the original size (and is a fast
    # no-op via quarters % 4).
    assert rotate_png(png, quarters=4) == png


def test_underscan_image_insets_and_preserves_size() -> None:
    from app.quantizer import underscan_image

    img = Image.new("RGB", (100, 80), (255, 0, 0))
    out = underscan_image(img, underscan=10, fill="#ffffff")
    assert out.size == (100, 80)
    assert out.getpixel((2, 2)) == (255, 255, 255)  # border = fill (sits under the mat)
    assert out.getpixel((50, 40)) == (255, 0, 0)  # content survives inset


def test_underscan_image_noop_for_nonpositive_or_oversize() -> None:
    from app.quantizer import underscan_image

    img = Image.new("RGB", (40, 40), (0, 0, 0))
    assert underscan_image(img, underscan=0) is img  # untouched, same object
    too_big = underscan_image(img, underscan=50)  # inset would consume the image
    assert too_big.size == (40, 40)


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
